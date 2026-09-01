import sys
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import server  # noqa: E402


class ServerMergeTests(unittest.TestCase):
    def setUp(self):
        self.original_sessions = server._sessions
        self.original_closed_generations = server._closed_generations
        self.original_persist = server._persist_locked
        server._sessions = {}
        server._closed_generations = {}
        server._persist_locked = lambda: None
        self.client = server.app.test_client()

    def tearDown(self):
        server._sessions = self.original_sessions
        server._closed_generations = self.original_closed_generations
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
                "token_source": "copilot-usage",
            }
        )
        self.assertEqual(corrected["metrics_version"], 2)
        self.assertEqual(corrected["tokens"], 80)
        self.assertEqual((corrected["added"], corrected["removed"]), (0, 0))
        self.assertFalse(corrected["added_any"])
        self.assertFalse(corrected["removed_any"])
        self.assertEqual(corrected["token_source"], "copilot-usage")

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

    def test_parser_upgrade_prunes_turns_omitted_from_new_snapshot(self):
        self.post_turn(
            {
                "msg_id": "real-turn",
                "metrics_version": 3,
                "tokens": 80,
                "ts": "2026-01-01T00:00:00Z",
            }
        )
        self.post_turn(
            {
                "msg_id": "obsolete-subagent-turn",
                "metrics_version": 3,
                "tokens": 800,
                "ts": "2026-01-01T00:00:01Z",
            }
        )

        response = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "turns": [
                    {
                        "msg_id": "real-turn",
                        "metrics_version": 4,
                        "tokens": 70,
                        "ts": "2026-01-01T00:00:02Z",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        turns = server._sessions["session-1"]["turns"]
        self.assertEqual([turn["msg_id"] for turn in turns], ["real-turn"])
        self.assertEqual(turns[0]["tokens"], 70)
        self.assertEqual(server._sessions["session-1"]["metrics_version"], 4)

        stale_response = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "turns": [
                    {
                        "msg_id": "real-turn",
                        "metrics_version": 3,
                        "tokens": 80,
                        "ts": "2026-01-01T00:00:00Z",
                    },
                    {
                        "msg_id": "obsolete-subagent-turn",
                        "metrics_version": 3,
                        "tokens": 800,
                        "ts": "2026-01-01T00:00:01Z",
                    },
                ],
            },
        )

        self.assertEqual(stale_response.status_code, 200)
        turns = server._sessions["session-1"]["turns"]
        self.assertEqual([turn["msg_id"] for turn in turns], ["real-turn"])
        self.assertEqual(turns[0]["tokens"], 70)

    def test_exact_turn_survives_fallback_while_new_proxy_turn_is_accepted(self):
        exact_response = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "turns": [
                    {
                        "msg_id": "exact-turn",
                        "metrics_version": 5,
                        "token_source": "copilot-usage",
                        "tokens": 40,
                        "ts": "2026-01-01T00:00:00Z",
                    }
                ],
            },
        )
        self.assertEqual(exact_response.status_code, 200)

        fallback_response = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "turns": [
                    {
                        "msg_id": "exact-turn",
                        "metrics_version": 5,
                        "token_source": "content-proxy",
                        "tokens": 400,
                        "ts": "2026-01-01T00:00:01Z",
                    },
                    {
                        "msg_id": "new-proxy-turn",
                        "metrics_version": 5,
                        "token_source": "content-proxy",
                        "tokens": 20,
                        "ts": "2026-01-01T00:00:02Z",
                    },
                ],
            },
        )

        self.assertEqual(fallback_response.status_code, 200)
        turns = {
            turn["msg_id"]: turn
            for turn in server._sessions["session-1"]["turns"]
        }
        self.assertEqual(turns["exact-turn"]["tokens"], 40)
        self.assertEqual(turns["exact-turn"]["token_source"], "copilot-usage")
        self.assertEqual(turns["new-proxy-turn"]["tokens"], 20)
        self.assertEqual(
            turns["new-proxy-turn"]["token_source"],
            "content-proxy",
        )

    def test_stale_owner_cannot_close_resumed_session(self):
        response = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "owner_generation": "boot-a:800:20",
            },
        )
        self.assertEqual(response.status_code, 200)

        resumed = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "owner_generation": "boot-a:900:30",
            },
        )
        self.assertEqual(resumed.status_code, 200)

        stale_close = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "owner_generation": "boot-a:800:20",
            },
        )
        self.assertEqual(stale_close.status_code, 200)
        self.assertEqual(stale_close.get_json()["ignored"], "stale_owner")
        self.assertIn("session-1", server._sessions)

        current_close = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "owner_generation": "boot-a:900:30",
            },
        )
        self.assertEqual(current_close.status_code, 200)
        self.assertNotIn("session-1", server._sessions)

    def test_stale_state_update_cannot_roll_owner_generation_back(self):
        for state, generation in (
            ("working", "boot-a:800:20"),
            ("working", "boot-a:900:30"),
            ("stopped", "boot-a:800:20"),
        ):
            response = self.client.post(
                "/session",
                json={
                    "id": "session-1",
                    "state": state,
                    "owner_generation": generation,
                },
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            server._sessions["session-1"]["owner_generation"],
            "boot-a:900:30",
        )
        self.assertEqual(server._sessions["session-1"]["state"], "working")

        close = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "owner_generation": "boot-a:900:30",
            },
        )
        self.assertEqual(close.status_code, 200)
        self.assertNotIn("session-1", server._sessions)

    def test_newer_owner_can_close_record_when_its_start_update_was_lost(self):
        started = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "owner_generation": "boot-a:800:20",
            },
        )
        self.assertEqual(started.status_code, 200)

        newer_close = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "owner_generation": "boot-a:900:30",
            },
        )
        self.assertEqual(newer_close.status_code, 200)
        self.assertNotIn("session-1", server._sessions)

    def test_incremental_marker_survives_versioned_turn_snapshot(self):
        self.post_turn(
            {
                "msg_id": "turn-1",
                "metrics_version": 5,
                "tokens": 100,
                "ts": "2026-01-01T00:00:00Z",
                "added": 0,
                "removed": 0,
            }
        )
        marker = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "turns": [
                    {
                        "msg_id": "compact-1",
                        "kind": "compact",
                        "tokens": 0,
                        "ts": "2026-01-01T00:00:01Z",
                    }
                ],
            },
        )
        self.assertEqual(marker.status_code, 200)
        self.assertEqual(
            [turn["msg_id"] for turn in server._sessions["session-1"]["turns"]],
            ["turn-1", "compact-1"],
        )

        upgraded = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "turns": [
                    {
                        "msg_id": "turn-1",
                        "metrics_version": 6,
                        "tokens": 90,
                        "ts": "2026-01-01T00:00:00Z",
                        "added": 0,
                        "removed": 0,
                    }
                ],
            },
        )
        self.assertEqual(upgraded.status_code, 200)
        self.assertEqual(
            [turn["msg_id"] for turn in server._sessions["session-1"]["turns"]],
            ["turn-1", "compact-1"],
        )

    def test_delayed_state_update_cannot_resurrect_closed_session(self):
        generation = "boot-a:800:20"
        self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "lifecycle": "working",
                "owner_generation": generation,
            },
        )
        self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "lifecycle": "closed",
                "owner_generation": generation,
            },
        )
        delayed = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "lifecycle": "stopped",
                "owner_generation": generation,
            },
        )

        self.assertEqual(delayed.get_json()["ignored"], "closed_owner")
        self.assertNotIn("session-1", server._sessions)

    def test_new_owner_start_supersedes_closed_session_tombstone(self):
        server._closed_generations["session-1"] = {
            "owner_generation": "boot-a:800:20",
            "closed_at": 1,
        }
        resumed = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "lifecycle": "working",
                "owner_generation": "boot-a:900:30",
            },
        )

        self.assertEqual(resumed.status_code, 200)
        self.assertIn("session-1", server._sessions)
        self.assertNotIn("session-1", server._closed_generations)

    def test_stale_close_cannot_downgrade_newer_tombstone(self):
        server._closed_generations["session-1"] = {
            "owner_generation": "boot-a:900:30",
            "closed_at": 1,
        }
        stale = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "lifecycle": "closed",
                "owner_generation": "boot-a:800:20",
            },
        )

        self.assertEqual(stale.get_json()["ignored"], "stale_owner")
        self.assertEqual(
            server._closed_generations["session-1"]["owner_generation"],
            "boot-a:900:30",
        )

    def test_previous_boot_close_cannot_delete_explicitly_started_owner(self):
        self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "lifecycle": "working",
                "owner_generation": "boot-new:900:30",
            },
        )
        old_close = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "lifecycle": "closed",
                "owner_generation": "boot-old:800:20",
            },
        )

        self.assertEqual(old_close.get_json()["ignored"], "stale_owner")
        self.assertIn("session-1", server._sessions)

    def test_state_and_tombstones_persist_in_one_atomic_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_state_path = server.STATE_PATH
            original_tombstone_path = server.TOMBSTONE_PATH
            try:
                server.STATE_PATH = str(Path(tmp) / "state.json")
                server.TOMBSTONE_PATH = server.STATE_PATH + ".closed"
                server._sessions["session-1"] = {
                    "id": "session-1",
                    "state": "stopped",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "turns": [],
                    "pending": False,
                }
                server._closed_generations["closed-1"] = {
                    "owner_generation": "boot-a:800:20",
                    "closed_at": 1,
                }

                self.original_persist()
                envelope = json.loads(Path(server.STATE_PATH).read_text())
                loaded_sessions, loaded_closed = server._load_state()

                self.assertEqual(envelope["format_version"], 2)
                self.assertIn("session-1", loaded_sessions)
                self.assertIn("closed-1", loaded_closed)
                self.assertFalse(Path(server.TOMBSTONE_PATH).exists())
            finally:
                server.STATE_PATH = original_state_path
                server.TOMBSTONE_PATH = original_tombstone_path

    def test_ownerless_close_tombstone_rejects_delayed_state(self):
        self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "lifecycle": "working",
            },
        )
        self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "closed",
                "lifecycle": "closed",
            },
        )
        delayed = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "stopped",
                "lifecycle": "stopped",
            },
        )

        self.assertEqual(delayed.get_json()["ignored"], "closed_owner")
        self.assertNotIn("session-1", server._sessions)
        self.assertIn("session-1", server._closed_generations)

    def test_newer_legacy_working_payload_supersedes_tombstone(self):
        server._closed_generations["session-1"] = {
            "owner_generation": "boot-a:800:20",
            "closed_at": 1,
        }
        legacy_start = self.client.post(
            "/session",
            json={
                "id": "session-1",
                "state": "working",
                "owner_generation": "boot-a:900:30",
            },
        )

        self.assertEqual(legacy_start.status_code, 200)
        self.assertIn("session-1", server._sessions)
        self.assertNotIn("session-1", server._closed_generations)


if __name__ == "__main__":
    unittest.main()
