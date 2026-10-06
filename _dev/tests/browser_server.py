"""Disposable HTTP fixture for browser tests; all image generation is fake."""
import io
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import web_server as web
from PIL import Image, ImageDraw


def main():
    with tempfile.TemporaryDirectory() as tmp:
        buffer = io.BytesIO()
        image = Image.new('RGB', (260, 380), '#2c2844')
        ImageDraw.Draw(image).ellipse((60, 80, 200, 220), fill='#a99eff')
        image.save(buffer, 'PNG')
        engine = web.Engine(Path(tmp), generate=lambda *a, **kw: buffer.getvalue(), auto_subscription=False)
        engine.api_key = 'offline-test-key'
        engine.state['ui_state']['delay'] = 0
        engine.start_random(12)
        while engine.job['running']:
            time.sleep(.01)
        for i, combo in enumerate(engine.combos):
            combo.update(matches=8, wins=4, elo=10000 + i * 70)
        engine.save()
        service = web.WebService(engine)
        server = web.make_server(service, port=0)
        threading.Thread(target=service.app.thumbnail_worker, daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
        print(f'http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close(); service.shutdown()


if __name__ == '__main__':
    main()
