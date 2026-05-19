import json
import os
import tempfile
from datetime import datetime, timezone
from threading import Lock

from flask import Flask, jsonify, request

from colors import PALETTE_SIZE
from features import BLINK_ON_PERMISSIONS

MAX_SLOTS = 8
# Stored capacity > rendered width on purpose: shrinking COLOR_W (which grows
# HIST_W) won't have to wait for fresh POSTs to backfill turn bars — the
# extras are already in state.json. Renderers cap display at HIST_W.
MAX_TURNS = 16
VALID_STATES = {"working", "stopped", "closed"}
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state.json")

app = Flask(__name__)
_lock = Lock()

# Append-only audit log of every color_idx mutation so that mysterious
# reassignments (e.g. a session unexpectedly bouncing between palette
# indices) can be traced after the fact. Set CLAUDERGBMATRIX_SERVER_LOG to
# override; tail the file to watch in real time.
SERVER_LOG = os.environ.get(
    "CLAUDERGBMATRIX_SERVER_LOG",
    os.path.join(tempfile.gettempdir(), "claudergbmatrix-server.log"),
)


def _audit(reason: str, sid: str, before, after, **extra) -> None:
    if before == after:
        return
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        parts = [ts, "color", reason, f"sid={sid[:8]}", f"{before}->{after}"]
        parts += [f"{k}={v}" for k, v in extra.items()]
        with open(SERVER_LOG, "a") as f:
            f.write(" ".join(parts) + "\n")
    except OSError:
        pass


def _epoch_from_iso(ts: str) -> int:
    # Server-emitted timestamps end in "+00:00"; transcript timestamps end in "Z".
    # fromisoformat handles "+00:00" natively (3.7+) and "Z" (3.11+).
    s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    return int(datetime.fromisoformat(s).timestamp())


def _nonneg_int(v) -> int:
    return v if isinstance(v, int) and v >= 0 else 0


def _assign_color_indices(sessions: dict) -> None:
    # Lease lowest-free palette index 0..PALETTE_SIZE-1 to each session that
    # lacks a valid one. Existing valid leases are preserved. With MAX_SLOTS ==
    # PALETTE_SIZE (== 8), there's always a free index after _trim_locked has
    # capped the session count.
    used = {
        s["color_idx"] for s in sessions.values()
        if isinstance(s.get("color_idx"), int) and 0 <= s["color_idx"] < PALETTE_SIZE
    }
    for s in sessions.values():
        ci = s.get("color_idx")
        if isinstance(ci, int) and 0 <= ci < PALETTE_SIZE:
            continue
        for i in range(PALETTE_SIZE):
            if i not in used:
                _audit("auto-assign", s.get("id", "?"), ci, i)
                s["color_idx"] = i
                used.add(i)
                break
        else:
            # All indices used and we still have an unassigned session — only
            # reachable if MAX_SLOTS > PALETTE_SIZE. Fall back to 0 rather than
            # crash; visual collision is recoverable, server crash isn't.
            _audit("fallback-zero", s.get("id", "?"), ci, 0)
            s["color_idx"] = 0


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
    except (OSError, ValueError):
        return {}
    # Backfill ts_epoch on turns persisted before that field existed, so the
    # rest of the code can assume it's always present.
    for sess in data.values():
        for t in sess.get("turns") or []:
            if "ts_epoch" not in t and t.get("ts"):
                try:
                    t["ts_epoch"] = _epoch_from_iso(t["ts"])
                except ValueError:
                    pass
    _assign_color_indices(data)
    return data


def _persist_locked() -> None:
    # Atomic write so a crash mid-write can't leave a half-written file.
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_sessions, f)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass  # disk errors shouldn't crash the server; live state is still in memory


_sessions: dict = _load_state()


def _trim_locked() -> None:
    if len(_sessions) <= MAX_SLOTS:
        return
    ordered = sorted(_sessions.values(), key=lambda s: s["updated_at"], reverse=True)
    keep = {s["id"] for s in ordered[:MAX_SLOTS]}
    for k in list(_sessions.keys()):
        if k not in keep:
            del _sessions[k]


