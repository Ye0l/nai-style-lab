"""Single-user browser app. Run: python web_server.py --port 8080."""
import argparse
import hashlib
import io
import hmac
import html
import json
import os
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))
from bridge import App
from engine import Engine, UserError
from version import VERSION

MAX_UPLOAD = 256 * 1024 * 1024
MAX_JSON = 1024 * 1024
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"


class WebUpdater:
    def view(self):
        return {'current': VERSION, 'state': 'idle', 'latest': None, 'notes': '',
                'page': 'https://github.com/Ye0l/nai-style-lab/releases', 'error': None,
                'progress': None, 'blocked': '웹 앱은 서버 업데이트 때 새 버전이 반영됩니다.'}

    def check(self):
        return self.view()

    def install(self, **_):
        raise UserError(self.view()['blocked'])


class WebService:
    def __init__(self, engine, token='', allowed_hosts=None, secure_cookie=False):
        self.app = App(engine, engine.data_dir / 'app.log')
        self.app.updater = WebUpdater()
        self.token = token
        self.cookie = secrets.token_urlsafe(32)
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or ['localhost', '127.0.0.1', '::1'])}
        self.secure_cookie = secure_cookie
        self.lock = threading.RLock()
        self.drafts = {}

    def call(self, method, path, body=None):
        body = body or {}
        route = urlsplit(path).path
        with self.lock:
            if route == '/api/meta' and method == 'GET':
                result = json.loads(self.app.call(method, path, body))
                if result['ok']:
                    result['data'].update(runtime='browser', authenticated=bool(self.token), max_upload=MAX_UPLOAD)
                return result
            if route == '/api/settings/draft' and method == 'POST':
                client, seq = body.get('client_id'), body.get('seq')
                if not isinstance(client, str) or len(client) > 128 or not isinstance(seq, int):
                    return {'ok': False, 'error': '잘못된 설정 저장 요청입니다.'}
                if seq <= self.drafts.get(client, -1):
                    return {'ok': True, 'data': None}
                if len(self.drafts) >= 256 and client not in self.drafts:
                    self.drafts.pop(next(iter(self.drafts)))
                self.drafts[client] = seq
                # A browser closing must not be needed to flush settings to disk.
                return json.loads(self.app.call('POST', '/api/settings', {'changes': body.get('changes', {})}))
            if route == '/api/open' and method == 'POST':
                target = self.app.engine.image_path(body.get('id')) if body.get('id') else self.app.safe_image(body.get('file'))
                if not target or not target.is_file():
                    return {'ok': False, 'error': '원본 이미지 파일을 찾을 수 없습니다.'}
                return {'ok': True, 'data': {'url': '/img/' + quote(target.name)}}
            if route == '/api/update/page' and method == 'POST':
                return {'ok': True, 'data': {'url': self.app.updater.view()['page']}}
            # A remote browser must never launch processes or native file dialogs on the server.
            if route in ('/api/open-folder', '/api/open-novelai', '/api/drag', '/api/data/pick',
                         '/api/data/export', '/api/data/import'):
                return {'ok': False, 'error': '브라우저의 파일 선택·다운로드 기능을 사용하세요.'}
            return json.loads(self.app.call(method, path, body))

    def shutdown(self):
        with self.lock:
            self.app.shutdown()


