"""Pi-fallback renderer.

Drives a 32x32 panel via hzeller's rpi-rgb-led-matrix C bindings on a Pi with
the Adafruit RGB Matrix Bonnet. Used only if the S3 Matrix Portal path doesn't
pan out — delete this file once the S3 is in production.

Imports `render_frame` from sibling server/ so the paint logic stays
single-source. The bonnet was the original target hardware; see the project
README "Hardware notes" section for audio-blacklist + dtparam=audio=off
gotchas.

Usage on the Pi:
    sudo .venv/bin/python legacy/pi_renderer.py
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from rgbmatrix import RGBMatrix, RGBMatrixOptions  # noqa: E402
from renderer import MATRIX_H, MATRIX_W, render_frame  # noqa: E402

SERVER_URL = os.environ.get("CLAUDE_LED_SERVER", "http://localhost:5000") + "/sessions"
POLL_INTERVAL_S = 0.5


def fetch_sessions() -> list[dict]:
    try:
        r = requests.get(SERVER_URL, timeout=1.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def main() -> None:
    options = RGBMatrixOptions()
    options.rows = MATRIX_H
    options.cols = MATRIX_W
    options.chain_length = 1
    options.parallel = 1
    options.hardware_mapping = "regular"
    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()
    while True:
        render_frame(canvas, fetch_sessions())
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