def get_ordered_sessions() -> list[dict]:
    with _lock:
        return sorted(_sessions.values(), key=lambda s: s["updated_at"], reverse=True)


@app.post("/session")
def upsert_session():
    data = request.get_json(force=True, silent=True) or {}
    sid = data.get("id")
    state = data.get("state")
    incoming = data.get("turns")
    legacy_tokens = data.get("tokens")
    ts_default = data.get("ts") or datetime.now(timezone.utc).isoformat()
    if not sid or state not in VALID_STATES:
        return jsonify(error="id and valid state (working|stopped|closed) required"), 400
    # state=closed means SessionEnd (or reaper-equivalent): the session is no
    # longer "open" so we delete it from the board entirely. Frees the palette
    # lease so a new session can claim that color, and the tile clears.
    if state == "closed":
        with _lock:
            if sid in _sessions:
                _audit("close-delete", sid, _sessions[sid].get("color_idx"), None)
                del _sessions[sid]
                _persist_locked()
        return jsonify(ok=True)
    with _lock:
        existing = _sessions.get(sid, {})
        turns = list(existing.get("turns") or [])

        if isinstance(incoming, list):
            # Max-merge by msg_id (= user_uuid for the user-turn schema). For a
            # fixed user-turn, tokens/added/removed are monotonically
            # non-decreasing as the transcript flushes more assistant lines, so
            # max() is the natural merge — handles transcript-flush backfill
            # and is immune to any hypothetical re-POST that under-counts.
            #
            # Use `prev_turn` for the inner loop — NOT `existing`, which holds
            # the session-level record we still need below for color_idx and
            # pending preservation. (Earlier this loop shadowed `existing` and
            # silently nuked color leases on every Stop POST that shipped turns.)
            by_id: dict[str, dict] = {}
            for t in turns:
                mid = t.get("msg_id")
                if mid:
                    by_id[mid] = t
            for t in sorted(incoming, key=lambda x: x.get("ts") or ""):
                mid = t.get("msg_id")
                tok = t.get("tokens") or 0
                kind = t.get("kind") or "turn"
                if not mid or kind not in ("turn", "compact", "clear"):
                    continue
                # Regular turns must have positive tokens; markers (compact/
                # clear) are full-column events and skip the token check.
                if kind == "turn" and (not isinstance(tok, int) or tok <= 0):
                    continue
                ts = t.get("ts") or ts_default
                new_ts_epoch = _epoch_from_iso(ts)
                prev_turn = by_id.get(mid, {})
                old_ts_epoch = prev_turn.get("ts_epoch") or 0
                by_id[mid] = {
                    "msg_id": mid,
                    "kind": kind,
                    "tokens": max(prev_turn.get("tokens", 0), tok if isinstance(tok, int) else 0),
                    "ts": ts if new_ts_epoch >= old_ts_epoch else prev_turn.get("ts", ts),
                    "ts_epoch": max(old_ts_epoch, new_ts_epoch),
                    "added": max(prev_turn.get("added") or 0, _nonneg_int(t.get("added"))),
                    "removed": max(prev_turn.get("removed") or 0, _nonneg_int(t.get("removed"))),
                    # _any flags survive once set — if any assistant call in a
                    # user-turn added/removed lines (even net-zero refactors),
                    # the flag stays true so the renderer floors are visible
                    # across re-POSTs.
                    "added_any": bool(prev_turn.get("added_any")) or bool(t.get("added_any")),
                    "removed_any": bool(prev_turn.get("removed_any")) or bool(t.get("removed_any")),
                }
            turns = sorted(by_id.values(), key=lambda x: x.get("ts") or "")[-MAX_TURNS:]
        elif isinstance(legacy_tokens, int) and legacy_tokens > 0:
            turns.append({
                "tokens": legacy_tokens,
                "ts": ts_default,
                "ts_epoch": _epoch_from_iso(ts_default),
                "added": 0,
                "removed": 0,
            })
            turns = turns[-MAX_TURNS:]

        pending = data.get("pending")
        if pending is None:
            pending = bool(existing.get("pending", False))
        else:
            pending = bool(pending)

        new_record = {
            "id": sid,
            "state": state,
            "updated_at": ts_default,
            "turns": turns,
            "pending": pending,
        }
        # --- BLINK_ON_PERMISSIONS feature gate ---
        # When enabled, accept and persist an `awaiting` flag (true while the
        # session is blocked on a permission prompt). When disabled, the flag
        # is ignored on input and never stored.
        if BLINK_ON_PERMISSIONS:
            awaiting = data.get("awaiting")
            if awaiting is None:
                awaiting = bool(existing.get("awaiting", False))
            else:
                awaiting = bool(awaiting)
            new_record["awaiting"] = awaiting
        # --- end BLINK_ON_PERMISSIONS ---
        # Preserve an existing palette lease so a session keeps its color for
        # its lifetime; a brand-new session gets one assigned below.
        existing_ci = existing.get("color_idx")
        if isinstance(existing_ci, int) and 0 <= existing_ci < PALETTE_SIZE:
            new_record["color_idx"] = existing_ci
        else:
            _audit("upsert-new", sid, None, "<auto>", state=state)
        _sessions[sid] = new_record
        _trim_locked()
        _assign_color_indices(_sessions)
        _persist_locked()
    return jsonify(ok=True)


