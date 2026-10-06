"""Transport integration without GUI or paid NovelAI requests."""
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        child = subprocess.Popen([sys.executable, '-u', str(ROOT / 'app/tauri_backend.py'), tmp],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=env)
        def rpc(request):
            child.stdin.write(json.dumps(request) + '\n')
            child.stdin.flush()
            reply = json.loads(child.stdout.readline())
            assert 'error' not in reply, reply
            return reply['result']
        def call(method, path, body=None, **extra):
            return rpc(dict(kind='call', method=method, path=path, body=body, **extra))
        try:
            assert json.loads(child.stdout.readline()) == {'ready': True}
            assert call('GET', '/api/meta')['data']['data_dir'] == tmp
            assert call('POST', '/api/artists/add', {'text': 'artist:test'})['data']['added'] == 1
            assert call('GET', '/api/artists')['ok']
            assert not call('GET', '/api/nope')['ok']
            page = rpc({'kind': 'resource', 'path': '/'})
            assert b'<html' in base64.b64decode(page['bytes'])
            assert rpc({'kind': 'resource', 'path': '/static/../../app/engine.py'}) is None
            assert rpc({'kind': 'resource', 'path': '/img/../api_key.txt'}) is None
            assert 'tauri' in base64.b64decode(rpc({'kind': 'resource', 'path': '/static/js/api.js'})['bytes']).decode().lower()
            assert call('POST', '/api/data/export', picked=None)['data']['path'] is None
            archive = str(data / 'export.zip')
            assert call('POST', '/api/data/export', picked=archive)['data']['path'] == archive
            assert Path(archive).is_file()
            assert call('POST', '/api/data/pick', picked=archive)['data']['name'] == 'export.zip'
            assert call('POST', '/api/data/import')['ok']
            assert not call('POST', '/api/update/install')['ok']
            second = subprocess.run([sys.executable, str(ROOT / 'app/tauri_backend.py'), tmp],
                                    input='', capture_output=True, text=True, timeout=10, env=env)
            assert second.returncode != 0 and json.loads(second.stdout)['ready'] is False
            assert call('POST', '/api/settings/draft', {'seq': 1, 'changes': {'steps': 21}})['ok']
            assert rpc({'kind': 'shutdown'}) is True
            child.wait(timeout=10)
            assert child.returncode == 0
            assert json.loads((data / 'state.json').read_text())['ui_state']['steps'] == 21
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        # EOF also saves and releases ownership (native process crash or exit).
        eof = subprocess.run([sys.executable, str(ROOT / 'app/tauri_backend.py'), tmp],
                             input='', capture_output=True, text=True, timeout=10, env=env)
        assert eof.returncode == 0, eof.stderr
    print('PASS: Tauri RPC, resources, traversal guards, export/import, lock, drafts, shutdown and EOF.')


if __name__ == '__main__':
    main()
