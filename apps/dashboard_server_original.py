#!/usr/bin/env python3
"""
Локальный сервер для домашней панели.
- Статика из ./static/
- API: /api/time, /api/weather, /api/currency, /api/notes, /api/chat
- Кэш: если внешний API недоступен, отдаём последний успешный ответ
"""
import http.server
import json
import os
import subprocess
import email.utils
import time
from datetime import timezone
from urllib.parse import urlparse

PORT = 8080
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, 'static')
DATA = os.path.join(ROOT, 'data')
NOTES_FILE = os.path.join(DATA, 'notes.json')
CACHE_FILE = os.path.join(DATA, 'cache.json')
CHAT_FILE = os.path.join(DATA, 'chat.json')

CLEAN_ENV = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}
CURL = '/usr/bin/curl'

WEATHER_URL = ('https://api.met.no/weatherapi/locationforecast/2.0/compact'
               '?lat=55.7558&lon=37.6173')

CURRENCY_SOURCES = [
    ('er-api', 'https://open.er-api.com/v6/latest/RUB'),
    ('cbr',    'https://www.cbr-xml-daily.ru/daily_json.js'),
]

MAX_CHAT_MESSAGES = 200


def curl(url, headers_only=False, timeout=10):
    if headers_only:
        cmd = [CURL, '-s', '--max-time', str(timeout), '-L',
               '-A', 'Mozilla/5.0', '-D', '-', '-o', '/dev/null', url]
    else:
        cmd = [CURL, '-s', '--max-time', str(timeout), '-L',
               '-A', 'dashboard-local/1.0', url]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5, env=CLEAN_ENV)
    if r.returncode != 0:
        raise RuntimeError(f'curl exit {r.returncode}')
    return r.stdout


# ---------- КЭШ ----------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    os.makedirs(DATA, exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)


def cached(key, fetcher):
    try:
        result = fetcher()
        cache = load_cache()
        cache[key] = {'data': result, 'ts': int(time.time())}
        save_cache(cache)
        return result, False
    except Exception as e:
        print(f'  ⚠️ {key} не ответил: {e}')
        cache = load_cache()
        if key in cache:
            print(f'  📦 отдаём из кэша (возраст {int(time.time()) - cache[key]["ts"]} сек)')
            return cache[key]['data'], True
        raise


# ---------- ВНЕШНИЕ API ----------
def fetch_time():
    raw = curl('https://www.cbr-xml-daily.ru/daily_json.js',
               headers_only=True, timeout=10).decode(errors='replace')
    date_str = None
    for line in raw.splitlines():
        if line.lower().startswith('date:'):
            date_str = line.split(':', 1)[1].strip()
            break
    if not date_str:
        raise RuntimeError('Не нашли Date')
    dt = email.utils.parsedate_to_datetime(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {'datetime': dt.isoformat(), 'unixtime': int(dt.timestamp()), 'timezone': 'UTC'}


def fetch_weather():
    data = json.loads(curl(WEATHER_URL, timeout=12))
    ts = data['properties']['timeseries']
    now = ts[0]
    d = now['data']['instant']['details']
    symbol = now['data'].get('next_1_hours', {}).get('summary', {}).get('symbol_code', 'cloudy')
    temps = []
    for entry in ts[:24]:
        try:
            temps.append(entry['data']['instant']['details']['air_temperature'])
        except KeyError:
            pass
    return {
        'current': {
            'temperature_2m': d['air_temperature'],
            'relative_humidity_2m': d['relative_humidity'],
            'wind_speed_10m': round(d['wind_speed'] * 3.6, 1),
            'weather_code': symbol_to_wmo(symbol),
        },
        'daily': {
            'temperature_2m_max': [max(temps)] if temps else [d['air_temperature']],
            'temperature_2m_min': [min(temps)] if temps else [d['air_temperature']],
        },
    }


def fetch_currency():
    last_err = None
    for name, url in CURRENCY_SOURCES:
        try:
            print(f'    → курсы: {name}')
            data = json.loads(curl(url, timeout=10))
            if name == 'er-api':
                rates = data.get('rates', {})
                valute = {}
                for code in ('USD', 'EUR', 'CNY', 'KZT'):
                    if code in rates and rates[code] > 0:
                        valute[code] = {'Value': 1.0 / rates[code]}
                if valute:
                    return {'Valute': valute, 'source': name}
            else:
                valute = {}
                for code in ('USD', 'EUR', 'CNY', 'KZT'):
                    if code in data.get('Valute', {}):
                        valute[code] = {'Value': data['Valute'][code]['Value']}
                if valute:
                    return {'Valute': valute, 'source': name}
        except Exception as e:
            last_err = e
            print(f'    ❌ {name}: {e}')
            continue
    raise RuntimeError(f'Все источники курсов упали: {last_err}')


def symbol_to_wmo(symbol):
    s = symbol.lower()
    if 'clearsky' in s: return 0
    if 'fair' in s: return 1
    if 'partlycloudy' in s: return 2
    if 'cloudy' in s: return 3
    if 'fog' in s: return 45
    if 'lightrain' in s or 'drizzle' in s: return 61
    if 'rain' in s: return 63
    if 'heavyrain' in s: return 65
    if 'lightsnow' in s or 'sleet' in s: return 71
    if 'snow' in s: return 73
    if 'heavysnow' in s: return 75
    if 'thunder' in s: return 95
    return 3


# ---------- ЗАМЕТКИ ----------
def load_notes():
    if not os.path.exists(NOTES_FILE):
        return {'text': ''}
    try:
        with open(NOTES_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'text': ''}


def save_notes(text):
    os.makedirs(DATA, exist_ok=True)
    with open(NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump({'text': text, 'updated': int(time.time())}, f, ensure_ascii=False)


# ---------- ЧАТ ----------
def load_chat():
    if not os.path.exists(CHAT_FILE):
        return []
    try:
        with open(CHAT_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_chat(messages):
    os.makedirs(DATA, exist_ok=True)
    if len(messages) > MAX_CHAT_MESSAGES:
        messages = messages[-MAX_CHAT_MESSAGES:]
    with open(CHAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False)


def add_chat_message(name, text):
    text = text.strip()
    if not text:
        return None
    messages = load_chat()
    msg = {
        'id': int(time.time() * 1000),
        'name': (name or 'Аноним')[:32],
        'text': text[:2000],
        'ts': int(time.time()),
    }
    messages.append(msg)
    save_chat(messages)
    return msg


def clear_chat():
    save_chat([])
    return True


# ---------- HTTP ----------
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC, **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/time':
            return self.api('time', fetch_time)
        if path == '/api/weather':
            return self.api('weather', fetch_weather)
        if path == '/api/currency':
            return self.api('currency', fetch_currency)
        if path == '/api/notes':
            return self.send_json(load_notes())
        if path == '/api/chat':
            return self.send_json({'messages': load_chat()})

        if path == '/':
            self.path = '/index.html'
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            self.send_error(400, 'Invalid JSON')
            return

        if path == '/api/notes':
            save_notes(data.get('text', ''))
            return self.send_json({'ok': True})

        if path == '/api/chat':
            msg = add_chat_message(data.get('name', ''), data.get('text', ''))
            return self.send_json({'ok': True, 'message': msg})

        if path == '/api/chat/clear':
            clear_chat()
            return self.send_json({'ok': True})

        self.send_error(404)

    def api(self, key, fetcher):
        try:
            data, from_cache = cached(key, fetcher)
            if from_cache:
                data = dict(data)
                data['_cached'] = True
            self.send_json(data)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and '/api/' in str(args[0]):
            print(f'  → {args[0]}')


if __name__ == '__main__':
    os.makedirs(DATA, exist_ok=True)
    http.server.HTTPServer.allow_reuse_address = True
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'✅ Сервер: http://localhost:{PORT}')
    print(f'   С телефона: http://<IP>:{PORT}')
    print(f'   Заметки: {NOTES_FILE}')
    print(f'   Чат: {CHAT_FILE}\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n⏹ Остановлен')