@app.get("/sessions")
def list_sessions():
    return jsonify(get_ordered_sessions())


@app.post("/claim-color")
def claim_color():
    """Manually claim a palette index for a session. If another session is
    currently using that index, it gets rotated to the lowest free one so the
    8 visible swatches stay distinct. Used by the `/claudergb-setcolor` skill.

    Request:  {"session_id": str, "color_idx": int 0..PALETTE_SIZE-1}
    Response: {"ok": true,
               "assigned": int,
               "bumped": null | {"session_id": str, "color_idx": int}}
    """
    data = request.get_json(force=True, silent=True) or {}
    sid = data.get("session_id")
    requested = data.get("color_idx")
    if not sid or not isinstance(requested, int) or not (0 <= requested < PALETTE_SIZE):
        return jsonify(error=f"session_id and color_idx (0..{PALETTE_SIZE - 1}) required"), 400
    with _lock:
        if sid not in _sessions:
            return jsonify(error="unknown session_id"), 404
        if _sessions[sid].get("color_idx") == requested:
            return jsonify(ok=True, assigned=requested, bumped=None)
        # Find the session currently holding the requested idx (if any).
        bumped_sid = next(
            (other_sid for other_sid, other in _sessions.items()
             if other_sid != sid and other.get("color_idx") == requested),
            None,
        )
        prev = _sessions[sid].get("color_idx")
        _audit("claim", sid, prev, requested, bumped=(bumped_sid[:8] if bumped_sid else "none"))
        _sessions[sid]["color_idx"] = requested
        bumped_response = None
        if bumped_sid is not None:
            # Clear the bumped session's lease so _assign_color_indices picks
            # the lowest free idx for it.
            _sessions[bumped_sid]["color_idx"] = None
            _assign_color_indices(_sessions)
            bumped_response = {
                "session_id": bumped_sid,
                "color_idx": _sessions[bumped_sid].get("color_idx"),
            }
        _persist_locked()
    return jsonify(ok=True, assigned=requested, bumped=bumped_response)


if __name__ == "__main__":
    # No auth: binds 0.0.0.0 so the S3 (or other LAN devices) can reach it via
    # WSL mirrored networking. Anyone on the LAN can read /sessions and inject
    # arbitrary state. Don't expose this to the public internet.
    app.run(host="0.0.0.0", port=5000)
