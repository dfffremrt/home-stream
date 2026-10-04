#!/usr/bin/env python3
"""Единый портал: Stream + Dashboard + Game | порт 8020"""
import http.server
import json
import os
import email.utils
import time
import re
import urllib.parse
import socket
import subprocess
import secrets
from datetime import timezone
from urllib.parse import urlparse

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

PORT = int(os.environ.get('PORT', 8020))
ROOT = os.path.dirname(os.path.abspath(__file__))
APPS = os.path.join(ROOT, 'apps')
DATA = os.path.join(ROOT, 'data')
VIDEOS_DIR = os.path.expanduser("~/Видео блять")
VIDEO_EXT = ('.mp4', '.webm', '.mkv', '.mov', '.m4v', '.ogv', '.avi')
PORTAL_FILE = os.path.join(DATA, 'portal.json')
NOTES_FILE = os.path.join(DATA, 'notes.json')
CACHE_FILE = os.path.join(DATA, 'cache.json')
CHAT_FILE = os.path.join(DATA, 'chat.json')
ADMIN_PASSWORD = "q1w2e3"
SESSIONS = {}
SESSION_TTL = 24 * 3600
WEATHER_URL = 'https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=55.7558&lon=37.6173'
CURRENCY_SOURCES = [
    ('er-api', 'https://open.er-api.com/v6/latest/RUB'),
    ('cbr', 'https://www.cbr-xml-daily.ru/daily_json.js'),
]
MAX_CHAT = 200


def http_get(url, headers_only=False, timeout=10):
    if HAS_REQUESTS:
        r = requests.get(url, timeout=timeout, headers={'User-Agent': 'portal/1.0'})
        r.raise_for_status()
        if headers_only:
            return r.headers.get('Date', '')
        return r.text
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}
    if headers_only:
        cmd = ['/usr/bin/curl', '-s', '--max-time', str(timeout), '-L', '-A', 'Mozilla/5.0', '-D', '-', '-o', '/dev/null', url]
    else:
        cmd = ['/usr/bin/curl', '-s', '--max-time', str(timeout), '-L', '-A', 'portal/1.0', url]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5, env=env)
    if r.returncode != 0:
        raise RuntimeError(f'curl exit {r.returncode}')
    if headers_only:
        for line in r.stdout.decode(errors='replace').splitlines():
            if line.lower().startswith('date:'):
                return line.split(':', 1)[1].strip()
        raise RuntimeError('No Date')
    return r.stdout.decode(errors='replace')


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def is_auth(handler):
    cookie = handler.headers.get('Cookie', '')
    m = re.search(r'admin_token=([a-f0-9]+)', cookie)
    if not m:
        return False
    token = m.group(1)
    expiry = SESSIONS.get(token)
    if not expiry or expiry < time.time():
        SESSIONS.pop(token, None)
        return False
    return True