class Handler(BaseHTTPRequestHandler):
    server_version = 'NAIStyleLab'
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    @property
    def service(self):
        return self.server.service

    def log_message(self, *_):
        pass  # Do not put passwords, paths or query strings in access logs.

    def send(self, status, content=b'', ctype='application/json; charset=utf-8', headers=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(content)))
        self.common_headers(headers)
        self.end_headers()
        self.wfile.write(content)

    def common_headers(self, headers=None):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Referrer-Policy', 'same-origin')
        headers = headers or {}
        self.send_header('Cache-Control', headers.get('Cache-Control', 'no-store'))
        for name, value in headers.items():
            if name != 'Cache-Control':
                self.send_header(name, value)

    def reply(self, value, status=200):
        self.send(status, json.dumps(value, ensure_ascii=False).encode())

    def error(self, text, status=400):
        self.reply({'ok': False, 'error': text}, status)

    def valid_host(self):
        try:
            return urlsplit('//' + self.headers.get('Host', '')).hostname in self.service.allowed_hosts
        except ValueError:
            return False

    def same_origin(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.scheme in ('http', 'https') and parsed.netloc == self.headers.get('Host')

    def authorized(self):
        if not self.service.token:
            return True
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookie.get('nai_session')
            return bool(value and hmac.compare_digest(value.value, self.service.cookie))
        except Exception:
            return False

    def gate(self):
        if not self.valid_host():
            self.error('허용되지 않은 호스트입니다.', 403)
            return False
        if not self.authorized():
            if self.path.startswith('/api/'):
                self.error('로그인이 필요합니다.', 401)
            else:
                self.send(303, headers={'Location': '/login'})
            return False
        return True

    def read_body(self, limit):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Content-Length가 있는 요청만 지원합니다.')
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            raise ValueError('잘못된 Content-Length입니다.')
        if not 0 <= length <= limit:
            raise ValueError(f'파일 또는 요청이 너무 큽니다. 최대 {limit // (1024 * 1024)} MiB입니다.')
        content = self.rfile.read(length)
        if len(content) != length:
            raise ValueError('요청 본문을 끝까지 받지 못했습니다.')
        return content

    def login_page(self, error=''):
        page = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NAI Style Lab · 로그인</title><style>body{{margin:0;background:#0b0d12;color:#eee;font:16px system-ui;display:grid;place-items:center;min-height:100dvh}}main{{box-sizing:border-box;width:min(400px,100%);padding:28px}}input,button{{box-sizing:border-box;width:100%;font:inherit;padding:14px;border-radius:10px;margin-top:12px}}button{{background:#8b7dff;border:0;color:#fff}}p{{line-height:1.6;color:#abb0c4}}</style>
<main><h1>NAI Style Lab</h1><p>접속 비밀번호를 입력하세요.</p><form method="post" action="/login"><label for="password">비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button>로그인</button></form><p role="alert">{html.escape(error)}</p></main></html>'''
        self.send(200, page.encode(), 'text/html; charset=utf-8')

    def do_GET(self):
        if not self.valid_host():
            return self.error('허용되지 않은 호스트입니다.', 403)
        path = urlsplit(self.path).path
        if path == '/login':
            if self.authorized():
                return self.send(303, headers={'Location': '/'})
            return self.login_page()
        if not self.gate():
            return
        try:
            if path == '/api/data/export':
                with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as file:
                    with self.service.lock:
                        self.service.app.engine.export_data(file)
                    size = file.tell()
                    file.seek(0)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Length', str(size))
                    self.common_headers({'Content-Disposition': f'attachment; filename="nai-style-lab-{datetime.now():%Y%m%d_%H%M%S}.zip"'})
                    self.end_headers()
                    shutil.copyfileobj(file, self.wfile)
                return
            if path.startswith('/api/'):
                return self.reply(self.service.call('GET', self.path))
            found = self.service.app.resource(path)
            if not found:
                return self.error('파일을 찾을 수 없습니다.', 404)
            file, ctype, cache = found
            with file.open('rb') as stream:
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
                self.common_headers({'Cache-Control': 'private, ' + cache})
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.service.app.log(f'web GET: {type(exc).__name__}')
            self.error('요청 처리 중 오류가 났습니다.', 500)

    def do_POST(self):
        if not self.valid_host() or not self.same_origin():
            return self.error('허용되지 않은 요청 출처입니다.', 403)
        path = urlsplit(self.path).path
        try:
            if path == '/login':
                password = parse_qs(self.read_body(4096).decode()).get('password', [''])[0]
                # Fixed-length digests allow arbitrary Unicode passwords without compare_digest errors.
                if self.service.token and not hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                                                 hashlib.sha256(self.service.token.encode()).digest()):
                    return self.login_page('비밀번호가 올바르지 않습니다.')
                cookie = 'nai_session=' + self.service.cookie + '; HttpOnly; SameSite=Strict; Path=/'
                if self.service.secure_cookie:
                    cookie += '; Secure'
                return self.send(303, headers={'Set-Cookie': cookie, 'Location': '/'})
            if not self.gate():
                return
            if path == '/api/data/import':
                if self.headers.get_content_type() != 'application/zip':
                    return self.error('ZIP 파일을 업로드하세요.', 415)
                content = self.read_body(MAX_UPLOAD)
                try:
                    with zipfile.ZipFile(io.BytesIO(content)) as archive:
                        entries = archive.infolist()
                        if len(entries) > 20000 or sum(i.file_size for i in entries) > 1024 * 1024 * 1024:
                            raise UserError('압축을 푼 데이터가 너무 큽니다. 최대 1 GiB입니다.')
                        for entry in entries:
                            limit = 64 * 1024 if entry.filename == 'api_key.txt' else 64 * 1024 * 1024
                            if entry.file_size > limit:
                                raise UserError('ZIP 내부 파일이 너무 큽니다.')
                except zipfile.BadZipFile:
                    raise UserError('NAI Style Lab에서 내보낸 ZIP 파일이 아닙니다.')
                with self.service.lock:
                    result = self.service.app.engine.import_data(content)
                return self.reply({'ok': True, 'data': result})
            if self.headers.get_content_type() != 'application/json':
                return self.error('JSON 요청이 필요합니다.', 415)
            body = json.loads(self.read_body(MAX_JSON) or b'{}')
            if not isinstance(body, dict):
                return self.error('JSON 객체가 필요합니다.')
            self.reply(self.service.call('POST', self.path, body))
        except (UserError, ValueError) as exc:
            self.error(str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.service.app.log(f'web POST: {type(exc).__name__}')
            self.error('요청 처리 중 오류가 났습니다.', 500)


def make_server(service, host='127.0.0.1', port=8080):
    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6
    server = (IPv6Server if ':' in host else ThreadingHTTPServer)((host, port), Handler)
    server.daemon_threads = True
    server.service = service
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('NAI_DATA_DIR', ROOT / 'data')))
    args = parser.parse_args()
    token = os.environ.get('NAI_WEB_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not token:
        parser.error('외부 접속을 열려면 NAI_WEB_TOKEN 접속 비밀번호를 설정하세요.')
    os.umask(0o077)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.data_dir / '.tauri.lock', 'a+b')  # Shared lock with the Linux desktop backend.
    if os.name == 'nt':
        import msvcrt
        lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    engine = Engine(args.data_dir, auto_subscription=True)
    hosts = os.environ.get('NAI_ALLOWED_HOSTS', 'localhost,127.0.0.1,::1').split(',')
    service = WebService(engine, token, [h.strip() for h in hosts if h.strip()], os.environ.get('NAI_COOKIE_SECURE') == '1')
    server = make_server(service, args.host, args.port)
    threading.Thread(target=service.app.thumbnail_worker, daemon=True).start()
    threading.Thread(target=engine.refresh_subscription, daemon=True).start()
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f'NAI Style Lab: http://{args.host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.shutdown()
        lock.close()


if __name__ == '__main__':
    main()
