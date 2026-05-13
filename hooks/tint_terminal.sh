#!/usr/bin/env bash
# Color the Windows Terminal tab indicator (palette index 264) to match this
# session's LED tile. Queries the state server for the session's leased
# `color_idx`, then looks up palette[color_idx] via colors.py to emit OSC 4
# on /dev/tty so the escape reaches the parent terminal regardless of how
# Claude Code captures hook stdout.
#
# Claude Code runs hooks within a single matcher group in PARALLEL, so this
# script can fire before notify.sh has finished POSTing the new session. We
# retry the lookup a few times to cover that race; total wait is capped at
# ~1s so a genuinely-missing session still bails fast.
set -uo pipefail
# Note: -e intentionally omitted so transient failures (server unreachable,
# malformed hook input, no /dev/tty) silently no-op instead of returning
# non-zero — Claude Code surfaces hook errors to the user, and a missing
# tab tint is not an error worth surfacing.

INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
if [ -z "${SID:-}" ]; then
  exit 0
fi

HOST="${CLAUDE_LED_HOST:-localhost}"
PORT="${CLAUDE_LED_PORT:-5000}"

CI=""
for attempt in 1 2 3 4 5; do
  RESP=$(curl -sS --max-time 2 "http://${HOST}:${PORT}/sessions" 2>/dev/null || true)
  CI=$(printf '%s' "$RESP" \
    | jq -r --arg id "$SID" '.[] | select(.id==$id) | .color_idx // empty' \
         2>/dev/null || true)
  if [[ "$CI" =~ ^[0-7]$ ]]; then
    break
  fi
  sleep 0.2
done
if ! [[ "$CI" =~ ^[0-7]$ ]]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEX=$(python3 "$SCRIPT_DIR/../server/colors.py" palette "$CI" 2>/dev/null) || exit 0

RR="${HEX:0:2}"
GG="${HEX:2:2}"
BB="${HEX:4:2}"
ESC=$(printf '\033]4;264;rgb:%s/%s/%s\a' "$RR" "$GG" "$BB")
# Prefer /dev/tty so the escape bypasses captured stdout (the path used by
# real SessionStart hooks). Fall back to stdout when /dev/tty isn't
# writable — Claude Code's Bash tool / `!` prefix run without a
# controlling terminal, but raw escapes on stdout still pass through to
# the terminal renderer. The subshell + 2>/dev/null silences bash's
# "no such device" message when the redirect itself fails.
if ( printf '%s' "$ESC" > /dev/tty ) 2>/dev/null; then
  :
else
  printf '%s' "$ESC"
fi
exit 0
