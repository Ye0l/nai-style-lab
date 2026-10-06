"""Private JSON-lines transport over inherited pipes; never opens a listening port."""
import base64
import json
import os
import sys
import threading
from pathlib import Path

from bridge import App
from engine import Engine, UserError
from version import VERSION


class DesktopUpdater:
    """Windows ZIP releases cannot be installed over a Linux package."""
    def view(self):
        return {'current': VERSION, 'state': 'idle', 'latest': None, 'notes': '',
                'page': 'https://github.com/Ye0l/nai-style-lab/releases', 'error': None,
                'progress': None, 'blocked': 'Linux 앱은 새 패키지를 설치하거나 git으로 업데이트하세요.'}

    def check(self):
        return self.view()

    def install(self, **_):
        raise UserError(self.view()['blocked'])


def dispatch(app, request):
    kind = request['kind']
    if kind == 'call':
        path = request['path']
        # Paths selected by the trusted native shell, never by JavaScript.
        picked = request.get('picked')
        if path == '/api/data/export':
            app.save_dialog = lambda _: picked
        elif path == '/api/data/pick':
            app.open_dialog = lambda: picked
        return json.loads(app.call(request['method'], path, request.get('body')))
    if kind == 'resource':
        found = app.resource(request['path'])
        if not found:
            return None
        path, content_type, cache = found
        return {'bytes': base64.b64encode(path.read_bytes()).decode('ascii'),
                'content_type': content_type, 'cache': cache}
    if kind == 'shutdown':
        app.shutdown()
        return True
    raise ValueError('Unknown transport request')


def main():
    data = Path(sys.argv[1]).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Advisory lock lives for the process, so crashes leave no stale ownership.
    import fcntl
    lock = open(data / '.tauri.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.umask(0o077)
    engine = Engine(data, auto_subscription=True)
    app = App(engine, data / 'app.log')
    app.updater = DesktopUpdater()
    app.start_drag = lambda _: (_ for _ in ()).throw(UserError(
        'Linux에서는 파일 끌어내기를 지원하지 않습니다. 원본 열기 또는 폴더 열기를 사용하세요.'))
    threading.Thread(target=app.thumbnail_worker, daemon=True).start()
    threading.Thread(target=engine.refresh_subscription, daemon=True).start()
    output = sys.stdout
    sys.stdout = sys.stderr  # Keep incidental engine output away from the protocol.
    print(json.dumps({'ready': True}), file=output, flush=True)
    saved = False
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                result = dispatch(app, request)
                reply = {'result': result}
            except Exception as exc:
                app.log(f'transport: {exc}')
                reply = {'error': str(exc)}
            print(json.dumps(reply, ensure_ascii=False), file=output, flush=True)
            if request.get('kind') == 'shutdown':
                saved = reply.get('result') is True
                break
    finally:
        if not saved:
            app.shutdown()
        lock.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ready': False, 'error': str(exc)}, ensure_ascii=False), flush=True)
        raise