def fetch_time():
    date_str = http_get('https://www.cbr-xml-daily.ru/daily_json.js', headers_only=True, timeout=10)
    dt = email.utils.parsedate_to_datetime(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {'datetime': dt.isoformat(), 'unixtime': int(dt.timestamp()), 'timezone': 'UTC'}


def symbol_to_wmo(s):
    s = s.lower()
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


def fetch_weather():
    data = json.loads(http_get(WEATHER_URL, timeout=12))
    ts = data['properties']['timeseries']
    now = ts[0]
    d = now['data']['instant']['details']
    symbol = now['data'].get('next_1_hours', {}).get('summary', {}).get('symbol_code', 'cloudy')
    temps = []
    for e in ts[:24]:
        try:
            temps.append(e['data']['instant']['details']['air_temperature'])
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
    last = None
    for name, url in CURRENCY_SOURCES:
        try:
            data = json.loads(http_get(url, timeout=10))
            if name == 'er-api':
                rates = data.get('rates', {})
                v = {}
                for c in ('USD', 'EUR', 'CNY', 'KZT'):
                    if c in rates and rates[c] > 0:
                        v[c] = {'Value': 1.0 / rates[c]}
                if v: return {'Valute': v, 'source': name}
            else:
                v = {}
                for c in ('USD', 'EUR', 'CNY', 'KZT'):
                    if c in data.get('Valute', {}):
                        v[c] = {'Value': data['Valute'][c]['Value']}
                if v: return {'Valute': v, 'source': name}
        except Exception as e:
            last = e
    raise RuntimeError(f'All sources failed: {last}')


def cached(key, fetcher):
    try:
        result = fetcher()
        cache = load_json(CACHE_FILE, {})
        cache[key] = {'data': result, 'ts': int(time.time())}
        save_json(CACHE_FILE, cache)
        return result, False
    except Exception as e:
        print(f'  {key}: {e}')
        cache = load_json(CACHE_FILE, {})
        if key in cache:
            return cache[key]['data'], True
        raise


def safe_filename(name):
    name = os.path.basename(name)
    name = re.sub(r'[^\w\s\.\-\(\)\[\]]+', '_', name, flags=re.UNICODE)
    return name.strip() or f"file_{int(time.time())}"


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    p = os.path.join(folder, name)
    i = 1
    while os.path.exists(p):
        p = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return p


class Handler(http.server.SimpleHTTPRequestHandler):

    def end_headers(self):
        self.send_header('ngrok-skip-browser-warning', 'true')
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and ('/api/' in str(args[0]) or '404' in str(args[1])):
            print(f'  → {args[0]}')

    def do_GET(self):
        path = urllib.parse.unquote(urlparse(self.path).path)

        if path == '/api/portal':
            return self.send_json(load_json(PORTAL_FILE, {'tiles': []}))
        if path == '/api/time':
            return self.api('time', fetch_time)
        if path == '/api/weather':
            return self.api('weather', fetch_weather)
        if path == '/api/currency':
            return self.api('currency', fetch_currency)
        if path == '/api/notes':
            return self.send_json(load_json(NOTES_FILE, {'text': ''}))
        if path == '/api/chat':
            return self.send_json({'messages': load_json(CHAT_FILE, [])})
        if path == '/api/videos':
            return self.send_video_list()
        if path == '/api/admin/check':
            return self.send_json({'ok': is_auth(self)})

        if path in ('/admin', '/admin/'):
            return self.serve_static(ROOT + '/apps/stream/admin.html')

        for prefix, folder in [('/dashboard', 'dashboard'), ('/stream', 'stream'), ('/game', 'game')]:
            if path == prefix or path == prefix + '/':
                return self.serve_static(os.path.join(APPS, folder, 'index.html'))

        if path in ('/', '/index.html'):
            return self.serve_static(ROOT + '/index.html')

        for prefix, folder in [('/dashboard/', 'dashboard'), ('/stream/', 'stream'), ('/game/', 'game')]:
            if path.startswith(prefix):
                rel = path[len(prefix):]
                if not rel: rel = 'index.html'
                full = os.path.join(APPS, folder, rel)
                if os.path.isfile(full):
                    return self.serve_static(full)
                return self.send_error(404)

        filename = path.lstrip('/')
        if filename:
            full = os.path.join(VIDEOS_DIR, filename)
            if os.path.isfile(full):
                return self.serve_video(full, filename)

        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''

        if path == '/api/login':
            try:
                data = json.loads(body.decode('utf-8'))
            except Exception:
                return self.send_json({'ok': False, 'error': 'Bad JSON'}, 400)
            if data.get('password') != ADMIN_PASSWORD:
                time.sleep(0.5)
                return self.send_json({'ok': False, 'error': 'Wrong password'}, 401)
            token = secrets.token_hex(32)
            SESSIONS[token] = time.time() + SESSION_TTL
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', f'admin_token={token}; Path=/; Max-Age={SESSION_TTL}; SameSite=Lax; HttpOnly')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode())
            return

        if path == '/api/logout':
            cookie = self.headers.get('Cookie', '')
            m = re.search(r'admin_token=([a-f0-9]+)', cookie)
            if m: SESSIONS.pop(m.group(1), None)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', 'admin_token=; Path=/; Max-Age=0')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode())
            return

        if path == '/api/notes':
            try:
                data = json.loads(body.decode('utf-8'))
                save_json(NOTES_FILE, {'text': data.get('text', ''), 'updated': int(time.time())})
                return self.send_json({'ok': True})
            except Exception as e:
                return self.send_json({'ok': False, 'error': str(e)}, 400)

        if path == '/api/chat':
            try:
                data = json.loads(body.decode('utf-8'))
                text = data.get('text', '').strip()
                if not text:
                    return self.send_json({'ok': False, 'error': 'empty'}, 400)
                msgs = load_json(CHAT_FILE, [])
                msg = {'id': int(time.time()*1000), 'name': (data.get('name') or 'Anon')[:32], 'text': text[:2000], 'ts': int(time.time())}
                msgs.append(msg)
                if len(msgs) > MAX_CHAT: msgs = msgs[-MAX_CHAT:]
                save_json(CHAT_FILE, msgs)
                return self.send_json({'ok': True, 'message': msg})
            except Exception as e:
                return self.send_json({'ok': False, 'error': str(e)}, 400)

        if path == '/api/chat/clear':
            save_json(CHAT_FILE, [])
            return self.send_json({'ok': True})

        if path == '/api/portal/add-tile':
            if not is_auth(self):
                return self.send_json({'ok': False, 'error': 'Not authorized'}, 401)
            try:
                data = json.loads(body.decode('utf-8'))
                portal = load_json(PORTAL_FILE, {'tiles': []})
                portal['tiles'].append({
                    'name': data.get('name', 'No name'),
                    'url': data.get('url', '/'),
                    'icon': data.get('icon', '📄'),
                    'color': data.get('color', '#4a7aff'),
                    'desc': data.get('desc', ''),
                })
                save_json(PORTAL_FILE, portal)
                return self.send_json({'ok': True})
            except Exception as e:
                return self.send_json({'ok': False, 'error': str(e)}, 400)

        if path == '/api/portal/delete-tile':
            if not is_auth(self):
                return self.send_json({'ok': False, 'error': 'Not authorized'}, 401)
            try:
                data = json.loads(body.decode('utf-8'))
                idx = int(data.get('index', -1))
                portal = load_json(PORTAL_FILE, {'tiles': []})
                if 0 <= idx < len(portal['tiles']):
                    portal['tiles'].pop(idx)
                    save_json(PORTAL_FILE, portal)
                    return self.send_json({'ok': True})
                return self.send_json({'ok': False, 'error': 'Bad index'}, 400)
            except Exception as e:
                return self.send_json({'ok': False, 'error': str(e)}, 400)

        if path == '/api/admin/delete':
            if not is_auth(self):
                return self.send_json({'ok': False, 'error': 'Not authorized'}, 401)
            try:
                data = json.loads(body.decode('utf-8'))
                filename = os.path.basename(data.get('file', ''))
                full = os.path.join(VIDEOS_DIR, filename)
                if os.path.isfile(full):
                    os.remove(full)
                    return self.send_json({'ok': True, 'deleted': filename})
                return self.send_json({'ok': False, 'error': 'Not found'}, 404)
            except Exception as e:
                return self.send_json({'ok': False, 'error': str(e)}, 400)

        if path == '/upload':
            return self.handle_upload()

        self.send_error(404)

    def api(self, key, fetcher):
        try:
            data, from_cache = cached(key, fetcher)
            if from_cache:
                data = dict(data); data['_cached'] = True
            self.send_json(data)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        if not os.path.isfile(path):
            return self.send_error(404)
        ext = os.path.splitext(path)[1].lower()
        mimes = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                 '.js': 'application/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
                 '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.ico': 'image/x-icon'}
        mime = mimes.get(ext, 'application/octet-stream')
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_video_list(self):
        if not os.path.isdir(VIDEOS_DIR):
            return self.send_json([])
        try:
            files = sorted(os.listdir(VIDEOS_DIR), key=lambda x: x.lower())
        except OSError:
            return self.send_json([])
        videos = []
        for f in files:
            if f.lower().endswith(VIDEO_EXT):
                full = os.path.join(VIDEOS_DIR, f)
                if os.path.isfile(full):
                    try: size = os.path.getsize(full)
                    except OSError: size = 0
                    videos.append({'title': os.path.splitext(f)[0], 'url': '/' + urllib.parse.quote(f), 'size': size})
        self.send_json(videos)

    def serve_video(self, filepath, filename):
        lower = filename.lower()
        size = os.path.getsize(filepath)
        mime_map = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
                    '.mov': 'video/quicktime', '.m4v': 'video/x-m4v', '.ogv': 'video/ogg', '.avi': 'video/x-msvideo'}
        mime = mime_map.get(os.path.splitext(lower)[1], 'application/octet-stream')
        range_header = self.headers.get('Range')
        if range_header:
            rng = range_header.replace('bytes=', '')
            parts = rng.split('-')
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
            length = end - start + 1
            self.send_response(206)
            self.send_header('Content-Type', mime)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Content-Length', str(length))
            self.end_headers()
            with open(filepath, 'rb') as f:
                f.seek(start)
                rem = length
                while rem > 0:
                    chunk = f.read(min(64*1024, rem))
                    if not chunk: break
                    self.wfile.write(chunk)
                    rem -= len(chunk)
        else:
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(size))
            self.end_headers()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(64*1024)
                    if not chunk: break
                    self.wfile.write(chunk)

    def handle_upload(self):
        ctype = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in ctype:
            return self.send_json({'ok': False, 'error': 'Bad content-type'}, 400)
        boundary = ctype.split('boundary=')[1].encode()
        length = int(self.headers.get('Content-Length', 0))
        os.makedirs(VIDEOS_DIR, exist_ok=True)
        rem = length
        delimiter = b'--' + boundary
        line = self.rfile.readline()
        rem -= len(line)
        saved_path = None
        while rem > 0:
            if not line.startswith(delimiter): break
            headers = {}
            while True:
                h = self.rfile.readline()
                rem -= len(h)
                if h in (b'\r\n', b'\n', b''): break
                k, _, v = h.decode('utf-8', 'replace').partition(':')
                headers[k.strip().lower()] = v.strip()
            disposition = headers.get('content-disposition', '')
            fm = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', disposition, re.IGNORECASE)
            if fm:
                raw = urllib.parse.unquote(fm.group(1))
                fname = safe_filename(raw)
                dest = unique_path(VIDEOS_DIR, fname)
                out = open(dest, 'wb')
                saved_path = dest
            else:
                out = None
            buf = b''
            while rem > 0:
                chunk = self.rfile.read(min(64*1024, rem))
                if not chunk: break
                rem -= len(chunk)
                buf += chunk
                idx = buf.find(delimiter)
                if idx != -1:
                    body = buf[:idx]
                    if body.endswith(b'\r\n'): body = body[:-2]
                    if out: out.write(body)
                    rest = buf[idx:]
                    nl = rest.find(b'\r\n')
                    line = rest[nl+2:] if nl != -1 and nl+2 < len(rest) else b''
                    break
                else:
                    keep = len(delimiter) + 4
                    if len(buf) > keep:
                        flush = buf[:-keep]
                        if out: out.write(flush)
                        buf = buf[-keep:]
            else:
                if out and buf:
                    if buf.endswith(b'\r\n'): buf = buf[:-2]
                    out.write(buf)
            if out: out.close()
            if rem <= 0: break
        if saved_path:
            name = os.path.basename(saved_path)
            return self.send_json({'ok': True, 'name': name, 'url': '/' + urllib.parse.quote(name)})
        return self.send_json({'ok': False, 'error': 'No file'}, 400)


if __name__ == '__main__':
    os.chdir(ROOT)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(VIDEOS_DIR, exist_ok=True)

    if not os.path.exists(PORTAL_FILE):
        save_json(PORTAL_FILE, {'tiles': [
            {'name': 'Home Stream', 'url': '/stream/', 'icon': '🎬', 'color': '#4a7aff', 'desc': 'Домашний стриминг'},
            {'name': 'Dashboard', 'url': '/dashboard/', 'icon': '🚀', 'color': '#ff8800', 'desc': 'Панель управления'},
            {'name': 'CS:BATTLE 3D', 'url': '/game/', 'icon': '🎮', 'color': '#8b5cf6', 'desc': 'Team Match'},
        ]})

    ip = get_local_ip()
    print()
    print('🌐 Мой портал запущен!')
    print()
    print(f'  Главная:    http://localhost:{PORT}')
    print(f'  По сети:    http://{ip}:{PORT}')
    print(f'  Дашборд:    http://localhost:{PORT}/dashboard/')
    print(f'  Стриминг:   http://localhost:{PORT}/stream/')
    print(f'  Игра:       http://localhost:{PORT}/game/')
    print(f'  Админка:    http://localhost:{PORT}/admin')
    print()
    print('  Ctrl+C - остановить')
    print()

    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nОстановлено.')
