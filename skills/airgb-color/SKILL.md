---
name: airgb-color
description: Manually set the LED tile color for the current Claude Code session to a specific palette color (orange, yellow, cyan, purple, blue, green, magenta, red). If the requested color is in use by another session, that session is rotated to a free color. Use when the user runs `/airgb-color <color>` or asks to change this session's tile/tab color.
---

# /airgb-color

Sets this session's color in the airgbmatrix board AND the Windows
Terminal tab. If the color is already in use, the holding session rotates
to a free color and the user is told how to retint that other tab.

The user invokes this with one argument:

```
/airgb-color <color>
```

where `<color>` is one of: `orange yellow cyan purple blue green magenta red`.

## Steps

### 1. Determine the current session UUID

Find the session UUID for *this* Claude Code session. It's referenced in
SessionStart hook output (system reminders earlier in the conversation),
in transcript file paths under `~/.claude/projects/`, and in any `git`
or `gh` CLI output that prints session metadata. Look for a UUID in the
format `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. If you genuinely cannot
find it, ask the user to paste it (they can find it via
`curl -s http://${CLAUDE_LED_HOST:-localhost}:5000/sessions | jq` and matching by their
project's cwd).

### 2. Map color name → palette index

| name    | idx |
|---------|-----|
| orange  | 0   |
| yellow  | 1   |
| cyan    | 2   |
| purple   | 3   |
| blue    | 4   |
| green   | 5   |
| magenta | 6   |
| red     | 7   |

If the user gives a color not in this list, tell them the valid options
and stop.

### 3. POST the claim

```bash
curl -sS -X POST http://${CLAUDE_LED_HOST:-localhost}:5000/claim-color \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SID>","color_idx":<IDX>}'
```

Expected response shape:

```json
{
  "ok": true,
  "assigned": <int>,
  "bumped": null
  // OR: {"session_id": "<other-sid>", "color_idx": <new-idx>}
}
```

Failure cases:
- HTTP 0 / connection refused → server isn't reachable. Tell the user:
  *"Server at `$CLAUDE_LED_HOST:5000` isn't reachable. If it runs on a
  different host (e.g. your home laptop over Tailscale), make sure that
  host is up and the server process is running."*
- HTTP 404 → session not yet registered. Tell the user:
  *"This session isn't tracked yet — submit any prompt to fire a hook,
  then re-run `/airgb-color`."*
- HTTP 400 → unreachable if the skill maps the color correctly.

### 4. Report the result

Programmatic OSC tinting from inside Claude Code doesn't reliably reach
Windows Terminal, so just hand the user the **hex code** to paste into
Windows Terminal's built-in **Right-click → Change tab color → More…**
dialog. Get the hex via:

```bash
python3 ~/r/nonrepo/standalone/airgbmatrix/server/colors.py palette <IDX>
```

(prints six hex digits like `2d2dd2`, no `#`).

If `bumped` is `null`:

> Current session set to **`<color>`**. Paste this hex into
> *Right-click → Change tab color → More…* on this tab:
>
> ```
> #<HEX>
> ```

If `bumped` is set, list both colors and their hex:

> Current session set to **`<color>`** (`#<HEX>`). Session
> `<bumped-short-id-8chars>` was using that color and got bumped to
> **`<bumped-color-name>`** (`#<BUMPED_HEX>`). Right-click → Change tab
> color → More… on each tab and paste the matching hex.

Keep the response short — confirmation plus the hex(es) to copy.
