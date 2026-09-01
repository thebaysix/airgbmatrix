import fcntl
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hooks import session_watchdog


def write_process(proc_root, pid, ppid, start_time, argv, state="S"):
    proc = proc_root / str(pid)
    proc.mkdir()
    fields = [state, str(ppid)] + ["0"] * 17 + [str(start_time)] + ["0"] * 30
    (proc / "stat").write_text(f"{pid} (process) {' '.join(fields)}")
    (proc / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")


class SessionWatchdogTests(unittest.TestCase):
    def test_finds_nearest_cli_owner_through_hook_shells(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            write_process(proc_root, 100, 50, 1000, ["/repo/hooks/notify.sh"])
            write_process(proc_root, 50, 20, 900, ["/bin/sh", "-c", "notify.sh"])
            write_process(proc_root, 20, 10, 800, ["/opt/copilot", "--yolo"])
            write_process(proc_root, 10, 1, 700, ["agency", "copilot"])

            self.assertEqual(
                session_watchdog.find_owner(proc_root, 100),
                (20, "800"),
            )

    def test_process_identity_detects_exit_and_pid_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            write_process(proc_root, 20, 10, 800, ["/opt/copilot"])

            self.assertTrue(
                session_watchdog._is_same_process(proc_root, (20, "800"))
            )
            self.assertFalse(
                session_watchdog._is_same_process(proc_root, (20, "801"))
            )
            self.assertFalse(
                session_watchdog._is_same_process(proc_root, (21, "800"))
            )
            write_process(
                proc_root,
                21,
                10,
                800,
                ["/opt/copilot"],
                state="Z",
            )
            self.assertFalse(
                session_watchdog._is_same_process(proc_root, (21, "800"))
            )

    def test_existing_watch_lock_prevents_duplicate_watcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "session"
            lock = root / "session.watch"
            marker.write_text("20 800")
            lock.write_text("20 800")
            lock_handle = lock.open("a+")
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with mock.patch.object(
                    session_watchdog,
                    "_post_closed",
                ) as post_closed:
                    session_watchdog.watch(
                        root,
                        (20, "800"),
                        "session-id",
                        marker,
                        lock,
                        root / "close-queue",
                    )
                post_closed.assert_not_called()
            finally:
                lock_handle.close()

    def test_new_owner_waits_for_old_watcher_lock_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "session.watch.lock"
            old_handle = lock.open("a+")
            fcntl.flock(old_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            session_watchdog._write_lock_identity(old_handle, (20, "800"))

            def release_old_lock():
                time.sleep(0.05)
                old_handle.close()

            releaser = threading.Thread(target=release_old_lock)
            releaser.start()
            new_handle = lock.open("a+")
            try:
                self.assertTrue(
                    session_watchdog._claim_lock(
                        new_handle,
                        lock,
                        (30, "900"),
                    )
                )
                self.assertEqual(lock.read_text().strip(), "30 900")
            finally:
                new_handle.close()
                releaser.join()

    def test_resume_during_close_retry_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "session"
            lock = root / "session.watch"
            marker.write_text("20 800")
            sleeps = 0

            def fake_sleep(_seconds):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 2:
                    marker.write_text("30 900")
                elif sleeps == 3:
                    raise RuntimeError("stop after resumed owner is observed")

            def process_is_live(_root, identity):
                return identity == (30, "900")

            with (
                mock.patch.object(
                    session_watchdog,
                    "_is_same_process",
                    side_effect=process_is_live,
                ),
                mock.patch.object(
                    session_watchdog.time,
                    "sleep",
                    side_effect=fake_sleep,
                ),
                mock.patch.object(
                    session_watchdog,
                    "_post_closed",
                    return_value="failed",
                ) as post_closed,
            ):
                with self.assertRaisesRegex(RuntimeError, "resumed owner"):
                    session_watchdog.watch(
                        root,
                        (20, "800"),
                        "session-id",
                        marker,
                        lock,
                        root / "close-queue",
                    )

            self.assertEqual(post_closed.call_count, 1)
            self.assertEqual(marker.read_text(), "30 900")

    def test_successful_stale_close_hands_off_to_resumed_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "session"
            lock = root / "session.watch.lock"
            marker.write_text("20 800")
            sleeps = 0

            def fake_sleep(_seconds):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 2:
                    raise RuntimeError("stop after resumed owner is monitored")

            def process_is_live(_root, identity):
                return identity == (30, "900")

            def close_then_resume(_proc_root, _session_id, _identity):
                marker.write_text("30 900")
                return "closed"

            with (
                mock.patch.object(
                    session_watchdog,
                    "_is_same_process",
                    side_effect=process_is_live,
                ),
                mock.patch.object(
                    session_watchdog.time,
                    "sleep",
                    side_effect=fake_sleep,
                ),
                mock.patch.object(
                    session_watchdog,
                    "_post_closed",
                    side_effect=close_then_resume,
                ) as post_closed,
            ):
                with self.assertRaisesRegex(RuntimeError, "resumed owner"):
                    session_watchdog.watch(
                        root,
                        (20, "800"),
                        "session-id",
                        marker,
                        lock,
                        root / "close-queue",
                    )

            self.assertEqual(post_closed.call_count, 1)
            self.assertEqual(marker.read_text(), "30 900")

    def test_failed_close_is_queued_and_watcher_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "session"
            lock = root / "session.watch.lock"
            marker.write_text("20 800")

            with (
                mock.patch.object(
                    session_watchdog,
                    "_is_same_process",
                    return_value=False,
                ),
                mock.patch.object(session_watchdog.time, "sleep"),
                mock.patch.object(
                    session_watchdog,
                    "_post_closed",
                    return_value="failed",
                ) as post_closed,
                mock.patch.object(
                    session_watchdog,
                    "_owner_generation",
                    return_value="boot-a:800:20",
                ),
            ):
                session_watchdog.watch(
                    root,
                    (20, "800"),
                    "session-id",
                    marker,
                    lock,
                    root / "close-queue",
                )

            self.assertEqual(
                post_closed.call_count,
                session_watchdog.MAX_CLOSE_ATTEMPTS,
            )
            queued = list((root / "close-queue").glob("*.json"))
            self.assertEqual(len(queued), 1)
            self.assertEqual(
                json.loads(queued[0].read_text()),
                {
                    "id": "session-id",
                    "owner_generation": "boot-a:800:20",
                },
            )

    def test_queue_drain_removes_completed_and_keeps_failed_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp)
            session_watchdog._queue_close(
                queue_dir,
                "closed-session",
                "boot-a:800:20",
            )
            session_watchdog._queue_close(
                queue_dir,
                "retry-session",
                "boot-a:900:30",
            )

            def post_result(session_id, _generation):
                return "stale" if session_id == "closed-session" else "failed"

            with mock.patch.object(
                session_watchdog,
                "_post_generation",
                side_effect=post_result,
            ):
                session_watchdog.drain_queue(queue_dir)

            queued_ids = {
                json.loads(path.read_text())["id"]
                for path in queue_dir.glob("*.json")
            }
            self.assertEqual(queued_ids, {"retry-session"})

    def test_queue_drain_does_not_unlink_concurrent_new_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue_dir = Path(tmp)
            session_watchdog._queue_close(
                queue_dir,
                "session-id",
                "boot-old:800:20",
            )

            def replace_during_post(_session_id, _generation):
                session_watchdog._queue_close(
                    queue_dir,
                    "session-id",
                    "boot-new:900:30",
                )
                return "stale"

            with mock.patch.object(
                session_watchdog,
                "_post_generation",
                side_effect=replace_during_post,
            ):
                session_watchdog.drain_queue(queue_dir)

            generations = {
                json.loads(path.read_text())["owner_generation"]
                for path in queue_dir.glob("*.json")
            }
            self.assertEqual(generations, {"boot-new:900:30"})


if __name__ == "__main__":
    unittest.main()
