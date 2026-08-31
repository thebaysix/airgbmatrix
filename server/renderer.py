"""Shared paint logic — `render_frame(canvas, sessions)`.

Called by `term_renderer.py`, `mock.py`, and `legacy/pi_renderer.py`. The S3
port lives in `s3/render_frame.py` and intentionally duplicates this logic
(different colors module, no colorsys on CircuitPython).

`canvas` is anything with `SetPixel(x, y, r, g, b)` — `term_renderer.Buffer`,
`mock.Buffer`, or hzeller's matrix canvas.
"""
import math
import time
from datetime import datetime, timezone

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
# Full-column markers for /compact and /clear. Sky blue is distinct from
# palette[2] cyan and palette[4] blue; violet is unused elsewhere.
BAR_COMPACT = (100, 180, 255)
BAR_COMPACT_DIM = (28, 50, 70)
BAR_CLEAR = (180, 100, 220)
BAR_CLEAR_DIM = (50, 28, 60)
# Awaiting-permission indicator (BLINK_ON_PERMISSIONS feature flag).
BAR_AMBER = (255, 180, 0)
HALF_TILE = TILE_H // 2  # code bar splits column at midline (4 px each side)
DIM_AFTER_S = 24 * 3600


def slot_origin(slot_idx: int) -> tuple[int, int]:
    col = slot_idx // ROWS
    row = slot_idx % ROWS
    return col * TILE_W, row * TILE_H


def _turn_epoch(turn: dict) -> int:
    # Prefer ts_epoch (set by the server). Fall back to parsing ts for any turn
    # whose payload predates the field — should be rare/none after one POST round.
    e = turn.get("ts_epoch")
    if isinstance(e, (int, float)):
        return int(e)
    ts = turn.get("ts") or ""
    s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    return int(datetime.fromisoformat(s).timestamp())


def _visible_turns(s: dict) -> list[dict]:
    # Match the renderer's real column budget. Pending animation consumes one
    # column, and a turn with code changes consumes a second column. Merely
    # taking the newest HIST_W turns can include off-screen outliers and
    # compress every white bar that is actually visible.
    visible = []
    col = 1 if s.get("pending") else 0
    for turn in sorted(s.get("turns") or [], key=_turn_epoch, reverse=True):
        if col >= HIST_W:
            break
        visible.append(turn)
        col += 1
        kind = turn.get("kind") or "turn"
        has_code = (
            (turn.get("added", 0) or 0) > 0
            or (turn.get("removed", 0) or 0) > 0
            or bool(turn.get("added_any"))
            or bool(turn.get("removed_any"))
        )
        if kind == "turn" and has_code and col < HIST_W:
            col += 1
    return visible


def _global_max_tokens(sessions: list[dict]) -> int:
    return max(
        (t["tokens"] for s in sessions for t in _visible_turns(s)),
        default=0,
    )


def _global_max_changes(sessions: list[dict]) -> tuple[int, int]:
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


def _bar_height(tokens: int, max_tokens: int) -> int:
    # sqrt scale: gives small turns a visible bar without flattening outliers
    # the way log does — typical 5-30k turns spread across heights 1-3 while
    # 100k+ turns still pop to 6-8.
    if tokens <= 0 or max_tokens <= 0:
        return 0
    ratio = math.sqrt(tokens / max_tokens)
    return max(1, round(ratio * TILE_H))


def _half_bar_height(value: int, max_value: int) -> int:
    # sqrt scale into the upper or lower half of the column. Min 1px floor when
    # value > 0 so even tiny code changes register visibly.
    if value <= 0 or max_value <= 0:
        return 0
    ratio = math.sqrt(value / max_value)
    return max(1, round(ratio * HALF_TILE))


def _paint_code_bar(canvas, abs_x: int, y0: int,
                    added: int, removed: int,
                    added_any: bool, removed_any: bool,
                    max_added: int, max_removed: int,
                    dim: bool) -> None:
    # Both stack from the bottom: green block first, then red block on top of
    # green. Each independently sqrt-scaled and capped at HALF_TILE so the
    # combined max (4 + 4) just fills the column. A single-color turn (only
    # green, only red) still sits at the floor — no "hanging from the ceiling".
    # _any floors: when net added/removed is 0 but the turn DID touch added
    # or removed lines (e.g. a same-size in-place refactor), paint 1px so
    # the activity isn't invisible.
    h_green = _half_bar_height(added, max_added)
    if h_green == 0 and added_any:
        h_green = 1
    h_red = _half_bar_height(removed, max_removed)
    if h_red == 0 and removed_any:
        h_red = 1
    if h_green > 0:
        color = BAR_GREEN_DIM if dim else BAR_GREEN
        for dy in range(TILE_H - h_green, TILE_H):
            canvas.SetPixel(abs_x, y0 + dy, *color)
    if h_red > 0:
        color = BAR_RED_DIM if dim else BAR_RED
        for dy in range(TILE_H - h_green - h_red, TILE_H - h_green):
            canvas.SetPixel(abs_x, y0 + dy, *color)


def render_frame(canvas, sessions: list[dict]) -> None:
    max_tokens = _global_max_tokens(sessions)
    max_added, max_removed = _global_max_changes(sessions)
    now_epoch = int(datetime.now(timezone.utc).timestamp())
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
        # Newest at column 0, fading off to the right as turns age. Each turn
        # emits a token bar (always) followed by an optional code bar (when
        # added/removed > 0). The token bar separator is what guarantees no
        # two code bars are adjacent.
        turns_by_ts = sorted(turns, key=_turn_epoch, reverse=True)
        col = col_offset
        for turn in turns_by_ts:
            if col >= HIST_W:
                break
            age = now_epoch - _turn_epoch(turn)
            dim = age > DIM_AFTER_S
            kind = turn.get("kind") or "turn"
            if kind == "compact" or kind == "clear":
                # Full-column marker — fills the entire histogram column.
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
            added_any = bool(turn.get("added_any"))
            removed_any = bool(turn.get("removed_any"))
            if (added > 0 or removed > 0 or added_any or removed_any) and col < HIST_W:
                _paint_code_bar(canvas, x0 + COLOR_W + col, y0,
                                added, removed, added_any, removed_any,
                                max_added, max_removed, dim)
                col += 1

        if pending:
            # --- BLINK_ON_PERMISSIONS feature gate ---
            # When the session is blocked on a permission prompt, replace the
            # rising white bar with a full-column amber bar that blinks on/off
            # so it reads as "stuck on you" vs. "still chugging".
            awaiting = BLINK_ON_PERMISSIONS and bool(s.get("awaiting"))
            if awaiting:
                if int(time.time() * 2) % 2 == 0:
                    for dy in range(TILE_H):
                        canvas.SetPixel(x0 + COLOR_W + 0, y0 + dy, *BAR_AMBER)
            # --- end BLINK_ON_PERMISSIONS ---
            else:
                # Animated rising bar at column 0: cycle 1->TILE_H over ~4
                # seconds, one step per 500ms render tick.
                h = int(time.time() * 2) % TILE_H + 1
                for dy in range(TILE_H - h, TILE_H):
                    canvas.SetPixel(x0 + COLOR_W + 0, y0 + dy, *BAR_BRIGHT)
