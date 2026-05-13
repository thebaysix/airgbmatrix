---
name: claudergb-status
description: Show the current state of the claudergbmatrix board — every active session with its color, state, turn count, token totals, code-change totals, and idle time. Use when the user runs `/claudergb-status` or asks "what's on the board" / "show me the matrix" / "list my sessions".
---

# /claudergb-status

Read-only snapshot of the LED board.

## Steps

### 1. Fetch sessions

```bash
curl -sS --max-time 2 http://${CLAUDE_LED_HOST:-localhost}:5000/sessions
```

If curl fails (connection refused), tell the user:
> Server at `$CLAUDE_LED_HOST:5000` isn't reachable. If it runs on a
> different host (e.g. your home laptop over Tailscale), make sure that
> host is up and the server process is running.

If the response is `[]`, tell the user:
> No sessions on the board.

### 2. Render the table

Format one row per session. The board orders by `updated_at` descending (most
recent first), so render in that order — it matches the slot layout the user
sees on the panel.

Per session, compute:

| field   | source                                                           |
|---------|------------------------------------------------------------------|
| slot    | row index 0..7                                                   |
| id      | first 8 chars of `id` (full UUID is too noisy)                   |
| color   | name from `color_idx` (table below)                              |
| state   | `state` (working / stopped) — `pending=true` adds a `*` marker   |
| turns   | `len(turns)`                                                     |
| tokens  | `sum(t.tokens)` — abbreviate as `12k` / `1.2M` for legibility    |
| +/-     | `sum(t.added)` / `sum(t.removed)` — show `0` for plain `0/0`     |
| age     | now − `updated_at`, abbreviated as `42s` / `5m` / `2h` / `1d`    |

Color index → name:

```
0 orange   1 yellow   2 cyan      3 brown
4 blue     5 green    6 magenta   7 red
```

Render each row with the swatch as a colored ANSI block so the user can
match the table to the LED tiles at a glance. The palette RGB values are
in `server/colors.py::PALETTE` — for an inline render, use the printf
escape `\x1b[48;2;R;G;Bm  \x1b[0m` for a 2-char block per color. To
fetch the actual RGB without re-implementing it, run
`python ~/r/nonrepo/standalone/claudergbmatrix/server/colors.py palette <idx>`
and parse the hex.

A reasonable layout:

```
slot  id        color           state    turns  tokens   code        age
----  --------  --------------  -------  -----  -------  ----------  -----
  0   ed37888a  ████ red        working*    16     857k  +334/-245   42s
  1   d4614dd8  ████ blue       stopped     12     185k  +90/-59     5m
  ...
```

(The `████` block uses the actual ANSI background-color escape so the
swatch renders inline.)

### 3. Keep it tight

Don't dump the underlying JSON unless the user asks for it. The point of
this view is at-a-glance status. If they want the raw data, they can use
the `--debug` flag in `term_mock.py` or curl `/sessions` themselves.
