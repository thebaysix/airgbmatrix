# airgbmatrix

An ambient 32×32 RGB LED panel that shows what your Claude Code sessions are
doing — one tile per session, colored by session GUID, with a live token +
code-change histogram of the most recent turns (12 columns wide; a turn that
edited files takes 2 columns, so roughly 6–12 turns of history per session).

```
+----+------------+
|COL | HISTOGRAM  |   <- one tile = one Claude Code session (16×8 px)
|4×8 |    12×8    |
+----+------------+
```

- **Color swatch** (left) — solid block from a fixed 8-color palette (hues
  spaced 45° apart, S=0.65 L=0.5). The server **leases** an index to each
  session on its first POST and the session keeps that index for its
  lifetime. With at most 8 visible sessions, every visible swatch is
  guaranteed distinct (vs. hash-mod-8 which would collide ~76% of the time
  via the birthday paradox). Width is configurable via `COLOR_W` in
  `server/renderer.py` and `s3/render_frame.py` (default 4); the histogram
  takes whatever's left.
- **Histogram** (right) — `HIST_W` columns × 8 rows (default 12). Each turn
  produces a white **token bar** (sqrt-scaled across all sessions); turns
  that touched code (Edit/Write/MultiEdit) also produce a stacked **code
  bar** in the next column — both colors anchored at the bottom of the
  column, green block first, red block stacked on top of green. Each color
  independently sqrt-scaled and capped at 4 px (column-half) so a balanced
  max-out fills the full 8 px. 1-px floor per color so single-line edits
  still register. Always bottom-anchored — a removed-only turn sits at the
  floor, not hanging from the ceiling. The token bar between turns
  guarantees no two code bars are ever adjacent. Turns older than 24h
  render dim. Newest on the left, fading right.

  Slash-command markers also appear inline as full-column 8-px bars at
  their place in the chronological order: **sky blue** (`#64b4ff`) for
  `/compact`, **violet** (`#b464dc`) for `/clear`. They consume one
  histogram column each (no following code-bar slot) and dim like the
  rest after 24h.

The server stores more turns than it displays (`MAX_TURNS=16`, `notify.sh`
ships up to 16) so that growing `HIST_W` (by shrinking `COLOR_W`) backfills
extra bars from existing state instead of having to wait for fresh POSTs.
Renderers cap rendering at `HIST_W` regardless.

A turn with code occupies 2 columns (token + code), so "N cols back" is no
longer strictly "N turns back" — but at-a-glance "when did I last touch
code" is more useful than precise turn counting.
- **Pending indicator** — while a request is in flight, column 0 of the
  histogram becomes a rising-bar animation; existing turn bars shift right by
  one. Clears as soon as the next Stop hook fires.

Up to 8 sessions can be displayed at once. When a 9th arrives, the oldest by
`updated_at` is evicted.


## Layout

```
slot 0  slot 4
slot 1  slot 5
slot 2  slot 6
slot 3  slot 7
```

Slots fill column-first, top to bottom. Most recently active session lands in
slot 0.


## Architecture

### End-to-end overview

Two hosts plus one MCU plus one panel, joined by three network hops.

```
[Cloud devbox WSL]                [Home laptop WSL]            [Home LAN]
 Claude Code                       Flask server :5000           S3 Matrix Portal
   │ hook fires                      ↑    state.json (disk)       ↑   2.4GHz WiFi
   notify.sh                         │                            │
   curl POST ─── Tailscale ──────────┘                            │
                                     │                            │
                                     └── mirrored mode +          │
                                         Defender rule ───────────┘
                                         (192.168.88.0/24 scoped)
                                                                  │
                                                                  ↓ HUB75 16-pin ribbon
                                                              [32×32 RGB panel]
                                                              5V wall adapter
                                                              + 4700µF cap
```

**Data flow per Claude turn.**

1. User submits prompt → `UserPromptSubmit` hook → `notify.sh pending`
   → POST `/session {id, state=working, pending=true}` over Tailscale to
   the laptop.
2. Claude responds → `Stop` hook → `notify.sh stopped` → reads the
   transcript, groups assistant messages by preceding user uuid, computes
   `{msg_id, tokens, added, removed, ts}` per turn (last 16), POSTs
   `/session {id, state=stopped, pending=false, turns}`. Idempotent
   upsert on `msg_id` via max-merge.
3. Server writes `state.json` atomically, leases a palette idx via
   `_assign_color_indices`, append-only audit log of color changes to
   the systemd journal.
