# Copilot CLI migration scoping

E+D Claude Code accounts get removed **2026-06-30**. This doc maps what
claudergbmatrix depends on in Claude Code, what GitHub Copilot CLI offers
in its place, and what porting actually costs.

## What this project depends on in Claude Code

### Hooks (`~/.claude/settings.json`)

All six hook events are wired:

| Event | Script call | Why |
|---|---|---|
| `SessionStart` | `notify.sh working` + `tint_terminal.sh` | Register session, leverage `source` field (startup/resume/compact/clear) to drop full-column compact/clear markers in the histogram. |
| `UserPromptSubmit` | `notify.sh pending` | Flip to pending so renderer animates the loading bar. |
| `Stop` | `notify.sh stopped` | Trigger transcript walk → per-turn token + code-delta aggregation. |
| `Notification` | `notify.sh awaiting` | (Currently no-op — feature flag off; see README known limitations.) |
| `PreToolUse` | `notify.sh tool-start` | (Currently no-op.) |
| `SessionEnd` | `notify.sh closed` | Drop session record + free palette lease. |

Stdin contract relied on by `notify.sh`:
- `session_id` (every hook)
- `transcript_path` (Stop) → JSONL with `type`, `uuid`, `timestamp`,
  `message.id`, `message.usage.{input_tokens,output_tokens,cache_creation_input_tokens}`,
  `message.content[]` with `tool_use` blocks carrying Edit/MultiEdit/Write
  `input` fields.
- `source` (SessionStart) ∈ {startup, resume, compact, clear}
- `message` (Notification) — substring matched for `[Pp]ermission`.

Config scope: **user-global** at `~/.claude/settings.json`. Fires for every
session in every cwd. This is load-bearing for claudergbmatrix's "ambient
across all sessions" design.

### Skills (`~/.claude/skills/<name>/SKILL.md`)

Three skills, each a single `SKILL.md` with frontmatter (`name`,
`description`) and a markdown body that tells the model to curl the local
server. No SDK calls, no special tool grants.

- `claudergb-color <name>`
- `claudergb-clear <name>`
- `claudergb-status`

Also user-global (in `~/.claude/skills/`).

### Not Claude-specific

`server/`, `s3/`, palette logic, renderer, `state.json` — all vanilla
Python + Flask + HTTP. The Pi/S3 hardware path doesn't know Claude Code
exists. **Only `hooks/` and `skills/` are CC-coupled.**

## Copilot CLI parity (per research)

### Hooks

Copilot CLI has hooks with **broader coverage** than Claude's (adds
`postToolUse`, `subagentStart/Stop`, `preCompact`, `errorOccurred`, etc.).
Two payload dialects: native camelCase, and a **snake_case VS Code compat
dialect** that preserves `session_id` / `transcript_path` field names —
the latter is what minimizes `jq` diffs in `notify.sh`.

