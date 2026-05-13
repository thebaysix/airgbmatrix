"""Terminal renderer for the 32x32 LED matrix.

Polls server.py and prints the matrix as 24-bit ANSI in the terminal,
using half-block characters so each terminal row shows two matrix rows
(approximating square 'pixels' at typical font aspect ratios).

Usage:
    python3 server.py                       # terminal 1
    python3 term_renderer.py                # terminal 2
    python3 term_renderer.py --debug        # with diagnostic sidebar
"""
import json
import sys
import time
from datetime import datetime

import requests

from renderer import MATRIX_H, MATRIX_W, render_frame

SERVER_URL = "http://localhost:5000"
POLL_INTERVAL_S = 0.5
DEBUG_ROW = MATRIX_H // 2 + 2


class Buffer:
    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.pixels = [[(0, 0, 0)] * w for _ in range(h)]

    def SetPixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.pixels[y][x] = (r, g, b)


def paint(buf: Buffer) -> None:
    out = ["\x1b[H"]  # cursor home — overwrite in place
    for row in range(0, buf.h, 2):
        for col in range(buf.w):
            top = buf.pixels[row][col]
            bot = buf.pixels[row + 1][col] if row + 1 < buf.h else (0, 0, 0)
            out.append(
                f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                f"\x1b[48;2;{bot[0]};{bot[1]};{bot[2]}m▀"
            )
        out.append("\x1b[0m\n")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _paint_debug(sessions: list[dict], now_epoch: int, fetch_ok: bool) -> None:
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
            "tokens": sum(t.get("tokens", 0) for t in turns),
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
    # Cursor-positioning per row instead of trailing \n — a \n at the bottom
    # of the terminal triggers scrolling and pushes the matrix up. Cursor
    # positioning never scrolls.
    out = []
    for i, line in enumerate(lines):
        out.append(f"\x1b[{DEBUG_ROW + i};1H{line}\x1b[K")
    out.append(f"\x1b[{DEBUG_ROW + len(lines)};1H\x1b[J")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def main() -> None:
    debug = "--debug" in sys.argv
    sys.stdout.write("\x1b[2J\x1b[?25l")  # clear screen, hide cursor
    sys.stdout.flush()
    try:
        while True:
            sessions: list[dict] = []
            fetch_ok = False
            try:
                r = requests.get(SERVER_URL + "/sessions", timeout=1.0)
                r.raise_for_status()
                sessions = r.json()
                fetch_ok = True
            except Exception:
                pass
            buf = Buffer(MATRIX_W, MATRIX_H)
            render_frame(buf, sessions)
            paint(buf)
            if debug:
                _paint_debug(sessions, int(time.time()), fetch_ok)
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[0m\n")  # show cursor, reset
        sys.stdout.flush()


if __name__ == "__main__":
    main()
