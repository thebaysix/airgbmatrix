import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTIFY = ROOT / "hooks" / "notify.sh"
TINT = ROOT / "hooks" / "tint_terminal.sh"


def event(event_type, event_id, timestamp, data):
    return {
        "type": event_type,
        "id": event_id,
        "timestamp": timestamp,
        "parentId": None,
        "data": data,
    }


class HookTests(unittest.TestCase):
    def run_notify(self, transcript_lines, hook_input=None, transcript_sid=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcript_dir = tmp_path
            if transcript_sid:
                transcript_dir = tmp_path / transcript_sid
                transcript_dir.mkdir()
            transcript = transcript_dir / "events.jsonl"
            transcript.write_text(
                "".join(json.dumps(line) + "\n" for line in transcript_lines)
            )
            capture = tmp_path / "payload.json"
            fake_curl = tmp_path / "curl"
            fake_curl.write_text(
                "#!/usr/bin/env bash\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = \"-d\" ]; then\n"
                "    shift\n"
                "    printf '%s' \"$1\" > \"$AIRGB_CAPTURE\"\n"
                "  fi\n"
                "  shift\n"
                "done\n"
                "printf '{\"ok\":true}'\n"
            )
            fake_curl.chmod(0o755)
            payload = hook_input or {
                "sessionId": "session-1",
                "transcriptPath": str(transcript),
            }
            if "transcript_path" in payload:
                payload["transcript_path"] = str(transcript)
            else:
                payload.setdefault("transcriptPath", str(transcript))
            env = os.environ.copy()
            env["PATH"] = f"{tmp_path}:{env['PATH']}"
            env["AIRGB_CAPTURE"] = str(capture)
            subprocess.run(
                [str(NOTIFY), "stopped"],
                input=json.dumps(payload),
                text=True,
                env=env,
                check=True,
                capture_output=True,
            )
            return json.loads(capture.read_text())

    def test_copilot_auxiliary_stop_uses_parent_transcript_session_id(self):
        parent_sid = "bc1720c4-c553-4ade-895d-4e63df56a8fd"
        lines = [
            event(
                "user.message",
                "user-1",
                "2026-01-01T00:00:00Z",
                {"content": "prompt", "transformedContent": "prompt"},
            )
        ]

        payload = self.run_notify(
            lines,
            {"sessionId": "550d1419-7e34-4889-9ad2-cc2f8d0ef98a"},
            transcript_sid=parent_sid,
        )
        self.assertEqual(payload["id"], parent_sid)

    def test_copilot_counts_only_successful_file_mutation_diffs(self):
        lines = [
            event("system.message", "sys", "2026-01-01T00:00:00Z", {"content": "x" * 40}),
            event(
                "user.message",
                "user-1",
                "2026-01-01T00:00:01Z",
                {"content": "prompt", "transformedContent": "prompt plus context"},
            ),
            event(
                "tool.execution_start",
                "start-view",
                "2026-01-01T00:00:02Z",
                {"toolCallId": "view-1", "toolName": "view"},
            ),
            event(
                "tool.execution_complete",
                "done-view",
                "2026-01-01T00:00:03Z",
                {
                    "toolCallId": "view-1",
                    "success": True,
                    "result": {
                        "content": "file contents",
                        "detailedContent": (
                            "diff --git a/file b/file\n"
                            "@@ -0,0 +1,2 @@\n"
                            "+not a real addition\n"
                            "-not a real removal\n"
                        ),
                    },
                },
            ),
            event(
                "tool.execution_start",
                "start-bash",
                "2026-01-01T00:00:04Z",
                {"toolCallId": "bash-1", "toolName": "bash"},
            ),
            event(
                "tool.execution_complete",
                "done-bash",
                "2026-01-01T00:00:05Z",
                {
                    "toolCallId": "bash-1",
                    "success": True,
                    "result": {
                        "content": "---- table separator\n- ordinary output",
                        "detailedContent": "---- table separator\n- ordinary output",
                    },
                },
            ),
            event(
                "tool.execution_start",
                "start-patch",
                "2026-01-01T00:00:06Z",
                {"toolCallId": "patch-1", "toolName": "apply_patch"},
            ),
            event(
                "tool.execution_complete",
                "done-patch",
                "2026-01-01T00:00:07Z",
                {
                    "toolCallId": "patch-1",
                    "success": True,
                    "result": {
                        "content": "Updated file",
                        "detailedContent": (
                            "diff --git a/file b/file\n"
                            "--- a/file\n"
                            "+++ b/file\n"
                            "@@ -1,2 +1,3 @@\n"
                            "-old\n"
                            "+new\n"
                            "+extra\n"
                            " unchanged\n"
                        ),
                    },
                },
            ),
            event(
                "tool.execution_start",
                "start-create",
                "2026-01-01T00:00:08Z",
                {"toolCallId": "create-1", "toolName": "create"},
            ),
            event(
                "tool.execution_complete",
                "done-create",
                "2026-01-01T00:00:09Z",
                {
                    "toolCallId": "create-1",
                    "success": True,
                    "result": {
                        "content": "Created file",
                        "detailedContent": (
                            "diff --git a/new b/new\n"
                            "@@ -0,0 +1,2 @@\n"
                            "+one\n"
                            "+two\n"
                        ),
                    },
                },
            ),
            event(
                "tool.execution_start",
                "start-failed-edit",
                "2026-01-01T00:00:10Z",
                {"toolCallId": "edit-1", "toolName": "edit"},
            ),
            event(
                "tool.execution_complete",
                "done-failed-edit",
                "2026-01-01T00:00:11Z",
                {
                    "toolCallId": "edit-1",
                    "success": False,
                    "result": {
                        "content": "Failed",
                        "detailedContent": "@@ -1 +1 @@\n-old\n+new\n",
                    },
                },
            ),
            event("assistant.turn_end", "end-1", "2026-01-01T00:00:12Z", {}),
            event(
                "user.message",
                "user-2",
                "2026-01-01T00:00:13Z",
                {"content": "status only", "transformedContent": "status only"},
            ),
            event(
                "tool.execution_start",
                "start-bash-2",
                "2026-01-01T00:00:14Z",
                {"toolCallId": "bash-2", "toolName": "bash"},
            ),
            event(
                "tool.execution_complete",
                "done-bash-2",
                "2026-01-01T00:00:15Z",
                {
                    "toolCallId": "bash-2",
                    "success": True,
                    "result": {
                        "content": "+ output\n- output",
                        "detailedContent": "+ output\n- output",
                    },
                },
            ),
        ]

        payload = self.run_notify(lines)
        self.assertEqual(payload["state"], "stopped")
        self.assertFalse(payload["pending"])
        turns = payload["turns"]
        self.assertEqual([turn["msg_id"] for turn in turns], ["user-1", "user-2"])
        self.assertEqual((turns[0]["added"], turns[0]["removed"]), (4, 1))
        self.assertTrue(turns[0]["added_any"])
        self.assertTrue(turns[0]["removed_any"])
        self.assertEqual((turns[1]["added"], turns[1]["removed"]), (0, 0))
        self.assertFalse(turns[1]["added_any"])
        self.assertFalse(turns[1]["removed_any"])
        self.assertTrue(all(turn["metrics_version"] == 2 for turn in turns))

    def test_copilot_uses_exact_post_compaction_context_and_adds_marker(self):
        lines = [
            event("system.message", "sys", "2026-01-01T00:00:00Z", {"content": "x" * 400}),
            event(
                "user.message",
                "user-1",
                "2026-01-01T00:00:01Z",
                {"content": "prompt", "transformedContent": "prompt"},
            ),
            event(
                "session.compaction_complete",
                "compact-1",
                "2026-01-01T00:00:02Z",
                {"success": True, "postCompactionTokens": 1234},
            ),
            event(
                "assistant.message",
                "assistant-1",
                "2026-01-01T00:00:03Z",
                {"content": "y" * 400, "toolRequests": []},
            ),
        ]

        turns = self.run_notify(lines)["turns"]
        self.assertEqual(turns[0]["kind"], "compact")
        self.assertEqual(turns[0]["msg_id"], "copilot:compact-1")
        self.assertEqual(turns[1]["msg_id"], "user-1")
        self.assertEqual(turns[1]["tokens"], 1334)

    def test_claude_context_includes_cache_reads(self):
        lines = [
            {
                "type": "user",
                "uuid": "user-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "prompt"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {
                    "id": "assistant-1",
                    "content": [],
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_creation_input_tokens": 3,
                        "cache_read_input_tokens": 4,
                    },
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:02Z",
                "message": {
                    "id": "assistant-2",
                    "content": [],
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 2,
                        "cache_creation_input_tokens": 3,
                        "cache_read_input_tokens": 5,
                    },
                },
            },
        ]

        turn = self.run_notify(
            lines,
            {
                "session_id": "session-1",
                "transcript_path": "",
            },
        )["turns"][0]
        self.assertEqual(turn["tokens"], 12)
        self.assertEqual(turn["metrics_version"], 2)

    def test_tint_accepts_copilot_camel_case_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_curl = tmp_path / "curl"
            fake_curl.write_text(
                "#!/usr/bin/env bash\n"
                "printf '[{\"id\":\"session-1\",\"color_idx\":6}]'\n"
            )
            fake_curl.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{tmp_path}:{env['PATH']}"
            result = subprocess.run(
                [str(TINT)],
                input=json.dumps({"sessionId": "session-1"}),
                text=True,
                env=env,
                check=True,
                capture_output=True,
            )
            self.assertIn("\x1b]4;264;rgb:d2/2d/d2", result.stdout)


if __name__ == "__main__":
    unittest.main()
