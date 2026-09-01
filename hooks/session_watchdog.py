#!/usr/bin/env python3
"""Close an airgbmatrix session when its owning CLI process disappears."""

import json
import fcntl
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

POLL_SECONDS = 2
RETRY_SECONDS = 5
LOCK_RETRY_SECONDS = 0.1
LOCK_HANDOFF_SECONDS = 10


def _process_info(proc_root: Path, pid: int):
    try:
        stat = (proc_root / str(pid) / "stat").read_text()
        tail = stat[stat.rfind(")") + 2 :].split()
        state = tail[0]
        ppid = int(tail[1])
        start_time = tail[19]
        argv = (
            (proc_root / str(pid) / "cmdline")
            .read_bytes()
            .decode(errors="replace")
            .split("\0")
        )
        return ppid, state, start_time, [arg for arg in argv if arg]
    except (OSError, ValueError, IndexError):
        return None


def _is_cli(argv: list[str]) -> bool:
    for arg in argv[:3]:
        name = Path(arg).name.lower()
        if name in {"copilot", "copilot.exe", "claude", "claude.exe"}:
            return True
    return False


def find_owner(proc_root: Path, start_pid: int):
    pid = start_pid
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        info = _process_info(proc_root, pid)
        if info is None:
            return None
        ppid, _state, start_time, argv = info
        if _is_cli(argv):
            return pid, start_time
        pid = ppid
    return None


def _owner_generation(proc_root: Path, identity) -> str:
    try:
        boot_id = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot_id = "unknown"
    return f"{boot_id}:{identity[1]}:{identity[0]}"


def _marker_identity(marker: Path):
    try:
        pid_text, start_time = marker.read_text().strip().split(" ", 1)
        return int(pid_text), start_time
    except (OSError, ValueError):
        return None


def _is_same_process(proc_root: Path, identity) -> bool:
    pid, expected_start_time = identity
    info = _process_info(proc_root, pid)
    return (
        info is not None
        and info[1] not in {"Z", "X"}
        and info[2] == expected_start_time
    )


def _write_lock_identity(lock_handle, identity) -> None:
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(f"{identity[0]} {identity[1]}\n")
    lock_handle.flush()


def _lock_identity(lock: Path):
    return _marker_identity(lock)


def _claim_lock(lock_handle, lock: Path, identity) -> bool:
    deadline = time.monotonic() + LOCK_HANDOFF_SECONDS
    while True:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _write_lock_identity(lock_handle, identity)
            return True
        except BlockingIOError:
            # A same-owner watcher already monitors this process. A resumed
            # owner waits only while the lock still advertises the old owner,
            # preventing a narrow unlock handoff from dropping all watchers.
            if _lock_identity(lock) == identity:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(LOCK_RETRY_SECONDS)


def _post_closed(session_id: str, identity) -> bool:
    host = os.environ.get("CLAUDE_LED_HOST", "localhost")
    port = os.environ.get("CLAUDE_LED_PORT", "5000")
    body = json.dumps({
        "id": session_id,
        "state": "closed",
        "owner_generation": _owner_generation(Path("/proc"), identity),
    }).encode()
    request = urllib.request.Request(
        f"http://{host}:{port}/session",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            if not 200 <= response.status < 300:
                return False
            try:
                result = json.loads(response.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                return True
            return result.get("ignored") != "stale_owner"
    except (OSError, urllib.error.URLError):
        return False


def watch(proc_root: Path, identity, session_id: str, marker: Path, lock: Path) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock.open("a+")
    try:
        if not _claim_lock(lock_handle, lock, identity):
            return

        while True:
            latest = _marker_identity(marker)
            if latest is not None and latest != identity:
                identity = latest
                _write_lock_identity(lock_handle, identity)
            if _is_same_process(proc_root, identity):
                time.sleep(POLL_SECONDS)
                continue

            # Allow a resumed process to replace the marker before deleting the
            # shared board record.
            time.sleep(POLL_SECONDS)
            latest = _marker_identity(marker)
            if latest is not None and latest != identity:
                identity = latest
                _write_lock_identity(lock_handle, identity)
                continue
            if _post_closed(session_id, identity):
                latest = _marker_identity(marker)
                if (
                    latest is not None
                    and latest != identity
                    and _is_same_process(proc_root, latest)
                ):
                    identity = latest
                    _write_lock_identity(lock_handle, identity)
                    continue
                break
            time.sleep(RETRY_SECONDS)
    finally:
        latest = _marker_identity(marker)
        if latest is not None and not _is_same_process(proc_root, latest):
            try:
                marker.unlink()
            except OSError:
                pass
        lock_handle.close()


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: session_watchdog.py owner START_PID | "
            "watch PID START_TIME SESSION_ID MARKER LOCK"
        )

    command = sys.argv[1]
    proc_root = Path(os.environ.get("AIRGBMATRIX_PROC_ROOT", "/proc"))
    if command == "owner" and len(sys.argv) == 3:
        owner = find_owner(proc_root, int(sys.argv[2]))
        if owner is not None:
            print(f"{owner[0]} {owner[1]} {_owner_generation(proc_root, owner)}")
        return
    if command == "watch" and len(sys.argv) == 7:
        watch(
            proc_root,
            (int(sys.argv[2]), sys.argv[3]),
            sys.argv[4],
            Path(sys.argv[5]),
            Path(sys.argv[6]),
        )
        return
    raise SystemExit("invalid watchdog arguments")


if __name__ == "__main__":
    main()