4. S3 polls `GET /sessions` every 500ms via `adafruit_requests` over
   WiFi → JSON → `render_frame()` → `Canvas.SetPixel(x,y,r,g,b)` →
   `displayio.Bitmap[x,y] = palette_idx` (with `BRIGHTNESS_PCT` baked
   into the palette entry).
5. `display.refresh()` → `rgbmatrix` driver → HUB75 GPIO bit-bang →
   panel LEDs.

**Persistence and recovery.** Laptop reboot → Windows Task Scheduler
fires `wsl.exe --exec sleep infinity` at login → WSL boots → systemd
PID 1 → `airgbmatrix.service` starts (linger enabled) → Flask
listens → the S3's ongoing silent retries (it's been polling every
500ms on wall power throughout) finally succeed → panel re-paints. No
manual steps. State carried across by `state.json`, loaded on server
boot.

**Auth and firewall posture.** Devbox → laptop traffic is
Tailscale-only; no public exposure. LAN → laptop traffic is gated by a
scoped Defender rule (TCP 5000, `RemoteAddress 192.168.88.0/24` only)
plus the Hyper-V vSwitch rule for the WSL mirror. Home subnet
only. The server itself runs no auth — internal by design.

**Skills (run from any devbox Claude tab):**

- `/airgb-color <name>` → POST `/claim-color` → server bumps the
  current holder if needed, prints the hex string for manual Windows
  Terminal tab tint.
- `/airgb-clear <name>` → POST `/session {state=closed}` → server
  deletes the record, frees the palette lease.
- `/airgb-status` → GET `/sessions` → ANSI table of every active
  session.

All three skills resolve their target via `$CLAUDE_LED_HOST:5000` (the
laptop's Tailscale IP, set in the devbox's `~/.claude/settings.json`).

### Two-host topology (detail)

```
   Cloud dev box (WSL)                    Home laptop (WSL)
   +───────────────────────────+          +─────────────────────────────+
   | Claude Code               |          | server.py (Flask:5000)      |
   |   notify.sh ──POST──┐     |          |   ▲           ▲             |
   |   tint_terminal.sh  │     |          |   │           │ poll        |
   |                     │     │ Tailscale│   │       term_renderer.py  |
   |                     └─────┼──────────┼───┘       mock.py           |
   +───────────────────────────+          +────────────│────────────────+
                                                       │ Win-side bridge
                                                       │ (mirrored mode
                                                       │  or portproxy)
                                                       ▼
                                            ┌──────────────────────┐
                                            │  Home LAN (WiFi)     │
                                            │   S3 Matrix Portal   │
                                            │   polls /sessions    │
                                            │     │                │
                                            │     ▼ HUB75          │
                                            │   32×32 panel        │
                                            └──────────────────────┘
```

Topology notes: the state server lives on the home laptop so the S3 (on the
same home LAN) can reach it. Claude Code on the cloud dev box POSTs to the
laptop's Tailscale IP — set `CLAUDE_LED_HOST` in `~/.claude/settings.json`
to the laptop's `100.x.y.z` address. The S3 reaches the laptop's Windows
LAN IP, which proxies into laptop WSL via mirrored networking (or
`netsh portproxy` as a fallback). See "Laptop-side networking" below.

**State server** (`server/server.py`) — Flask app on port 5000. Holds a dict of
sessions in memory plus a JSON file (`state.json`) for crash recovery. The
board only ever shows currently-open sessions: a `state=closed` POST
(SessionEnd) deletes the session record entirely, freeing its palette lease
so the next session reusing the slot gets a fresh color. There's no
background reaper — lifecycle is fully driven by hooks (`SessionStart`
adds, `SessionEnd` removes) and the `/airgb-color` / `/airgb-clear`
skills for manual cleanup. If a Claude session ever exits without firing
SessionEnd (server down at the time, kernel panic, etc.) the stale record
just sticks around until you `/airgb-clear` it or it gets LRU-evicted
by an 8th-and-9th session arriving. Endpoints:

- `POST /session` — upsert a session by `id`. Optional `turns` array of
  `{msg_id, tokens, ts, added, removed}`; the server **upserts by `msg_id`**
  (one record per user-turn, identified by the starting user message's uuid)
  so repeated POSTs are idempotent and a later Stop with refreshed sums
  overwrites a prior under-count from a transcript-flush race. Each turn is
  enriched with `ts_epoch` (int seconds) for clients that can't parse ISO
  8601 (e.g. CircuitPython on the S3).
- `GET /sessions` — list all tracked sessions with their turn buffers,
  ordered by `updated_at` descending.
- `POST /claim-color` — manually set a session's palette index. Body:
  `{"session_id": str, "color_idx": 0..7}`. If another session is currently
  holding that index, it gets rotated to the lowest free one. Response:
  `{"ok": true, "assigned": int, "bumped": null|{"session_id":..., "color_idx":...}}`.
  Used by the `/airgb-color` skill.

**Renderers** — two stacks, one shared `render_frame`:

- *Server-side* (`server/renderer.py`) — pure paint logic, called by:
  - `server/term_renderer.py` — dev-box ANSI 24-bit terminal preview.
  - `server/mock.py` — browser preview at `http://localhost:5001`.
- *S3-side* (`s3/render_frame.py`) — port for CircuitPython, exercised by:
  - `s3/code.py` — real Matrix Portal S3 entry point. Polls over WiFi via
    `adafruit_requests`, paints into `displayio.Bitmap`.
  - `s3/term_mock.py` — runs the same code path on the dev box, with
    `s3/displayio.py` (mock) painting half-blocks to the terminal. Lets you
    debug palette caching and `Canvas` shim before flashing real silicon.

The S3 path duplicates `render_frame` rather than importing the server-side
one, because `colors.py` differs (CircuitPython has no `colorsys`). Outputs
match bit-for-bit — verified by running `session_color()` from both modules
against a fixed input set.

**Hooks** (`hooks/*.sh`) — one shell script per Claude Code lifecycle event.
Configured in `~/.claude/settings.json` (see `hooks/settings.json.example`).


## Token + code model

Per-turn token count = `input_tokens + output_tokens + cache_creation_input_tokens`.
`cache_read_input_tokens` is excluded — it's roughly constant turn-over-turn
(the same cached context is re-read each time) and would flatten the histogram
into uniformly tall bars. What we actually want to surface is the *new* work
each turn produced.

Per-turn code change = sum of `added` / `removed` across all
Edit/Write/MultiEdit `tool_use` blocks in that user-turn, computed with
**net-delta + substring-aware wrap detection**:

- For each Edit (or MultiEdit sub-edit), if `old_string` is empty OR is a
  contiguous substring of `new_string`, treat it as a **wrap** (anchor /
  insert pattern, where `old_string` is just the unique-match anchor and
  no actual lines are being removed). Count: `added = lines(new)`,
  `removed = 0`.
- Otherwise, count **net delta**: `added = max(0, lines(new) - lines(old))`,
  `removed = max(0, lines(old) - lines(new))`.
- Write counts content lines as added; we don't see prior file content
  from the tool_use, so removed stays 0.

This avoids the historical bug where Edit's `old_string` (which always
includes anchor/context lines for unique-match purposes) was counted as
"removed" — anchor-add patterns (`old="def foo():"`, `new="def foo():\n
return bar"`) used to produce a red bar despite no actual line removal.

Two boolean shadow fields, `added_any` and `removed_any`, are also
shipped per turn. They are true if the turn touched *any* lines on each
side, regardless of net delta. The renderer uses them as visibility
floors: when net added/removed is 0 but the bool is true (e.g. a 5→5
in-place refactor with no substring overlap), the bar still gets a 1-px
green/red floor so the activity stays visible. Anchored adds keep
`removed_any=false` (the substring check), so they don't trigger a red
floor.


## Hooks

| Event              | Script                  | What it does                            |
|--------------------|-------------------------|-----------------------------------------|
| `SessionStart`     | `notify.sh working`     | Marks session live in the state server. |
|                    |                         | If `source` is `compact` or `clear`,    |
|                    |                         | also POSTs a histogram marker turn —    |
|                    |                         | works for both manual `/compact /clear` |
|                    |                         | and auto-compaction.                    |
| `SessionStart`     | `tint_terminal.sh`      | GET /sessions to read this session's    |
|                    |                         | leased `color_idx`, then OSC 4;264 to   |
|                    |                         | `/dev/tty` so the Windows Terminal tab  |
|                    |                         | indicator matches the LED tile color    |
| `UserPromptSubmit` | `notify.sh pending`     | Sets pending=true → loading-bar anim    |
| `Stop`             | `notify.sh stopped`     | Tails the transcript, groups assistant  |
|                    |                         | API calls by preceding real user prompt,|
|                    |                         | sums tokens + added/removed per group,  |
|                    |                         | POSTs last 16 user-turns; clears pending|
| `SessionEnd`       | `notify.sh closed`      | Removes the session from the board      |
| `Notification`     | `notify.sh awaiting`    | Permission-blink feature. No-op while   |
|                    |                         | `BLINK_ON_PERMISSIONS=False` (current). |
| `PreToolUse`       | `notify.sh tool-start`  | Permission-blink feature. No-op while   |
|                    |                         | `BLINK_ON_PERMISSIONS=False` (current). |

