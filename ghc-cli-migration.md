# Copilot CLI integration notes

The Copilot CLI migration is complete. airgbmatrix remains dual-tool: the
same hook scripts accept Copilot CLI's camelCase payloads and Claude Code's
snake_case payloads, while each engine keeps its own hook configuration.

## Hook configuration

Copilot CLI loads user-global hooks from `~/.copilot/hooks/*.json`. Start from
`hooks/copilot-hooks.json.example`; replace the absolute script paths and set
`CLAUDE_LED_HOST` to the state server. Copilot loads hook changes at startup,
so restart the CLI after editing the file.

Claude Code continues to load `~/.claude/settings.json`; start from
`hooks/settings.json.example`. The event mapping is:

| Copilot CLI | Claude Code | airgbmatrix action |
|---|---|---|
| `sessionStart` | `SessionStart` | `notify.sh working`, `tint_terminal.sh` |
| `userPromptSubmitted` | `UserPromptSubmit` | `notify.sh pending` |
| `agentStop` | `Stop` | `notify.sh stopped` |
| `sessionEnd` | `SessionEnd` | `notify.sh closed` |

Closing a terminal tab may kill the CLI before `sessionEnd` can run.
`notify.sh` therefore starts one detached `session_watchdog.py` process per
real session. The watcher follows the nearest Copilot/Claude ancestor using
its PID plus Linux process start time, follows a replacement PID after resume,
and posts `closed` when the owner disappears. State updates carry the same
owner generation, allowing the server to reject a delayed close from a
pre-resume process. The server retains a bounded generation tombstone so a
request already in flight cannot recreate a closed session. If the server
stays unavailable, the watcher writes the close to a durable user-state queue
and exits; future lifecycle hooks retry queued closes. This is a Linux/WSL
liveness lease rather than an age timeout, so an open idle tab remains on the
board indefinitely.

Copilot's native hook payload uses `sessionId` and `transcriptPath`; Claude's
uses `session_id` and `transcript_path`. Both accessors are accepted.
Auxiliary Copilot agents can report a transient `sessionId` while sharing the
parent transcript; for stopped events, the UUID containing `events.jsonl` is
the canonical board identity. A mismatched auxiliary stop is ignored rather
than redirected, because redirecting it would mark the still-running parent
session as stopped.

YAML subagents also inherit `userPromptSubmitted`, but that payload has no
transcript path and the subagent never receives `sessionStart` or `sessionEnd`.
The hook records IDs seen at `sessionStart` in a runtime directory, with
`${COPILOT_HOME:-~/.copilot}/session-state` as an initial-prompt fallback.
Unregistered, non-owning prompt IDs are ignored, preventing an empty pending
tile from surviving after the subagent exits. This registration also supports
Copilot's `--config-dir` override after `sessionStart`.

The shared transcript includes the subagent's own `user.message`, assistant,
and tool events. airgbmatrix recognizes `source: "agent-..."`,
`interactionId`, and `parentToolCallId` metadata and excludes those streams
from the parent session's turn and token histograms.

## Transcript contracts

### Copilot CLI

Copilot persists `events.jsonl` records with `{type,data,id,timestamp,parentId}`.
airgbmatrix groups records by `user.message`.

When Agency's `${COPILOT_HOME:-~/.copilot}/session-store.db` is available,
airgbmatrix reads `assistant_usage_events` and sums the parent agent's exact
cache-excluded usage for each turn:

```text
max(input_tokens - cache_read_tokens, 0) + output_tokens
```

Rows with `parent_tool_call_id` are subagent calls and are excluded. Exact
turns carry `token_source: "copilot-usage"`.

Without the session store, `assistant.usage` is ephemeral and unavailable to
an `agentStop` hook. The portable fallback therefore uses a proxy built only
from persisted model-visible content within that user turn:

- transformed user prompt content
- assistant content, reasoning text, and tool requests
- `tool.execution_complete.data.result.content`

It intentionally excludes event envelopes, telemetry, hook events, and
`result.detailedContent`. A successful `session.compaction_complete` creates a
compact marker without redefining the surrounding turn's token volume.
Fallback turns carry `token_source: "content-proxy"`.

Code deltas require correlating `tool.execution_start` and
`tool.execution_complete` by `toolCallId`; completion events do not carry the
tool name. Only successful built-in mutation tools are counted:

- `edit`
- `create`
- `apply_patch`
- `str_replace_editor`

For those tools, `+` and `-` lines are counted only inside unified-diff hunks.
This is a trust boundary, not just an optimization. Copilot read tools also
store synthetic file diffs in `detailedContent`, and arbitrary shell output
may begin with `+` or `-`; treating either as a code diff creates false bars.

Shell and MCP tools can mutate files, but their completion payload does not
guarantee an authoritative before/after diff. airgbmatrix reports no code
delta for them rather than inventing one from display text.

### Claude Code

Claude transcripts contain `user` / `assistant` records. airgbmatrix groups
assistant calls under the preceding real user prompt and sums distinct API
calls plus Edit/MultiEdit/Write code deltas. Token use includes input, output,
and cache-creation tokens; cache-read tokens remain excluded from the activity
signal pending a deliberate product decision.

Claude's `SessionStart.source` still supplies `/compact` and `/clear` markers.
Copilot supplies compact markers through its persisted compaction-complete
event and has no equivalent clear marker.

## Agency launcher impact

`agency copilot` launches the installed Copilot CLI, injects a session ID when
it can, and forwards non-Agency arguments to the child process. Permission
flags such as `--yolo` affect tool approval, not transcript event semantics.
The important behavior change from `agency claude` is therefore the engine and
its transcript schema, not the permission mode.

Agency's public CLI guide documents `agency copilot` as the interactive launch
path: `docs/agency/CLI/index.md`, "Launch an interactive session." The launcher
behavior is implemented in `client/agency/src/copilot.rs` where the command is
constructed, a session ID is injected, and filtered extra arguments are
forwarded.

## References

- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference
- https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks
- https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills
