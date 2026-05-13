# Migration plan: Pi → cloud dev box + laptop + S3 Matrix Portal

Pi 3 + Adafruit RGB Matrix Bonnet is shelved (suspected bad Molex cable; user
ordered replacements but is also moving the renderer onto an Adafruit Matrix
Portal S3 — purpose-built ESP32-S3 board running CircuitPython).

The state server now lives on the **home laptop** (in WSL), not the dev box.
The dev box is a cloud Windows machine accessed via Windows App — its WSL
is on Azure's network, not the home LAN, so the S3 can't reach it. Claude
Code on the dev box POSTs to the laptop over Tailscale; the S3 reaches the
laptop on the home LAN through a Windows-side bridge (mirrored mode or
portproxy).

```
   Cloud dev box (WSL)                    Home laptop (WSL)
   +───────────────────────────+          +─────────────────────────────+
   | Claude Code               |          | server.py (Flask:5000)      |
   |   notify.sh ──POST──┐     |          |   ▲           ▲             |
   |   tint_terminal.sh  │     │ Tailscale│   │           │ 500ms poll  |
   |                     └─────┼──────────┼───┘       term_renderer.py  |
   |                           │          |           mock.py           |
   +───────────────────────────+          +────────────│────────────────+
                                                       │ Win-side bridge
                                                       ▼
                                           Home LAN ──>  S3 Matrix Portal
                                                         (CircuitPython,
                                                          adafruit_requests)
                                                         ──HUB75──> 32×32
```


## Phases

**Phase 1 — server-side upgrade & reorg. ✅ DONE.**
- `pi/` moved to `server/`; `pi_renderer` extracted to `legacy/`.
- Python 3.12: `datetime.fromisoformat`, f-strings, type hints, Flask 3 route
  decorators.
- `requirements.txt` bumped to `flask>=3.0`, `requests>=2.31`.
- `ts_epoch` (int seconds) added alongside `ts`. Backfilled on load for
  pre-existing `state.json` files.
- `hooks/settings.json.example` defaults `CLAUDE_LED_HOST=localhost`.
- README rewritten with new architecture + WSL guidance + S3 risks.

**Phase 2 — Server moved to home laptop, dev box → laptop over Tailscale. ✅ DONE.**
- Tried: mirrored networking on the *cloud* dev box. Doesn't help — the dev
  box isn't on the home LAN, so the S3 still can't reach it.
- Done instead:
  - Joined the home laptop's WSL to the same tailnet as the dev box.
  - `tailscale file cp` ed `server/` over to the laptop.
  - Set up `venv` + `pip install -r requirements.txt` on the laptop;
    server runs at `0.0.0.0:5000`.
  - On the dev box, set `CLAUDE_LED_HOST` in `~/.claude/settings.json` to
    the laptop's Tailscale IP (`100.x.y.z`). Env propagated live without
    a Claude restart.
  - Stopped the old dev-box server.
- Cross-platform fix: `server.py` log path now uses `tempfile.gettempdir()`
  so it works on Windows-native Python if we ever move there.

**Phase 2b — Laptop-side bridge for S3 (home LAN access). ✅ DONE.**
- `.wslconfig` on the laptop (`$env:USERPROFILE\.wslconfig`):
  ```ini
  [wsl2]
  networkingMode=mirrored
  ```
- `wsl --shutdown` to apply.
- Hyper-V vSwitch → WSL VM traffic (admin PowerShell, one-time):
  ```powershell
  Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
  ```
- External LAN → Windows :5000. Home WiFi was profile=Public; rather than
  flip it to Private (which would change network discovery / SMB exposure),
  scoped the rule to the home subnet so it's effectively home-only even on
  Public WiFi (admin PowerShell):
  ```powershell
  New-NetFirewallRule -DisplayName "claudergbmatrix" -Direction Inbound \
    -Protocol TCP -LocalPort 5000 -Action Allow -Profile Public \
    -RemoteAddress 192.168.88.0/24
  ```
  Remove with `Remove-NetFirewallRule -DisplayName claudergbmatrix`.
- Verified: from phone on same WiFi, `http://192.168.88.12:5000/sessions`
  returns JSON. WSL `eth2` mirrors `192.168.88.12/24`.
- Fallback (not needed here): `netsh portproxy` forwarding Windows :5000 →
  WSL :5000, plus Defender Firewall inbound rule.

**Phase 3 — S3 code path on the dev box. ✅ DONE.**
- `s3/displayio.py` — dev-box mock of CircuitPython displayio with a
  `TerminalDisplay` that paints ANSI half-blocks. Bitmap, Palette, TileGrid,
  Group all behave like the real thing.
- `s3/canvas.py` — `Canvas` shim: `SetPixel(x,y,r,g,b)` → cached palette index
  → `displayio.Bitmap[x,y] = idx`. 32-entry palette covers our color set with
  headroom.
- `s3/colors.py` — port of `server/colors.py` with inlined HSL→RGB (no
  `colorsys`). Output verified bit-for-bit against the server module.
- `s3/render_frame.py` — port of paint logic. Takes `now_epoch` as a parameter
  so NTP-less boots can pass 0 (loses >24h dim feature, doesn't crash).
- `s3/code.py` — CircuitPython entry point (untested on hardware until parts
  arrive). WiFi via `wifi.radio`, HTTP via `adafruit_requests` with 2s timeout
  + try/except so flaky polls show stale state for one tick.
- `s3/term_mock.py` — dev-box runner. Same Canvas + render_frame as `code.py`,
  just substitutes the mock displayio + `requests` for `adafruit_requests`.
- `s3/settings.toml.example` — WIFI_SSID, WIFI_PASSWORD, SERVER_URL template.

**Phase 4 — S3 hardware flash & validate. ✅ DONE.**
- Flashed CircuitPython 10.2.1 onto Matrix Portal S3 (MATRIXS3BOOT → drag
  `.uf2` → CIRCUITPY mounts as `D:\`).
- Dropped in libs from 10.x bundle: `adafruit_matrixportal`,
  `adafruit_portalbase`, `adafruit_bitmap_font`, `adafruit_display_text`,
  `adafruit_requests.mpy`, `adafruit_fakerequests.mpy`, `neopixel.mpy`.
  Also `adafruit_connection_manager.mpy` — missed it initially, hit
  ImportError on first run.
- Copied `s3/{canvas,colors,render_frame,features,code}.py` + a filled-in
  `settings.toml` to `CIRCUITPY/`.
- Hardware: HUB75 ribbon → panel `IN` side, 4700µF cap on the screw-terminal
  adapter (long lead +, striped −), 5V wall adapter into the harness.
  Panel powered up first try.
- Tuned brightness. Started with a `BRIGHTNESS` constant in `code.py`,
  then refactored to `BRIGHTNESS_PCT` (int 0–100) in `settings.toml` so
  future tuning is one-line + save (no `code.py` re-transfer). Applied
  as a one-shot RGB scale in `Canvas._palette_index`. Currently set
  to 20.
- Pinned both nodes via router DHCP reservation: laptop → 192.168.88.20,
  S3 → 192.168.88.21. `SERVER_URL` in `settings.toml` updated accordingly.
- Skills (`/claudergb-color`, `/claudergb-clear`, `/claudergb-status`)
  updated to use `$CLAUDE_LED_HOST` instead of hardcoded `localhost`.
- Gotchas hit during bring-up:
  - SSID was `bluewhale6G` (6/5GHz band) — S3's ESP32-S3 only sees
    2.4GHz; switched to the 2.4GHz `bluewhale` SSID.
  - Laptop's home LAN IP drifted (`192.168.88.12` → `.3` → `.20`) before
    pinning, breaking the S3's hardcoded `SERVER_URL` once.
  - `display.brightness` doesn't exist on rgbmatrix/framebufferio — had
    to scale in software at the palette layer.

### Phase 4 checklist

Prereqs:
- [ ] Phase 2b done — `curl http://<laptop-lan-ip>:5000/sessions` works
      from your phone (or any home-LAN device).
- [ ] Server running on the laptop (`python server/server.py`).
- [ ] Laptop's Windows LAN IP noted (going into `settings.toml` on the S3).

Flash & libs:
- [ ] Double-tap reset → drag latest CP `.uf2` for `adafruit_matrixportal_s3`
      onto `MATRIXBOOT`.
- [ ] Copy Adafruit bundle libs into `CIRCUITPY/lib/`: `adafruit_matrixportal/`,
      `adafruit_requests.mpy`, plus the `requirements/adafruit_matrixportal/`
      deps (typically `adafruit_portalbase`, `adafruit_bitmap_font`,
      `adafruit_display_text`, `neopixel`).

Our code:
- [ ] Copy `s3/{canvas,colors,render_frame,features,code}.py` → `CIRCUITPY/`.
- [ ] Copy `settings.toml.example` → `CIRCUITPY/settings.toml`; fill in
      `WIFI_SSID`, `WIFI_PASSWORD`, `SERVER_URL`.
- [ ] Confirm `displayio.py` and `term_mock.py` are NOT on `CIRCUITPY/`.

Hardware:
- [ ] HUB75 ribbon: S3 → panel's `IN` side.
- [ ] 4700µF cap across the panel's 5V terminals.
- [ ] 5V wall adapter → panel screw terminals (don't power panel from USB).

Boot & validate:
- [ ] Serial console (`screen /dev/ttyACM0 115200`) shows successful
      `wifi.radio.connect` + first `/sessions` poll.
- [ ] Test prompt in a Claude tab → bar appears on the panel within ~1s.
- [ ] Static DHCP reservations set for both the S3 and the laptop on the
      home router (so `SERVER_URL` survives reboots).

Tuning (only if needed):
- [ ] Stutter or timeouts: lower `bit_depth` 4→3, raise `POLL_INTERVAL_S`
      0.5→1.0 in `code.py`.

**Phase 5 — production cutover.** Mostly done; two follow-ups pending.

- ✅ **systemd user service for `server.py`.** Unit at
  `~/.config/systemd/user/claudergbmatrix.service`, enabled +
  `loginctl enable-linger lucaswh-msft`. Survives shell close, restarts
  on crash. Logs via `journalctl --user -u claudergbmatrix.service -f`.
- ✅ **WSL auto-boot on Windows login.** Windows Task Scheduler task
  `StartWSL` runs `wsl.exe --exec sleep infinity` `-AtLogOn -User
  $env:USERNAME`. `sleep infinity` is required — `wsl --exec true`
  boots WSL but then idle-times-out and tears down the VM ~10s later,
  killing the service. The persistent sleep process keeps the VM alive.
- ⏳ **Delete `legacy/pi_renderer.py`.** Hold until the S3 has been
  stable for a week of normal use.
- ⏳ **Write up the build for the project log.** Standalone task.

Net result: panel survives an unattended laptop reboot. Boot sequence
on power-on:
1. Windows reaches login screen.
2. User logs in → Task Scheduler fires `StartWSL` → WSL VM boots with
   systemd as PID 1.
3. systemd starts `user@1000.service` (linger enabled) → that starts
   `claudergbmatrix.service` → Flask listens on `0.0.0.0:5000`.
4. Tailscale daemon comes up in WSL → devbox hooks can POST again.
5. Mirrored networking exposes `:5000` on laptop's home LAN IP → S3's
   ongoing polls (it's been retrying every 0.5s on wall power)
   suddenly start succeeding. Panel re-paints.

Total unattended time from power button to live panel: ~30–60s
depending on Windows boot + login auto-fill.


## Risks and decisions to flag in README

1. **Laptop-side WSL bridge required (or NAT + portproxy fallback).** The
   server runs in laptop WSL; without mirrored mode (or portproxy), the
   S3 on the home LAN can't reach it. Mirrored is dramatically simpler —
   `localhost` works both directions and LAN can reach WSL directly, no
   `netsh portproxy` script-on-boot to maintain. (The dev-box → laptop
   path is separate and goes over Tailscale, so neither bridge is needed
   for hook traffic.)
2. **mDNS resolution from CircuitPython is unreliable.** Don't use
   `windows-host.local`; hardcode the LAN IP and pin it via static DHCP.
3. **Single cooperative loop on the S3.** No threads. A blocked HTTP call
   freezes rendering — wrap polls in a tight timeout + try/except.
4. **HTTP only.** `adafruit_requests` over HTTPS has historically been picky;
   the LAN server is plain HTTP, fine.
5. **No auth on the Flask server.** Same risk as before — fine on a home LAN,
   never expose to the public internet.


## Pi as fallback

`legacy/pi_renderer.py` drives the Pi + Adafruit RGB Matrix Bonnet via
`rpi-rgb-led-matrix`. It imports `render_frame` from `server/` via a sys.path
hack so the paint logic stays single-source. Deleted once the S3 is in
production (Phase 5).