| Claude event | Copilot event | Drop-in for our use? |
|---|---|---|
| SessionStart | `sessionStart` | Yes for register, **No** for compact/clear markers — `source` enum is {startup, resume, new}, lacks `compact`/`clear`. |
| UserPromptSubmit | `userPromptSubmitted` | Yes. |
| Stop | `agentStop` | Yes — has `transcript_path`. |
| Notification | `notification` | Yes (richer `notification_type` enum incl. `permission_prompt`). Doesn't fix our upstream limitation (post-resolution firing) — that lives in Claude Code itself. |
| PreToolUse | `preToolUse` | Yes (richer — can mutate args; we don't need that). |
| SessionEnd | `sessionEnd` | Yes. |
| — | `preCompact` | **Recovers the compact signal** lost on `sessionStart`. |
| — | (none) | No clean replacement for `source=clear`. |

### Skills

Near drop-in. Same `SKILL.md` + frontmatter convention. Personal skills
live at `~/.copilot/skills/` (or `~/.agents/skills/`); project skills at
`.github/skills/`, `.claude/skills/`, or `.agents/skills/`. Slash command
invocation is identical.

### The two real blockers

1. **No documented user-global hooks settings.** Copilot hooks are
   loaded from `.github/hooks/*.json` in the current repo. There's no
   public equivalent of `~/.claude/settings.json` that wires hooks for
   every session regardless of cwd. claudergbmatrix's whole point is
   ambient across all sessions — so this is the load-bearing gap.

2. **Transcript schema undocumented.** `transcript_path` is exposed,
   but the format (JSONL? same field shape? `message.usage` present?
   `tool_use.input` for Edit/MultiEdit/Write?) is not documented
   publicly. The per-user-turn aggregator in `notify.sh` is built on
   Claude's specific schema; until we see a real Copilot transcript,
   we don't know if the jq pipeline survives or needs a rewrite.

## Migration plan

### Phase A — preflight (do once, no rush, anytime before 6/30)

1. Install Copilot CLI via Agency (`agency copilot`).
2. Capture one sample `Stop` hook payload + transcript file. Decide:
   does `jq` need real changes or just field-name swaps?
3. Hunt for an undocumented user-global hooks config. Check `~/.copilot/`,
   `~/.agents/`, Agency docs, and `copilot --help`. If nothing, file a
   feature request via `/feedback` — this is a real gap for ambient-status
   tooling, and we're early enough in the consolidation that it might
   get prioritized.

### Phase B — port skills (low-risk, do first)

```bash
mkdir -p ~/.copilot/skills
cp -r ~/.claude/skills/claudergb-{color,clear,status} ~/.copilot/skills/
```

Frontmatter is compatible. Body text refers to "Claude Code" in a couple
of places — global-replace with "Copilot CLI" or just "the agent". The
curl-the-server logic is unaffected. **Validate**: `/claudergb-status`
runs and renders the table.

### Phase C — port hooks (the actual work)

Decision branch on Phase A outcome:

- **If a global hook config exists**: write a new `settings.json`-style
  file there with the snake_case dialect. Rename `SessionStart` →
  `sessionStart` etc.; field accessors in `notify.sh` (`.session_id`,
  `.transcript_path`, `.source`, `.message`) survive unchanged.

- **If only repo-scoped hooks are supported**: pivot the design. Two
  options:
  - **Repo-scoped acceptance**: install `.github/hooks/*.json` only in
    repos where you actually want the LED visualization. Lose the "every
    session" property. Honest tradeoff.
  - **Wrapper script**: alias `copilot` to a wrapper that injects an
    ambient hook config. Brittle, but preserves behavior.

Either way:
- Rewrite the `SessionStart`-with-`source=compact` branch to fire on
  `preCompact` instead. There's no `clear` analog — drop that marker
  type, or use Notification on `notification_type=session_cleared` if
  Copilot emits one.
- Re-validate the transcript jq pipeline against a real sample. If
  `message.usage` field shape differs, the dedup + sum logic needs
  surgical fixes, not a rewrite.

### Phase D — README + settings.example updates

- `hooks/settings.json.example` → keep as Claude reference, add a
  parallel `hooks/copilot-hooks.example.json`.
- README: dual-path install instructions until 6/30, then drop the
  Claude path.

### Phase E — cleanup (after 6/30)

Delete `~/.claude/settings.json` references, archive the Claude skills
dir, point all docs at the Copilot setup.

## Cost estimate

- **Skills port**: ~15 min, mostly copy-paste.
- **Hooks port if global config exists**: ~1 hour (rename events,
  validate transcript schema, run end-to-end).
- **Hooks port if only repo-scoped**: ~3 hours + design pivot, OR accept
  that the board only fires for instrumented repos.
- **Transcript schema mismatch (worst case)**: ~half a day to rewrite
  the jq aggregator. Bounded — the inputs we care about (tokens,
  added/removed lines, msg dedup) are conceptually simple.

## Decision point

Don't migrate yet. Wait until Phase A is cheap (Copilot CLI installed
anyway for daily work) and we have an actual transcript sample to look
at. Most of the risk lives in two unknowns; both resolve with five
minutes of empirical observation.

## Sources

Research notes from agent run on 2026-05-12 — verify against the live
docs before each phase:

- https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli
- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference
- https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks
- https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills
