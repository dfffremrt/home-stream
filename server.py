import os

PORT = int(os.environ.get('PORT', 8020))
VIDEOS_DIR = os.path.expanduser("~/Видео блять")
VIDEO_EXT = ('.mp4', '.webm', '.mkv', '.mov', '.m4v', '.ogv')
MAX_UPLOAD = 20 * 1024 * 1024 * 1024
import http.server
import socketserver
import os
import json
import urllib.parse
import socket
import re


def safe_filename(name):
    """Убираем опасные символы, оставляем буквы/цифры/точки/пробелы/дефисы/скобки."""
    name = os.path.basename(name)
    name = re.sub(r'[^\w\s\.\-\(\)\[\]]+', '_', name, flags=re.UNICODE)
    return name.strip() or f"video_{int(time.time())}.mp4"


def unique_path(folder, name):
    """Если файл с таким именем уже есть — добавляем (1), (2) и т.д."""
    base, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return candidate


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # --- Список видео ---
        if path == '/api/videos':
            try:
                files = sorted(os.listdir(VIDEOS_DIR))
            except FileNotFoundError:
                self.send_error(404, "Папка не найдена")
                return

            videos = []
            for f in files:
                if f.lower().endswith(VIDEO_EXT):
                    full = os.path.join(VIDEOS_DIR, f)
                    try:
                        size = os.path.getsize(full)
                        mtime = os.path.getmtime(full)
                    except OSError:
                        size, mtime = 0, 0
                    videos.append({
                        'title': os.path.splitext(f)[0],
                        'url': '/video/' + urllib.parse.quote(f),
                        'size': size,
                        'mtime': mtime,
                    })

            body = json.dumps(videos, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # --- Стриминг видео ---
        if path.startswith('/video/'):
            self.serve_video(path[len('/video/'):])
            return

        super().do_GET()

    def serve_video(self, encoded_name):
        filename = urllib.parse.unquote(encoded_name)
        filepath = os.path.join(VIDEOS_DIR, filename)

        if not os.path.isfile(filepath):
            self.send_error(404, "Файл не найден")
            return

        file_size = os.path.getsize(filepath)
        range_header = self.headers.get('Range')

        lower = filename.lower()
        if lower.endswith('.webm'):
            mime = 'video/webm'
        elif lower.endswith('.mkv'):
            mime = 'video/x-matroska'
        elif lower.endswith('.mov'):
            mime = 'video/quicktime'
        else:
            mime = 'video/mp4'

        if range_header:
            range_val = range_header.replace('bytes=', '')
            parts = range_val.split('-')
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            length = end - start + 1

            self.send_response(206)
            self.send_header('Content-Type', mime)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
            self.send_header('Content-Length', str(length))
            self.end_headers()

            with open(filepath, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        else:
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(file_size))
            self.end_headers()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    # --- ЗАГРУЗКА ВИДЕО ---
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/upload':
            self.send_error(404)
            return

        ctype = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in ctype:
            self.send_error(400, "Ожидается multipart/form-data")
            return

        # Парсим multipart
        boundary = ctype.split('boundary=')[1].encode()
        length = int(self.headers.get('Content-Length', 0))

        if length > MAX_UPLOAD:
            self.send_error(413, "Файл слишком большой")
            return

        os.makedirs(VIDEOS_DIR, exist_ok=True)

        # Разбираем вручную, чтобы не грузить всё в память
        remaining = length
        delimiter = b'--' + boundary

        # читаем до первого boundary
        line = self.rfile.readline()
        remaining -= len(line)

        saved_path = None

        while remaining > 0:
            # line — это boundary
            if not line.startswith(delimiter):
                break

            # читаем заголовки части
            headers = {}
            while True:
                h = self.rfile.readline()
                remaining -= len(h)
                if h in (b'\r\n', b'\n', b''):
                    break
                k, _, v = h.decode('utf-8', 'replace').partition(':')
                headers[k.strip().lower()] = v.strip()

            disposition = headers.get('content-disposition', '')
            # ищем filename
            fname_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', disposition, re.IGNORECASE)

            part_length = None
            # читаем тело части до следующего boundary
            # проще: буферизуем и ищем boundary в потоке
            if fname_match:
                raw_name = urllib.parse.unquote(fname_match.group(1))
                fname = safe_filename(raw_name)
                dest = unique_path(VIDEOS_DIR, fname)
                out = open(dest, 'wb')
                saved_path = dest
            else:
                out = None  # поле без файла — пропускаем

            # читаем тело до boundary
            prev = b''
            buf = b''
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                buf += chunk
                # ищем boundary
                idx = buf.find(delimiter)
                if idx != -1:
                    # всё до idx — тело
                    body = buf[:idx]
                    # убираем \r\n перед boundary
                    if body.endswith(b'\r\n'):
                        body = body[:-2]
                    if out:
                        out.write(body)
                    # остаток — это следующая boundary-строка, но нам нужно дочитать до \r\n
                    rest = buf[idx:]
                    nl = rest.find(b'\r\n')
                    if nl != -1:
                        line = rest[nl+2:] if nl+2 < len(rest) else b''
                    else:
                        line = b''
                    break
                else:
                    # ещё не встретили boundary — сбрасываем всё кроме хвоста
                    keep = len(delimiter) + 4
                    if len(buf) > keep:
                        flush = buf[:-keep]
                        if out:
                            out.write(flush)
                        buf = buf[-keep:]
            else:
                # закончились данные
                if out and buf:
                    if buf.endswith(b'\r\n'):
                        buf = buf[:-2]
                    out.write(buf)

            if out:
                out.close()

            if remaining <= 0:
                break

        # Ответ
        if saved_path:
            body = json.dumps({
                'ok': True,
                'name': os.path.basename(saved_path),
                'title': os.path.splitext(os.path.basename(saved_path))[0],
                'url': '/video/' + urllib.parse.quote(os.path.basename(saved_path)),
            }, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
        else:
            body = json.dumps({'ok': False, 'error': 'Файл не получен'}).encode('utf-8')
            self.send_response(400)

        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ip = get_local_ip()

    print()
    print('🎬 Home Stream запущен!')
    print()
    print(f'  Локально:   http://localhost:{PORT}')
    print(f'  По сети:    http://{ip}:{PORT}')
    print(f'  Папка:      {VIDEOS_DIR}')
    print()
    print('  Ctrl+C — остановить')
    print()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nОстановлено.')

