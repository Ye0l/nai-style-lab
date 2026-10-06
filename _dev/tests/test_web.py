"""Real HTTP tests; uses a fake generator and never contacts NovelAI."""
import http.client
import importlib.util
import io
import json
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import web_server as web
from PIL import Image


def main():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        engine = web.Engine(data, auto_subscription=False)
        engine.api_key = 'private-test-key'
        engine.key_file.write_text(engine.api_key)
        image = engine.img_dir / 'test.png'
        Image.new('RGB', (64, 96), '#8b7dff').save(image)
        service = web.WebService(engine, token='접속 비밀번호')
        server = web.make_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        cookie = ''
        def req(method, path, body=None, headers=None, authorized=True):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            hs = dict(headers or {})
            if authorized and cookie:
                hs['Cookie'] = cookie
            if isinstance(body, dict):
                body = json.dumps(body).encode(); hs.setdefault('Content-Type', 'application/json')
            conn.request(method, path, body, hs)
            response = conn.getresponse()
            status, result, returned = response.status, response.read(), dict(response.getheaders())
            conn.close()
            return status, result, returned
        def api(method, path, body=None):
            status, content, _ = req(method, path, body)
            value = json.loads(content)
            assert status == 200 and value['ok'], (status, value)
            return value['data']
        try:
            assert req('GET', '/')[0] == 303
            assert req('GET', '/api/settings')[0] == 401
            assert req('GET', '/img/test.png')[0] == 303
            assert req('GET', '/login')[0] == 200
            status, content, headers = req('POST', '/login', 'password=wrong', {'Content-Type': 'application/x-www-form-urlencoded'})
            assert status == 200 and 'Set-Cookie' not in headers
            from urllib.parse import urlencode
            status, _, headers = req('POST', '/login', urlencode({'password': service.token}), {'Content-Type': 'application/x-www-form-urlencoded'})
            assert status == 303 and 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
            cookie = headers['Set-Cookie'].split(';')[0]
            assert req('GET', '/')[0] == 200
            assert api('GET', '/api/meta')['runtime'] == 'browser'
            assert req('GET', '/api/settings', headers={'Host': 'evil.test'})[0] == 403
            assert req('POST', '/api/artists/add', {'text': 'evil'}, {'Origin': 'https://evil.test'})[0] == 403
            assert req('POST', '/api/settings', 'anything', {'Content-Type': 'text/plain'})[0] == 415
            assert api('POST', '/api/artists/add', {'text': 'artist:http-test'})['added'] == 1
            api('POST', '/api/settings/draft', {'client_id': 'one', 'seq': 2, 'changes': {'steps': 21}})
            api('POST', '/api/settings/draft', {'client_id': 'one', 'seq': 1, 'changes': {'steps': 20}})
            assert api('GET', '/api/settings')['settings']['steps'] == 21
            assert json.loads(engine.state_file.read_text())['ui_state']['steps'] == 21
            assert api('POST', '/api/open', {'file': 'test.png'})['url'] == '/img/test.png'
            for path in ['/static/../../app/core.py', '/static/%2e%2e/app/core.py', '/img/../api_key.txt', '/data/api_key.txt']:
                assert req('GET', path)[0] == 404, path
            status, content, headers = req('GET', '/img/test.png')
            assert status == 200 and content.startswith(b'\x89PNG') and 'private' in headers['Cache-Control']
            status, backup, headers = req('GET', '/api/data/export')
            assert status == 200 and headers['Content-Type'] == 'application/zip'
            assert 'attachment' in headers['Content-Disposition']
            assert zipfile.ZipFile(io.BytesIO(backup)).read('api_key.txt') == b'private-test-key'
            api('POST', '/api/settings', {'changes': {'steps': 19}})
            status, content, _ = req('POST', '/api/data/import', backup, {'Content-Type': 'application/zip'})
            assert status == 200 and json.loads(content)['ok']
            assert api('GET', '/api/settings')['settings']['steps'] == 21
            assert req('POST', '/api/data/import', b'not a zip', {'Content-Type': 'application/zip'})[0] == 400
            for path in ['/api/open-folder', '/api/drag', '/api/data/pick']:
                assert not json.loads(req('POST', path, {})[1])['ok']
            assert not json.loads(req('POST', '/api/update/install', {})[1])['ok']
            assert req('GET', '/api/data/export', authorized=False)[0] == 401
        finally:
            server.shutdown(); server.server_close(); thread.join(); service.shutdown()
    print('PASS: HTTP authentication, host/origin guards, private resources, durable drafts, ZIP export/import, browser routes.')


if __name__ == '__main__':
    main()
