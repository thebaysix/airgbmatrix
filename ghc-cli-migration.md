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

Copilot's native hook payload uses `sessionId` and `transcriptPath`; Claude's
uses `session_id` and `transcript_path`. Both accessors are accepted.
Auxiliary Copilot agents can report a transient `sessionId` while sharing the
parent transcript; for stopped events, the UUID containing `events.jsonl` is
the canonical board identity.

## Transcript contracts

### Copilot CLI

Copilot persists `events.jsonl` records with `{type,data,id,timestamp,parentId}`.
airgbmatrix groups records by `user.message`.

`assistant.usage` is ephemeral and is not available to an `agentStop` hook.
The context bar therefore uses a proxy built only from persisted model-visible
content:

- system message content
- transformed user prompt content
- assistant content, reasoning text, and tool requests
- `tool.execution_complete.data.result.content`

It intentionally excludes event envelopes, telemetry, hook events, and
`result.detailedContent`. A successful `session.compaction_complete` resets
the estimate to the exact `postCompactionTokens` value and creates a compact
marker.

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
assistant calls under the preceding real user prompt, keeps the largest
reported context size in that group, and aggregates Edit/MultiEdit/Write code
deltas. Context includes input, output, cache-creation, and cache-read tokens.

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
