"""Port of server/renderer.py's render_frame for CircuitPython.

Differs from the server version in three ways that matter:
  - Imports `colors` from this directory (no colorsys dependency).
  - Trusts `ts_epoch` exclusively — no fallback to ISO parsing.
  - Takes `now_epoch` as a parameter instead of computing it internally, so
    NTP-less boots can pass 0 (and just lose the >24h dim feature) rather
    than crashing.

The animation step uses time.monotonic(), which works on CircuitPython without
any clock sync.
"""
import math
import time

from colors import PALETTE, STATE_COLORS
from features import BLINK_ON_PERMISSIONS

MATRIX_W, MATRIX_H = 32, 32
TILE_W, TILE_H = 16, 8
COLS, ROWS = 2, 4
COLOR_W = 4               # width of the session-color swatch (left side of tile)
HIST_W = TILE_W - COLOR_W  # remaining columns belong to the histogram

BAR_BRIGHT = (255, 255, 255)
BAR_DIM = (60, 60, 60)
BAR_GREEN = (0, 200, 0)
BAR_GREEN_DIM = (0, 60, 0)
BAR_RED = (220, 30, 30)
BAR_RED_DIM = (60, 12, 12)
# Full-column markers for /compact and /clear.
BAR_COMPACT = (100, 180, 255)
BAR_COMPACT_DIM = (28, 50, 70)
BAR_CLEAR = (180, 100, 220)
BAR_CLEAR_DIM = (50, 28, 60)
# Awaiting-permission indicator (BLINK_ON_PERMISSIONS feature flag).
BAR_AMBER = (255, 180, 0)
HALF_TILE = TILE_H // 2  # code bar splits column at midline (4 px each side)
DIM_AFTER_S = 24 * 3600


def slot_origin(slot_idx):
    col = slot_idx // ROWS
    row = slot_idx % ROWS
    return col * TILE_W, row * TILE_H


def _turn_epoch(turn):
    e = turn.get("ts_epoch")
    if isinstance(e, int):
        return e
    if isinstance(e, float):
        return int(e)
    return 0


def _visible_turns(s):
    # Newest HIST_W per session — same set the renderer paints, so scale
    # isn't compressed by aged-off-screen turns still in storage.
    return sorted(s.get("turns") or [], key=_turn_epoch, reverse=True)[:HIST_W]


def _global_max_tokens(sessions):
    m = 0
    for s in sessions:
        for t in _visible_turns(s):
            tok = t.get("tokens", 0)
            if tok > m:
                m = tok
    return m


def _global_max_changes(sessions):
    max_added = 0
    max_removed = 0
    for s in sessions:
        for t in _visible_turns(s):
            a = t.get("added", 0) or 0
            r = t.get("removed", 0) or 0
            if a > max_added:
                max_added = a
            if r > max_removed:
                max_removed = r
    return max_added, max_removed


def _bar_height(tokens, max_tokens):
    if tokens <= 0 or max_tokens <= 0:
        return 0
    ratio = math.sqrt(tokens / max_tokens)
    h = round(ratio * TILE_H)
    return h if h >= 1 else 1


def _half_bar_height(value, max_value):
    if value <= 0 or max_value <= 0:
        return 0
    ratio = math.sqrt(value / max_value)
    h = round(ratio * HALF_TILE)
    return h if h >= 1 else 1


def _paint_code_bar(canvas, abs_x, y0, added, removed, max_added, max_removed, dim):
    # Both stack from the bottom: green block first, then red block on top of
    # green. Each capped at HALF_TILE so the combined max (4+4) just fills the
    # column. Single-color turns sit at the floor.
    h_green = _half_bar_height(added, max_added)
    h_red = _half_bar_height(removed, max_removed)
    if h_green > 0:
        color = BAR_GREEN_DIM if dim else BAR_GREEN
        for dy in range(TILE_H - h_green, TILE_H):
            canvas.SetPixel(abs_x, y0 + dy, *color)
    if h_red > 0:
        color = BAR_RED_DIM if dim else BAR_RED
        for dy in range(TILE_H - h_green - h_red, TILE_H - h_green):
            canvas.SetPixel(abs_x, y0 + dy, *color)


def render_frame(canvas, sessions, now_epoch):
    max_tokens = _global_max_tokens(sessions)
    max_added, max_removed = _global_max_changes(sessions)
    empty = STATE_COLORS["empty"]

    for slot in range(COLS * ROWS):
        x0, y0 = slot_origin(slot)
        s = sessions[slot] if slot < len(sessions) else None
        if s:
            ci = s.get("color_idx")
            left = PALETTE[ci] if isinstance(ci, int) and 0 <= ci < len(PALETTE) else PALETTE[0]
        else:
            left = empty

        for dy in range(TILE_H):
            for dx in range(COLOR_W):
                canvas.SetPixel(x0 + dx, y0 + dy, *left)
            for dx in range(HIST_W):
                canvas.SetPixel(x0 + COLOR_W + dx, y0 + dy, *empty)

        if not s:
            continue

        pending = bool(s.get("pending"))
        col_offset = 1 if pending else 0

        turns = s.get("turns") or []
        turns_by_ts = sorted(turns, key=_turn_epoch, reverse=True)
        col = col_offset
        for turn in turns_by_ts:
            if col >= HIST_W:
                break
            age = now_epoch - _turn_epoch(turn) if now_epoch else 0
            dim = age > DIM_AFTER_S
            kind = turn.get("kind") or "turn"
            if kind == "compact" or kind == "clear":
                if kind == "compact":
                    color = BAR_COMPACT_DIM if dim else BAR_COMPACT
                else:
                    color = BAR_CLEAR_DIM if dim else BAR_CLEAR
                for dy in range(TILE_H):
                    canvas.SetPixel(x0 + COLOR_W + col, y0 + dy, *color)
                col += 1
                continue
            if max_tokens > 0:
                h = _bar_height(turn["tokens"], max_tokens)
                if h > 0:
                    color = BAR_DIM if dim else BAR_BRIGHT
                    for dy in range(TILE_H - h, TILE_H):
                        canvas.SetPixel(x0 + COLOR_W + col, y0 + dy, *color)
            col += 1
            added = turn.get("added", 0) or 0
            removed = turn.get("removed", 0) or 0
            if (added > 0 or removed > 0) and col < HIST_W:
                _paint_code_bar(canvas, x0 + COLOR_W + col, y0,
                                added, removed, max_added, max_removed, dim)
                col += 1

        if pending:
            # --- BLINK_ON_PERMISSIONS feature gate ---
            awaiting = BLINK_ON_PERMISSIONS and bool(s.get("awaiting"))
            if awaiting:
                if int(time.monotonic() * 2) % 2 == 0:
                    for dy in range(TILE_H):
                        canvas.SetPixel(x0 + COLOR_W + 0, y0 + dy, *BAR_AMBER)
            # --- end BLINK_ON_PERMISSIONS ---
            else:
                h = int(time.monotonic() * 2) % TILE_H + 1
                for dy in range(TILE_H - h, TILE_H):
                    canvas.SetPixel(x0 + COLOR_W + 0, y0 + dy, *BAR_BRIGHT)
