#!/usr/bin/env python3
"""
Home Stream Server — с поддержкой .hmp5
"""

import http.server
import socketserver
import os
import json
import urllib.parse
import socket
import re
import time

# ==================== НАСТРОЙКИ ====================
PORT = int(os.environ.get('PORT', 8020))
VIDEOS_DIR = os.path.expanduser("~/Видео блять")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Форматы
VIDEO_EXT = ('.mp4', '.webm', '.mkv', '.mov', '.m4v', '.ogv', '.avi')
HMP5_EXT = ('.hmp5',)
AUDIO_EXT = ('.mp3', '.wav', '.m4a', '.aac', '.ogg')

MAX_UPLOAD = 20 * 1024 * 1024 * 1024  # 20 ГБ
# ===================================================


def safe_filename(name):
    name = os.path.basename(name)
    name = re.sub(r'[^\w\s\.\-\(\)\[\]]+', '_', name, flags=re.UNICODE)
    return name.strip() or f"file_{int(time.time())}"


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return candidate


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


class Handler(http.server.SimpleHTTPRequestHandler):

    def end_headers(self):
        # Убираем заглушку ngrok
        self.send_header('ngrok-skip-browser-warning', 'true')
        super().end_headers()

    def log_message(self, format, *args):
        # Раскомментируй если хочешь видеть запросы:
        super().log_message(format, *args)

    # ==================== GET ====================
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        # --- API: список видео ---
        if path == '/api/videos':
            return self.send_video_list()

        # --- Плеер hmp4.html ---
        if path in ('/hmp4.html', '/hmp4'):
            return self.serve_player()

        # --- index.html (каталог) ---
        if path in ('/', '/index.html'):
            return self.serve_index()

        # --- Файлы из VIDEOS_DIR ---
        filename = path.lstrip('/')
        if filename:
            full = os.path.join(VIDEOS_DIR, filename)
            if os.path.isfile(full):
                return self.serve_file(full, filename)

        # --- Всё остальное (статика проекта: css, js) ---
        return super().do_GET()

    # ==================== Список видео ====================
    def send_video_list(self):
        if not os.path.isdir(VIDEOS_DIR):
            return self._json([])

        try:
            files = sorted(os.listdir(VIDEOS_DIR), key=lambda x: x.lower())
        except OSError:
            return self._json([])

        videos = []
        for f in files:
            lower = f.lower()
            full = os.path.join(VIDEOS_DIR, f)
            if not os.path.isfile(full):
                continue

            try:
                size = os.path.getsize(full)
                mtime = os.path.getmtime(full)
            except OSError:
                size, mtime = 0, 0

            if lower.endswith(HMP5_EXT):
                videos.append({
                    'title': os.path.splitext(f)[0],
                    'type': 'hmp5',
                    'url': '/' + urllib.parse.quote(f),
                    'size': size,
                    'mtime': mtime,
                })
            elif lower.endswith(VIDEO_EXT):
                videos.append({
                    'title': os.path.splitext(f)[0],
                    'type': 'video',
                    'url': '/' + urllib.parse.quote(f),
                    'size': size,
                    'mtime': mtime,
                })

        return self._json(videos)

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    # ==================== index.html ====================
    def serve_index(self):
        index_path = os.path.join(BASE_DIR, 'index.html')
        if os.path.isfile(index_path):
            return self._serve_static(index_path, 'text/html; charset=utf-8')
        self.send_error(404, "index.html не найден")

    # ==================== hmp4.html ====================
    def serve_player(self):
        player_path = os.path.join(BASE_DIR, 'hmp4.html')
        if os.path.isfile(player_path):
            return self._serve_static(player_path, 'text/html; charset=utf-8')
        self.send_error(404, "hmp4.html не найден")

    def _serve_static(self, path, mime):
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ==================== Отдача файлов ====================
    def serve_file(self, filepath, filename):
        lower = filename.lower()
        size = os.path.getsize(filepath)

        # MIME
        if lower.endswith('.hmp5'):
            mime = 'text/plain; charset=utf-8'
        elif lower.endswith('.mp4'):
            mime = 'video/mp4'
        elif lower.endswith('.webm'):
            mime = 'video/webm'
        elif lower.endswith('.mkv'):
            mime = 'video/x-matroska'
        elif lower.endswith('.mov'):
            mime = 'video/quicktime'
        elif lower.endswith('.m4v'):
            mime = 'video/x-m4v'
        elif lower.endswith('.avi'):
            mime = 'video/x-msvideo'
        elif lower.endswith('.ogv'):
            mime = 'video/ogg'
        elif lower.endswith('.mp3'):
            mime = 'audio/mpeg'
        elif lower.endswith('.wav'):
            mime = 'audio/wav'
        elif lower.endswith('.m4a'):
            mime = 'audio/mp4'
        elif lower.endswith('.aac'):
            mime = 'audio/aac'
        elif lower.endswith('.ogg'):
            mime = 'audio/ogg'
        else:
            mime = 'application/octet-stream'

        # .hmp5 отдаём целиком (маленький)
        if lower.endswith('.hmp5'):
            return self._serve_static(filepath, mime)

        # Остальное — с поддержкой Range (перемотка)
        range_header = self.headers.get('Range')

        if range_header:
            range_val = range_header.replace('bytes=', '')
            parts = range_val.split('-')
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
            self.send_header('Content-Length', str(size))
            self.end_headers()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    # ==================== POST (загрузка) ====================
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/upload':
            self.send_error(404)
            return

        ctype = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in ctype:
            self.send_error(400, "Ожидается multipart/form-data")
            return

        boundary = ctype.split('boundary=')[1].encode()
        length = int(self.headers.get('Content-Length', 0))

        if length > MAX_UPLOAD:
            self.send_error(413, "Файл слишком большой")
            return

        os.makedirs(VIDEOS_DIR, exist_ok=True)

        remaining = length
        delimiter = b'--' + boundary
        line = self.rfile.readline()
        remaining -= len(line)

        saved_path = None

        while remaining > 0:
            if not line.startswith(delimiter):
                break

            headers = {}
            while True:
                h = self.rfile.readline()
                remaining -= len(h)
                if h in (b'\r\n', b'\n', b''):
                    break
                k, _, v = h.decode('utf-8', 'replace').partition(':')
                headers[k.strip().lower()] = v.strip()

            disposition = headers.get('content-disposition', '')
            fname_match = re.search(
                r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?',
                disposition, re.IGNORECASE
            )

            if fname_match:
                raw_name = urllib.parse.unquote(fname_match.group(1))
                fname = safe_filename(raw_name)
                dest = unique_path(VIDEOS_DIR, fname)
                out = open(dest, 'wb')
                saved_path = dest
            else:
                out = None

            buf = b''
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                buf += chunk
                idx = buf.find(delimiter)
                if idx != -1:
                    body = buf[:idx]
                    if body.endswith(b'\r\n'):
                        body = body[:-2]
                    if out:
                        out.write(body)
                    rest = buf[idx:]
                    nl = rest.find(b'\r\n')
                    if nl != -1:
                        line = rest[nl + 2:] if nl + 2 < len(rest) else b''
                    else:
                        line = b''
                    break
                else:
                    keep = len(delimiter) + 4
                    if len(buf) > keep:
                        flush = buf[:-keep]
                        if out:
                            out.write(flush)
                        buf = buf[-keep:]
            else:
                if out and buf:
                    if buf.endswith(b'\r\n'):
                        buf = buf[:-2]
                    out.write(buf)

            if out:
                out.close()

            if remaining <= 0:
                break

        if saved_path:
            name = os.path.basename(saved_path)
            body = json.dumps({
                'ok': True,
                'name': name,
                'title': os.path.splitext(name)[0],
                'type': 'hmp5' if name.lower().endswith('.hmp5') else 'video',
                'url': '/' + urllib.parse.quote(name),
            }, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
        else:
            body = json.dumps({'ok': False, 'error': 'Файл не получен'}).encode('utf-8')
            self.send_response(400)

        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    os.chdir(BASE_DIR)
    os.makedirs(VIDEOS_DIR, exist_ok=True)

    ip = get_local_ip()

    print()
    print('Home Stream Server')
    print()
    print(f'  Локально:   http://localhost:{PORT}')
    print(f'  По сети:    http://{ip}:{PORT}')
    print(f'  Папка:      {VIDEOS_DIR}')
    print(f'  Плеер:      http://localhost:{PORT}/hmp4.html')
    print()
    print('  Ctrl+C - остановить')
    print()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()
            print('Остановлено.')
