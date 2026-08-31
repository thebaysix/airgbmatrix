"""Dev-box mock of the S3 Matrix Portal.

Runs the same code path as s3/code.py — Canvas, displayio.Bitmap + Palette,
render_frame — but with the mock displayio (s3/displayio.py) painting to the
terminal, and `requests` standing in for `adafruit_requests`. When you swap
this for s3/code.py on real hardware, the only differences are WiFi setup and
the matrix vs. terminal display.

Usage:
    python3 server/server.py            # terminal 1
    python3 s3/term_mock.py             # terminal 2
    python3 s3/term_mock.py --debug     # with diagnostic sidebar
"""
import json
import os
import sys
import time
from datetime import datetime

import requests

# Ensure s3/ is on sys.path so 'import displayio' picks up the local mock,
# regardless of where this script is launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import displayio  # noqa: E402  (s3/displayio.py — the mock)
from canvas import Canvas  # noqa: E402
from render_frame import render_frame, MATRIX_W, MATRIX_H  # noqa: E402

SERVER_URL = "http://localhost:5000"
POLL_INTERVAL_S = 0.5
HTTP_TIMEOUT_S = 1.0
DEBUG_ROW = MATRIX_H // 2 + 2  # 1 blank row below the matrix (which takes H/2 rows)


def _latest_tokens(turns):
    positive = [t for t in turns if (t.get("tokens", 0) or 0) > 0]
    if not positive:
        return 0
    latest = max(positive, key=lambda t: (t.get("ts_epoch", 0), t.get("ts", "")))
    return latest.get("tokens", 0) or 0


def _paint_debug(sessions, now_epoch, fetch_ok):
    """Paint a copy-pasteable JSON dump below the matrix."""
    summary = (
        f"debug  {datetime.fromtimestamp(now_epoch).strftime('%H:%M:%S')}  "
        f"fetch={'ok' if fetch_ok else 'FAIL'}  "
        f"{len(sessions)}/8 sessions"
    )
    records = []
    for slot, s in enumerate(sessions[:8]):
        turns = s.get("turns") or []
        updated = s.get("updated_at") or ""
        try:
            ua = updated.replace("Z", "+00:00") if updated.endswith("Z") else updated
            age_s = now_epoch - int(datetime.fromisoformat(ua).timestamp())
        except ValueError:
            age_s = -1
        records.append({
            "slot": slot,
            "id": s.get("id"),
            "state": s.get("state"),
            "pending": bool(s.get("pending")),
            "turns": len(turns),
            "context_tokens": _latest_tokens(turns),
            "added": sum(t.get("added", 0) or 0 for t in turns),
            "removed": sum(t.get("removed", 0) or 0 for t in turns),
            "age_s": age_s,
            "updated_at": s.get("updated_at"),
        })
    lines = [summary, "["]
    for i, rec in enumerate(records):
        comma = "," if i < len(records) - 1 else ""
        lines.append("  " + json.dumps(rec) + comma)
    lines.append("]")
    # Use explicit cursor positioning per row instead of \n. A trailing \n at
    # the bottom of the terminal triggers scrolling, which pushes the matrix
    # up and out of view; cursor positioning never scrolls.
    out = []
    for i, line in enumerate(lines):
        out.append(f"\x1b[{DEBUG_ROW + i};1H{line}\x1b[K")
    # Position past the last line and clear from there to end of screen — wipes
    # any stale rows from a previous (longer) render. Doesn't scroll.
    out.append(f"\x1b[{DEBUG_ROW + len(lines)};1H\x1b[J")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def main():
    debug = "--debug" in sys.argv
    display = displayio.TerminalDisplay(MATRIX_W, MATRIX_H)
    canvas = Canvas(MATRIX_W, MATRIX_H)
    group = displayio.Group()
    group.append(displayio.TileGrid(canvas.bitmap, pixel_shader=canvas.palette))
    display.root_group = group

    try:
        while True:
            sessions = []
            fetch_ok = False
            try:
                r = requests.get(SERVER_URL + "/sessions", timeout=HTTP_TIMEOUT_S)
                r.raise_for_status()
                sessions = r.json()
                fetch_ok = True
            except Exception:
                pass
            now = int(time.time())
            render_frame(canvas, sessions, now)
            display.refresh()
            if debug:
                _paint_debug(sessions, now, fetch_ok)
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[0m\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
