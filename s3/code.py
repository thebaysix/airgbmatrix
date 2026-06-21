"""S3 Matrix Portal entry point. Runs on real CircuitPython hardware.

Copy s3/canvas.py, s3/colors.py, s3/render_frame.py and this file to
CIRCUITPY/. Do NOT copy s3/displayio.py — that's the dev-box mock; the real
displayio is built into the firmware. Same for s3/term_mock.py (devbox-only).

Set WIFI_SSID, WIFI_PASSWORD, and SERVER_URL in CIRCUITPY/settings.toml. See
settings.toml.example.
"""
import os
import time

import board                               # noqa: F401
import displayio
import wifi
import socketpool
import ssl
import adafruit_requests
from adafruit_matrixportal.matrix import Matrix

from canvas import Canvas
from render_frame import render_frame, MATRIX_W, MATRIX_H

POLL_INTERVAL_S = 0.5
HTTP_TIMEOUT_S = 2
# Brightness comes from settings.toml as an integer percent 0-100
# (CircuitPython's settings.toml only supports ints and strings, no floats).
# Default 20 if unset; clamp anything wild to a sane range.
_b = os.getenv("BRIGHTNESS_PCT")
BRIGHTNESS = max(0, min(100, int(_b) if _b is not None else 20)) / 100.0


def main():
    matrix = Matrix(width=MATRIX_W, height=MATRIX_H, bit_depth=4)
    display = matrix.display
    display.auto_refresh = False

    canvas = Canvas(MATRIX_W, MATRIX_H, brightness=BRIGHTNESS)
    group = displayio.Group()
    group.append(displayio.TileGrid(canvas.bitmap, pixel_shader=canvas.palette))
    display.root_group = group

    # Paint the empty grid before any blocking network call (wifi connect and
    # the first HTTP connect can each stall for seconds). Proves the panel is
    # alive at boot instead of showing black while we wait on the server.
    render_frame(canvas, [], 0)
    display.refresh()

    wifi.radio.connect(os.getenv("WIFI_SSID"), os.getenv("WIFI_PASSWORD"))
    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())

    server_url = os.getenv("SERVER_URL")  # e.g. http://192.168.1.10:5000

    while True:
        sessions = []
        try:
            r = requests.get(server_url + "/sessions", timeout=HTTP_TIMEOUT_S)
            sessions = r.json()
            r.close()
        except Exception:
            # Stale frame is better than a hung loop.
            pass
        try:
            now = int(time.time())
        except Exception:
            now = 0  # NTP not synced yet — skip the >24h dim feature
        render_frame(canvas, sessions, now)
        display.refresh()
        time.sleep(POLL_INTERVAL_S)


main()