Tab color persists past `SessionEnd` even though the board tile is removed —
the OSC 4 escape stays in effect on the terminal until the tab is closed or
explicitly reset. Add an OSC 104 reset to a `SessionEnd` hook if you'd
rather the tab revert when a session ends.

**Known limitation: tab tint can drift past 8 concurrent tabs.** Each tab
emits OSC 4 once on its own `SessionStart` and the tint then lives in the
terminal forever. The server, however, keeps only the 8 most-recent-by-
`updated_at` sessions and reassigns `color_idx` leases as sessions evict and
return. Consequences when you cross 8 active sessions:
1. An evicted session's tab still shows its old tint even though its tile is
   gone from the board (visual ghost).
2. If that tab later re-prompts, it gets a *new* lease — usually a different
   color than its frozen tint, so tab and tile no longer match.

We live with it because >8 concurrent tabs is rare. If you'd rather not
drift, add `tint_terminal.sh` to the `UserPromptSubmit` hook so every prompt
re-syncs the tab to the current lease (extra GET per prompt + brief flash on
color change).


## Installation

Two hosts, two installs.

### Cloud dev box (Claude Code + hooks only)

```bash
# Hooks need jq + curl
jq --version && curl --version | head -1

# Make hooks executable
chmod +x hooks/notify.sh hooks/tint_terminal.sh

# Edit ~/.claude/settings.json — see hooks/settings.json.example for the shape.
# Set CLAUDE_LED_HOST to the laptop's Tailscale IP (e.g. 100.x.y.z).
```

Reload settings via `/hooks` (or restart `claude`) so the hooks pick up.
Hooks POST to `CLAUDE_LED_HOST:5000` over Tailscale.

### Home laptop (state server)

Copy just `server/` to the laptop (e.g. via `tailscale file cp`):

```bash
# On laptop WSL
cd ~/airgbmatrix
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
```

Tested with Python 3.12 on Ubuntu in WSL2.


## Running

```bash
# Laptop — state server (leave running)
.venv/bin/python server/server.py

# Laptop or dev box — pick one renderer for dev preview:
.venv/bin/python server/term_renderer.py     # ANSI server-side path
.venv/bin/python server/mock.py              # browser at http://localhost:5001
.venv/bin/python s3/term_mock.py             # ANSI S3-side path (Canvas + mock displayio)
```

Renderers hardcode `SERVER_URL = "http://localhost:5000"`, so they need
to run on the same host as the server (the laptop). To preview from the
dev box instead, edit the `SERVER_URL` constant in the renderer file to
the laptop's Tailscale IP.

Pass `--debug` to either terminal renderer to paint a copy-pasteable JSON
dump below the matrix — fetch status, session count, per-slot record. Useful
for spotting stuck sessions (e.g. `state=working, pending=true` long after the
prompt finished, usually from a Stop hook that POSTed to the wrong host before
env reload).

### Keeping the server alive

Recommended setup runs the server as a systemd user unit, with WSL
auto-starting on Windows login so a laptop reboot doesn't require any
manual steps. Three-layer cake:

**1. Enable systemd in WSL** (one-time, requires Win11 22H2+):

In `/etc/wsl.conf` on the laptop:

```ini
[boot]
systemd=true
```

`wsl --shutdown` from Windows to apply. Verify after relaunch:
`systemctl --user status` should print a running state table.

**2. systemd user unit** at `~/.config/systemd/user/airgbmatrix.service`:

```ini
[Unit]
Description=airgbmatrix state server
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/airgbmatrix
ExecStart=%h/airgbmatrix/server/.venv/bin/python server/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Enable, start, enable user lingering so the unit runs without an open
login shell:

```bash
systemctl --user daemon-reload
systemctl --user enable --now airgbmatrix.service
sudo loginctl enable-linger $USER
```

View logs: `journalctl --user -u airgbmatrix.service -f`.

**3. Auto-boot WSL on Windows login** so the systemd unit actually has
a kernel to run in. Two-part: a VBS wrapper that launches `wsl.exe`
without a console window, plus a Task Scheduler entry that fires the
wrapper at logon.

Create `C:\Users\<you>\hidden-wsl.vbs`:

```vbs
CreateObject("Wscript.Shell").Run "wsl.exe --exec sleep infinity", 0, False
```

Admin PowerShell on the laptop:

```powershell
Register-ScheduledTask -TaskName "StartWSL" `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
  -Action (New-ScheduledTaskAction -Execute "wscript.exe" -Argument "$env:USERPROFILE\hidden-wsl.vbs") `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) `
  -RunLevel Limited
```

**Note: the `sleep infinity` argument is load-bearing.** A shorter
command like `wsl.exe --exec true` boots WSL but exits immediately;
WSL then idle-times-out and tears down the VM ~10s later, taking your
service with it. `sleep infinity` is a no-CPU process that holds the
VM open indefinitely. Don't simplify it.

**Why the VBS wrapper:** `wsl.exe` is a console application — Task
Scheduler launching it directly opens a visible terminal window for
the lifetime of `sleep infinity`. Closing that window kills the
process and the chain collapses. `wscript.exe` running the VBS spawns
`wsl.exe` with WindowStyle=Hidden, so the VM stays alive in the
background with nothing visible to accidentally close. To verify it's
running: `wsl --list --running` (Windows-side) or check for the
`wsl.exe` process in Task Manager.

With all three in place, a laptop power-cycle ends with `/sessions`
serving JSON on its own, no terminals opened, no commands typed.

### Manual / quick-run alternative (tmux)

If you skipped the systemd setup or want to run on a host where it's not
available:

```bash
tmux new -s ledboard
cd ~/airgbmatrix && source server/.venv/bin/activate && python server/server.py
# Ctrl+B then D to detach. Re-attach later with:
tmux attach -t ledboard
```

`pkill -f "python server/server.py"` clears a stuck copy if port 5000
reports as in use.

### Powering the panel on/off

Plug both wall adapters (S3 USB-C + panel 5V) into a single **switched
power strip**. Flick the switch to kill both at once; flick back to bring
them up. The S3 reboots from cold, reconnects WiFi, resumes polling. No
state is lost — `state.json` lives on the laptop, unaffected.

The one hard rule: **never plug/unplug the HUB75 ribbon while the panel
has 5V applied.** Data lines aren't isolated from the LED rails on most
panels; hot-plugging can short signal pins to the 5V bus and brick the
S3's GPIO. Seat the ribbon once, leave it.


## Laptop-side networking (required for the S3)

The S3 Matrix Portal lives on your home LAN and needs to reach `server.py`
running inside the laptop's WSL2. By default WSL2 uses NAT and your LAN
can't see WSL ports. (The dev-box → laptop path is fine — it goes over
Tailscale, no special networking needed.) Two options for the laptop:

### Recommended: mirrored networking mode (Win11 22H2+)

Create or edit `C:\Users\<you>\.wslconfig` on the **laptop** and add:

```ini
[wsl2]
networkingMode=mirrored
```

Then in an admin PowerShell on the laptop:

```powershell
wsl --shutdown
# Hyper-V vSwitch → WSL VM (lets the host's mirrored adapter forward to WSL):
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
# External LAN → Windows :5000 (the actually-traversed Defender path).
# Scoped to the home subnet so it's effectively home-only even when the
# WiFi profile is Public — avoids exposure on public WiFi networks.
New-NetFirewallRule -DisplayName "airgbmatrix" -Direction Inbound `
  -Protocol TCP -LocalPort 5000 -Action Allow -Profile Public `
  -RemoteAddress 192.168.<your-subnet>.0/24
```

Both rules are needed: the Hyper-V one covers VM-side ingress, the
Defender one covers the LAN-side ingress. If your home WiFi is profile
Private (rare on a fresh Windows install), swap `-Profile Public` for
`-Profile Private`. Remove cleanly with
`Remove-NetFirewallRule -DisplayName airgbmatrix`.

After WSL restarts, the home LAN can reach the WSL Flask server at
`http://<laptop-lan-ip>:5000`. Verify from your phone or another LAN device:

```bash
curl http://<laptop-lan-ip>:5000/sessions   # should return JSON
```

### Fallback: NAT + portproxy

If you can't enable mirrored mode on the laptop, forward a Windows port
to the WSL VM. The WSL VM IP changes across reboots, so this needs
re-running on every boot:

```powershell
netsh interface portproxy add v4tov4 listenport=5000 listenaddress=0.0.0.0 `
  connectport=5000 connectaddress=$(wsl hostname -I)
```

Plus a Windows Defender Firewall inbound rule for TCP 5000.


## S3 Matrix Portal notes

`s3/code.py` is the CircuitPython entry point. Files to copy to `CIRCUITPY/`:
`s3/{canvas,colors,render_frame,features,code}.py` plus a filled-in
`settings.toml` (use `s3/settings.toml.example` as a starting point).
**Don't copy `s3/displayio.py` or `s3/term_mock.py`** — those are dev-box
mocks; real displayio is firmware-built-in.

Libraries to drop in `CIRCUITPY/lib/` from the Adafruit bundle (10.x):
`adafruit_matrixportal/`, `adafruit_portalbase/`, `adafruit_bitmap_font/`,
`adafruit_display_text/`, `adafruit_requests.mpy`,
`adafruit_connection_manager.mpy` (split out in 10.x; `adafruit_requests`
imports it),  `adafruit_fakerequests.mpy`, `neopixel.mpy`.

`settings.toml` keys: `WIFI_SSID`, `WIFI_PASSWORD`, `SERVER_URL`,
`BRIGHTNESS_PCT` (0-100, see Hardware notes).

Use `s3/term_mock.py` on the dev box to validate the S3 code path before
flashing — same Canvas, same Bitmap+Palette, same `render_frame`, just
painting to the terminal instead of HUB75.

### Updating live code on the S3

Once the S3 is on wall power, day-to-day tweaks (brightness, palette,
poll interval, etc.) need a brief plug-back into the laptop. Sequence:

1. Power off the panel (switched power strip flip).
2. Unplug S3 USB-C from the wall adapter; plug into the laptop.
3. Mount `CIRCUITPY` in WSL: `sudo mount -t drvfs D: /mnt/d`.
4. Edit in place. For brightness or other settings: edit `/mnt/d/settings.toml`.
   For code changes: copy the updated file from your `s3/` working copy:
   ```bash
   cp s3/code.py /mnt/d/code.py
   ```
   Board auto-reloads on save.
5. `sudo umount /mnt/d`, eject `CIRCUITPY` from Windows tray, unplug,
   plug back into the wall adapter, re-energize panel.

If the change came from the devbox source-of-truth, send it to the
laptop first via `tailscale file cp` (see Phase 2 in `MIGRATION.md` for
the recipe), extract on the laptop, then `cp` into `/mnt/d/`.

For pure runtime tuning (brightness, server URL) keep edits in
`settings.toml` so you don't have to re-flash `code.py`.

Risks worth knowing before flashing:

- **No threads on CircuitPython.** A single cooperative loop polls + renders.
  Wrap HTTP in a tight (~2s) timeout + try/except so a flaky poll shows stale
  state for one tick instead of hanging the loop.
- **mDNS resolution is unreliable.** Don't use `<host>.local`. Hardcode the
  Windows host LAN IP and pin it via static DHCP reservation in your router.
- **HTTPS is picky.** `adafruit_requests` over TLS has historically been
  fragile — fine here since the LAN server is plain HTTP.
- **No auth.** The server binds `0.0.0.0`. Fine on a home LAN, never expose to
  the public internet.


## File reference

| File                          | Purpose                                  |
|-------------------------------|------------------------------------------|
| `server/server.py`            | Flask state server, user-turn upsert, ts_epoch |
| `server/colors.py`            | BKDRHash → HSL → RGB; CLI prints hex     |
| `server/features.py`          | Feature flags (`BLINK_ON_PERMISSIONS` etc.) |
| `server/renderer.py`          | Shared server-side paint logic           |
| `server/term_renderer.py`     | ANSI terminal preview (server stack)     |
| `server/mock.py`              | Browser preview at :5001                 |
| `server/requirements.txt`     | Flask 3+, requests 2.31+                 |
| `s3/code.py`                  | CircuitPython entry point (real S3)      |
| `s3/term_mock.py`             | Dev-box runner of the S3 code path       |
| `s3/canvas.py`                | SetPixel shim over displayio.Bitmap+Palette |
| `s3/colors.py`                | colorsys-free port of `server/colors.py` |
| `s3/render_frame.py`          | CircuitPython port of paint logic        |
| `s3/features.py`              | Mirror of `server/features.py` for the S3|
| `s3/displayio.py`             | Dev-box mock of CircuitPython displayio  |
| `s3/settings.toml.example`    | WIFI_SSID, WIFI_PASSWORD, SERVER_URL, BRIGHTNESS_PCT |
| `legacy/pi_renderer.py`       | Legacy Pi-bonnet driver (not in current setup) |
| `hooks/notify.sh`             | Posts session state + last-16-turn usage |
| `hooks/tint_terminal.sh`      | OSC 4 tab tint                           |
| `hooks/settings.json.example` | Template for `~/.claude/settings.json`   |
| `MIGRATION.md`                | Phased migration plan: Pi → laptop + S3  |
| `skills/<name>/SKILL.md`      | Claude Code skill definitions (see below)|


## Skills

User-invokable skills live under `skills/<name>/SKILL.md`. Install one by
symlinking it into `~/.claude/skills/`:

```bash
ln -snf ~/r/nonrepo/standalone/airgbmatrix/skills/airgb-color \
        ~/.claude/skills/airgb-color
