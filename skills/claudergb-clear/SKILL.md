---
name: claudergb-clear
description: Remove the session currently using a given palette color from the claudergbmatrix board. The session record is deleted server-side (frees the palette lease, clears the LED tile) — same effect as the session firing SessionEnd. Use when the user runs `/claudergb-clear <color>` or asks to "drop the red session" / "remove the magenta tile" etc.
---

# /claudergb-clear

Drop whichever session is currently holding a given color. The session
record is deleted (palette lease freed, tile clears). Other tabs keep
their own colors; if the cleared session was the user's current tab, its
LED tile disappears but the tab itself keeps running — Claude Code only
notices the change at the next hook fire.

The user invokes this with one argument:

```
/claudergb-clear <color>
```

where `<color>` is one of: `orange yellow cyan purple blue green magenta red`.

## Steps

### 1. Map color name → palette index

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

### 2. Find the session at that idx

```bash
curl -sS --max-time 2 http://${CLAUDE_LED_HOST:-localhost}:5000/sessions \
  | jq -r --argjson idx <IDX> '.[] | select(.color_idx==$idx) | .id'
```

Three possible outcomes:

- Empty output → no session is currently using that color. Tell the user:
  > No session is using **`<color>`**.

- One session id → continue to step 3.

- Two or more (shouldn't happen given the lease invariant, but defensive)
  → process them all in a loop or just ask the user which to drop.

If the curl itself fails (connection refused), tell the user:
> Server at `$CLAUDE_LED_HOST:5000` isn't reachable. If it runs on a
> different host (e.g. your home laptop over Tailscale), make sure that
> host is up and the server process is running.

### 3. Delete the session

A `state=closed` POST removes the session entirely (it's the same path
SessionEnd uses):

```bash
curl -sS -X POST http://${CLAUDE_LED_HOST:-localhost}:5000/session \
  -H 'Content-Type: application/json' \
  -d '{"id":"<SID>","state":"closed"}'
```

Expect `{"ok":true}` with HTTP 200.

### 4. Report

> Cleared **`<color>`** session `<short-id-8chars>`.

If the session that got cleared is the user's *current* tab (its UUID
matches the session you're currently running in), append a note:

> *Heads up: that was this session. Run `/claudergb-color <newcolor>`
> to claim a new color, or any prompt will re-register on the next
> SessionStart-like hook fire.*

Wait — actually SessionStart only fires once per Claude session
boot, so simply submitting a prompt won't re-register. The session's
record will reappear on the next `Stop` hook (which fires after every
prompt). If the user wants this tab back on the board immediately,
they can re-fire the working state by hand:
> ```bash
> echo '{"session_id":"<this-sid>"}' | bash ~/r/nonrepo/standalone/claudergbmatrix/hooks/notify.sh working
> ```

Keep the response tight — confirmation + (only if relevant) one-line fix.
