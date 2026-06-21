"""Browser mock of the 32x32 LED matrix.

Runs on http://localhost:5001 and visualises whatever `server.py` currently
holds. Reuses `render_frame` from renderer.py so the paint logic under test
is identical to what runs on the panel.

Usage:
    python3 server.py        # terminal 1
    python3 mock.py          # terminal 2
    open http://localhost:5001
"""
import threading
import time

import requests
from flask import Flask, jsonify, render_template_string

from renderer import MATRIX_H, MATRIX_W, render_frame

SERVER_URL = "http://localhost:5000"
TICK_S = 0.25
PORT = 5001


class Buffer:
    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.pixels = [[(0, 0, 0)] * w for _ in range(h)]

    def SetPixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.pixels[y][x] = (r, g, b)


_buffer = Buffer(MATRIX_W, MATRIX_H)
_buf_lock = threading.Lock()


def _tick_loop() -> None:
    while True:
        try:
            r = requests.get(SERVER_URL + "/sessions", timeout=1.0)
            r.raise_for_status()
            sessions = r.json()
        except Exception:
            sessions = []
        new_buf = Buffer(MATRIX_W, MATRIX_H)
        render_frame(new_buf, sessions)
        with _buf_lock:
            _buffer.pixels = new_buf.pixels
        time.sleep(TICK_S)


app = Flask(__name__)

PAGE = """
<!doctype html>
<html><head><title>airgbmatrix mock</title>
<style>
  body { background:#111; color:#ccc; font-family:ui-monospace,monospace; margin:0; padding:24px; }
  h1 { font-size:13px; font-weight:normal; color:#888; margin:0 0 16px; }
  .row { display:flex; gap:24px; align-items:flex-start; }
  #grid { display:grid; grid-template-columns:repeat(32,14px); grid-template-rows:repeat(32,14px); gap:1px; background:#000; padding:6px; border:1px solid #333; }
  .px { width:14px; height:14px; border-radius:50%; background:#000; }
  pre { background:#0a0a0a; color:#888; padding:8px; font-size:11px; max-height:480px; min-width:360px; overflow:auto; border:1px solid #222; margin:0; }
</style></head>
<body>
<h1>airgbmatrix mock — 32×32, polling server every {{tick_ms}}ms</h1>
<div class="row">
  <div id="grid"></div>
  <pre id="state"></pre>
</div>
<script>
const grid = document.getElementById('grid');
const stateEl = document.getElementById('state');
const cells = [];
for (let i = 0; i < 32*32; i++) {
  const d = document.createElement('div');
  d.className = 'px';
  grid.appendChild(d);
  cells.push(d);
}
async function refresh() {
  const [pxR, sR] = await Promise.all([fetch('/pixels'), fetch('/state')]);
  const px = await pxR.json();
  for (let y = 0; y < 32; y++)
    for (let x = 0; x < 32; x++) {
      const [r,g,b] = px[y][x];
      cells[y*32 + x].style.background = `rgb(${r},${g},${b})`;
    }
  stateEl.textContent = JSON.stringify(await sR.json(), null, 2);
}
refresh();
setInterval(refresh, 250);
</script>
</body></html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE, tick_ms=int(TICK_S * 1000))


@app.get("/pixels")
def pixels():
    with _buf_lock:
        return jsonify(_buffer.pixels)


@app.get("/state")
def state():
    try:
        return jsonify(requests.get(SERVER_URL + "/sessions", timeout=1.0).json())
    except Exception as e:
        return jsonify(error=str(e))


if __name__ == "__main__":
    threading.Thread(target=_tick_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