ln -snf ~/r/nonrepo/standalone/airgbmatrix/skills/airgb-clear \
        ~/.claude/skills/airgb-clear
ln -snf ~/r/nonrepo/standalone/airgbmatrix/skills/airgb-status \
        ~/.claude/skills/airgb-status
```

Available:

- **`/airgb-color <color>`** — set the current session's tile + tab
  to one of `orange yellow cyan purple blue green magenta red`. If the
  color is in use, the holder is rotated to a free idx; Claude prints a
  one-line command to paste in that bumped tab to resync its tint.
- **`/airgb-clear <color>`** — drop whichever session is currently
  using that color from the board. Same effect as that session firing
  SessionEnd. Frees the palette lease.
- **`/airgb-status`** — compact at-a-glance table of every active
  session: slot, short id, ANSI swatch + color name, state, turn count,
  abbreviated tokens, code +/- totals, and idle age. Read-only.


## Hardware notes

- **[S3 Matrix Portal](https://www.adafruit.com/product/5778)** has the
  HUB75 driver onboard and runs CircuitPython firmware + `displayio`.
  S3 → 16-pin ribbon → panel; nothing else between them.
- **Panel**: [32×32 RGB LED Matrix, 5mm pitch](https://www.adafruit.com/product/2026).
  Other pitches (4mm, 6mm) work — adjust the diffuser gap accordingly.
- **Power**: a 32×32 panel at full white draws ~2A @ 5V. Use a separate
  [5V 4A switching supply](https://www.adafruit.com/product/1466) via a
  [2.1mm jack to screw-terminal adapter](https://www.adafruit.com/product/368).
  A [4700µF capacitor](https://www.adafruit.com/product/1589) across the
  panel's 5V terminals is an optional but recommended add-on for inrush
  smoothing. All parts sold separately by Adafruit; there's no kit.
- **Brightness**: HUB75 panels run at outdoor-readable brightness by default.
  Set `BRIGHTNESS_PCT` (0–100 integer) in `CIRCUITPY/settings.toml`. `20` is
  comfortable indoors, `10–15` for bedrooms, `100` for outdoor demos.
  Applied as a one-shot RGB scale in `Canvas._palette_index`. Note:
  `display.brightness` doesn't exist on `rgbmatrix.RGBMatrix` /
  `framebufferio` — this is the software path. Saving `settings.toml`
  auto-reloads the board, so tuning is one-line + Ctrl+S.

### Diffusing the LEDs

The bare panel paints sharp point-source LEDs. A diffuser softens them
into something you can actually look at. The hidden variable is the
**gap between the LEDs and the diffuser** — usually 5–10mm gives the
best blend on a 32×32 P5 panel without bleeding pixels into each other.
Material options, cheapest to nicest:

- **Tier 1 — try with what's in the house.** Parchment paper, tracing
  paper, or white printer paper, taped to a cardboard frame ~5mm in
  front of the panel. Foam tape on the corners is the easiest spacer.
  Loses ~30% perceived brightness. Pleasant enough to leave on for days
  while you decide if you want better.
- **Tier 2 — ~$10 acrylic.** Pre-frosted acrylic sheet ("satin ice", "P95",
  "frosted plexiglass"), 1/8" / ~3mm thick. Or buy clear acrylic and sand
  one side with 320–400 grit in a circular pattern — same result for
  less. Mount with M3 standoffs or set into a shadow-box frame whose
  depth provides the gap.
- **Tier 3 — pixel grid + diffuser.** A 3D-printed black bezel with one
  5×5mm cell per pixel sits between panel and diffuser, isolating
  pixels into defined squares (the LaMetric Time look). Combined with
  frosted acrylic in front, each pixel looks like a clean square tile
  rather than a fuzzy blob. Etsy/Adafruit sell pre-printed grids for
  common panel pitches if you don't have a printer.

Glossy diffusers create hot spots; matte/frosted is always better. Test
with parchment paper first — if you like the diffused look, spend the
$10 on real acrylic.


## Troubleshooting

### Panel suddenly black after running fine for weeks

Most likely the laptop WSL VM died (Windows reboot, Task Scheduler trigger
missed, `sleep infinity` killed). The S3's WiFi may also have dropped if
its association was lost while the server was unreachable. Recovery
sequence:

**Laptop PowerShell:**
```powershell
wsl --list --running                   # likely empty
Start-ScheduledTask -TaskName "StartWSL"
Start-Sleep 8
wsl --list --running                   # should now list Ubuntu
curl.exe http://localhost:5000/sessions  # should return JSON
```

If `curl` fails after WSL is back, the systemd service didn't auto-start.
From WSL: `systemctl --user status airgbmatrix.service`. Restart it
manually if needed: `systemctl --user restart airgbmatrix.service`.

**S3 side** — easiest path is a power cycle (flip the strip off and on):
the S3 cold-boots, reads `settings.toml`, reconnects WiFi, resumes polling.
If you'd rather not power-cycle (or want to confirm the failure mode),
connect via PuTTY on `COM5` at 115200, drop into REPL, and reconnect:

```python
import os, wifi
wifi.radio.connect(os.getenv("WIFI_SSID"), os.getenv("WIFI_PASSWORD"))
print(wifi.radio.connected, wifi.radio.ipv4_address)
```

`True 192.168.88.21` confirms re-association. Ctrl+D to soft-reboot
code.py so it picks up where it left off.

If this recurs repeatedly: investigate Task Scheduler config. Candidate
hardening — add an `AtStartup` trigger alongside the `AtLogOn` trigger so
WSL boots even before user login; or set `RunOnlyIfLoggedOn=False` on
the task.


## Known limitations

- **"Dim after 24h" never fires on the S3.** Shelved. The S3 has no NTP
  sync, so `time.time()` returns boot-seconds (or 0), not Unix epoch.
  `age = now_epoch - ts_epoch` becomes a huge negative number, the
  `age > DIM_AFTER_S` branch in `s3/render_frame.py` is never taken,
  and every turn paints bright forever. Dev-box renderers do dim
  correctly (they use real `time.time()`). Fix options when we
  revisit:
  - **A.** Add `adafruit_ntp` to `code.py`, sync RTC after WiFi
    connect. Minimal change; one extra lib + one failure mode (NTP
    unreachable → silently lose feature for that boot).
  - **B.** Server includes `now_epoch` in the `/sessions` response;
    S3 uses that instead of `time.time()`. Cleanest split (S3 stays a
    dumb consumer); requires updating both renderers.
  - **C.** Server pre-computes per-turn `is_old: bool` and ships it.
    Simplest S3 change but moves the 24h threshold from render-side to
    server-side config.
  - Likely choice: B. Also verify `BAR_DIM (60,60,60)` is still
    visible at `BRIGHTNESS_PCT=20` (scaled → `(12,12,12)` which may
    quantize to off at `bit_depth=4`). If invisible, bump DIM RGBs
    higher (~`(100,100,100)`) before relying on the feature.
- **Tab tint depends on the server being live at SessionStart.** The
  `tint_terminal.sh` hook queries `GET /sessions` to learn the session's
  leased `color_idx`; if the server isn't running when a Claude session
  starts, the tab silently stays untinted (the hook exits 0 to avoid
  surfacing as a hook error). Re-tinting later requires either restarting
  the Claude session with the server up, or manually invoking the script
  with the session_id once the server is reachable. Future fix to consider:
  cache a `session_id → color_idx` mapping on disk so tinting can survive
  server restarts, OR have the hook retry with backoff.


## Future ideas

Filed for later consideration; not on the active roadmap.

- **"Awaiting permission" indicator.** *Tried, currently disabled.*
  Implemented behind the `BLINK_ON_PERMISSIONS` flag in
  `server/features.py` and `s3/features.py` — when on, the rising white
  pending column flips to a full-column amber bar that blinks on/off
  while a session is blocked on a tool-approval prompt. Wired through
  the `Notification` hook (set `awaiting=true` on permission messages)
  and cleared by `PreToolUse` and `Stop`.

  **Why it's off**: empirical testing showed Claude Code fires the
  `Notification` hook *after* the user resolves the permission prompt,
  not when the prompt is displayed — so the blink starts post-accept
  rather than during the actual wait, exactly the opposite of what we
  want. Without a "permission requested" hook (firing on prompt display)
  there's no clean signal in the current hook surface for "blocked on
  user right now." Code is left in place behind the flag — flip to
  `True` and reload `/hooks` to re-enable if upstream gains the right
  signal, or repurpose as a generic "tool in progress" indicator
  (semantics shift but visual works). Search the repo for
  `BLINK_ON_PERMISSIONS` to find every gated block for surgical removal.

