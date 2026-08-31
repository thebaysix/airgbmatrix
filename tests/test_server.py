import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import server  # noqa: E402


class ServerMergeTests(unittest.TestCase):
    def setUp(self):
        self.original_sessions = server._sessions
        self.original_persist = server._persist_locked
        server._sessions = {}
        server._persist_locked = lambda: None
        self.client = server.app.test_client()

    def tearDown(self):
        server._sessions = self.original_sessions
        server._persist_locked = self.original_persist

    def post_turn(self, turn):
        response = self.client.post(
            "/session",
            json={"id": "session-1", "state": "stopped", "turns": [turn]},
        )
        self.assertEqual(response.status_code, 200)
        return server._sessions["session-1"]["turns"][0]

    def test_new_metrics_version_replaces_bad_persisted_counts(self):
        old = self.post_turn(
            {
                "msg_id": "turn-1",
                "tokens": 100,
                "ts": "2026-01-01T00:00:00Z",
                "added": 9,
                "removed": 7,
                "added_any": True,
                "removed_any": True,
            }
        )
        self.assertEqual((old["added"], old["removed"]), (9, 7))

        corrected = self.post_turn(
            {
                "msg_id": "turn-1",
                "metrics_version": 2,
                "tokens": 80,
                "ts": "2026-01-01T00:00:01Z",
                "added": 0,
                "removed": 0,
                "added_any": False,
                "removed_any": False,
            }
        )
        self.assertEqual(corrected["metrics_version"], 2)
        self.assertEqual(corrected["tokens"], 80)
        self.assertEqual((corrected["added"], corrected["removed"]), (0, 0))
        self.assertFalse(corrected["added_any"])
        self.assertFalse(corrected["removed_any"])

    def test_older_metrics_version_cannot_reinflate_corrected_counts(self):
        self.post_turn(
            {
                "msg_id": "turn-1",
                "metrics_version": 2,
                "tokens": 80,
                "ts": "2026-01-01T00:00:00Z",
                "added": 0,
                "removed": 0,
            }
        )
        preserved = self.post_turn(
            {
                "msg_id": "turn-1",
                "metrics_version": 1,
                "tokens": 800,
                "ts": "2026-01-01T00:00:01Z",
                "added": 20,
                "removed": 10,
                "added_any": True,
                "removed_any": True,
            }
        )
        self.assertEqual(preserved["tokens"], 80)
        self.assertEqual((preserved["added"], preserved["removed"]), (0, 0))
        self.assertFalse(preserved["added_any"])
        self.assertFalse(preserved["removed_any"])

    def test_same_metrics_version_still_max_merges_transcript_backfill(self):
        self.post_turn(
            {
                "msg_id": "turn-1",
                "metrics_version": 2,
                "tokens": 80,
                "ts": "2026-01-01T00:00:00Z",
                "added": 2,
                "removed": 1,
            }
        )
        merged = self.post_turn(
            {
                "msg_id": "turn-1",
                "metrics_version": 2,
                "tokens": 100,
                "ts": "2026-01-01T00:00:01Z",
                "added": 4,
                "removed": 3,
                "added_any": True,
                "removed_any": True,
            }
        )
        self.assertEqual(merged["tokens"], 100)
        self.assertEqual((merged["added"], merged["removed"]), (4, 3))
        self.assertTrue(merged["added_any"])
        self.assertTrue(merged["removed_any"])


if __name__ == "__main__":
    unittest.main()
