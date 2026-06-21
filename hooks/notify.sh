#!/usr/bin/env bash
# Usage: notify.sh <working|stopped|closed|pending>
# Reads Claude Code hook JSON from stdin and POSTs session state to the LED board.
#
# `pending` is the UserPromptSubmit signal: keeps state as working but sets
# pending=true so the renderer paints an animated loading bar. Stop and
# SessionStart/End set pending=false explicitly.
#
# When state == stopped, also extracts the last 16 unique assistant turns from the
# transcript (each as {msg_id, tokens, ts}) and POSTs them as a `turns` array.
# The server dedupes by msg_id, so back-filling missed turns is automatic and
# repeated POSTs are idempotent — robust against Stop firing before the latest
# transcript line has been flushed.
#
# tokens = input + output + cache_creation. cache_read is excluded; it's
# roughly constant turn-over-turn and would flatten the histogram.
#
# Configure target via CLAUDE_LED_HOST / CLAUDE_LED_PORT (defaults: localhost:5000).
set -uo pipefail
# Note: -e intentionally omitted. Hooks are best-effort — when the server is
# down or jq sees malformed input, we want a silent no-op, not a non-zero
# exit that Claude Code surfaces as a hook error to the user.

ARG="${1:?state arg required: working|stopped|closed|pending|awaiting|tool-start}"
HOST="${CLAUDE_LED_HOST:-localhost}"
PORT="${CLAUDE_LED_PORT:-5000}"

# --- BLINK_ON_PERMISSIONS feature gate ---
# Read the flag once; both the new arg variants (awaiting, tool-start) and
# the inclusion of the `awaiting` field in the POST payload are gated on it.
# Defaults to "0" (off) if anything fails — server then ignores the field.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLINK_ON_PERMISSIONS=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR/../server')
from features import BLINK_ON_PERMISSIONS
print(1 if BLINK_ON_PERMISSIONS else 0)
" 2>/dev/null || echo 0)
# --- end BLINK_ON_PERMISSIONS ---

INPUT=$(cat)

# AWAITING is left empty for args that don't touch it; only included in the
# payload below when the feature flag is on.
AWAITING=""
case "$ARG" in
  pending)    STATE="working"; PENDING="true";  AWAITING="false" ;;
  working)    STATE="$ARG";    PENDING="false"; AWAITING="false" ;;
  stopped)    STATE="$ARG";    PENDING="false"; AWAITING="false" ;;
  closed)     STATE="$ARG";    PENDING="false"; AWAITING="false" ;;
  awaiting|tool-start)
    if [ "$BLINK_ON_PERMISSIONS" != "1" ]; then
      exit 0  # feature off — these args are silently no-ops
    fi
    STATE="working"; PENDING="true"
    if [ "$ARG" = "awaiting" ]; then AWAITING="true"; else AWAITING="false"; fi
    ;;
  *) echo "invalid arg: $ARG" >&2; exit 1 ;;
esac

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
TRANSCRIPT=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)
# SessionStart hook input includes a `source` field: startup | resume |
# compact | clear. Used below to emit /compact and /clear histogram markers
# (works for both manual /compact and auto-compaction triggered when
# context fills up).
SOURCE=$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null || true)

if [ -z "${SID:-}" ]; then
  exit 0
fi

# --- BLINK_ON_PERMISSIONS feature gate ---
# Notification hook fires for both permission prompts AND idle waits. Only
# the permission case matters here; filter by checking the `message` field
# for the substring "permission" (Claude Code uses messages like "Claude
# needs your permission to use Bash").
if [ "$ARG" = "awaiting" ]; then
  MESSAGE=$(printf '%s' "$INPUT" | jq -r '.message // empty' 2>/dev/null || true)
  case "$MESSAGE" in
    *[Pp]ermission*) ;;        # match — proceed
    *) exit 0 ;;               # idle ping or other notification — skip
  esac
fi
# --- end BLINK_ON_PERMISSIONS ---

TURNS_JSON="[]"
if [ "$STATE" = "stopped" ] && [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  # One turn record per *user-prompt* (not per assistant API call). Walk the
  # transcript forward, group assistant messages by the preceding real user
  # message — "real" meaning user.content is a string, OR an array with no
  # tool_result blocks (tool_result lines are user-typed in API form but
  # they're really tool outputs, not new user prompts).
  #
  # Per-group aggregation: tokens summed across all assistant API calls in
  # the group; added/removed summed across every Edit/MultiEdit/Write in
  # those calls. Edit semantics: replacing N lines with M counts as M added
  # + N removed (git-diff style). Write counts content as added; removed
  # stays 0 since we don't see prior file content from the tool_use.
  #
  # Key (msg_id field on the wire) = uuid of the starting user message —
  # stable across re-POSTs even when the transcript backfills more assistant
  # lines for the same group, so the server's upsert keeps refreshing sums.
  TURNS_JSON=$(jq -s -c '
    def lines: if . == null or . == "" then 0 else (split("\n") | length) end;
    # Per-Edit accounting:
    #   - If old_string is empty OR is a substring of new_string (anchor /
    #     wrap pattern), treat as pure add: added=lines(new), removed=0.
    #     This avoids counting anchor lines as "removed" — the common case
    #     where an Edit replaces a context anchor with anchor+new content.
    #   - Otherwise, net delta: added=max(0, lines(new)-lines(old)),
    #     removed=max(0, lines(old)-lines(new)).
    # _any bools survive same-size refactors (rename old=5 → new=5 with no
    # substring overlap → added=removed=0, but added_any=removed_any=true
    # so the renderer floors paint 1 px of each).
    def pair_delta($old; $new):
      ($old // "") as $o |
      ($new // "") as $n |
      ($o | lines) as $ol |
      ($n | lines) as $nl |
      (if ($o == "") or ($n | contains($o)) then
         {a: $nl, r: 0}
       else
         {a: (if $nl > $ol then $nl - $ol else 0 end),
          r: (if $ol > $nl then $ol - $nl else 0 end)}
       end) as $d |
      {added: $d.a, removed: $d.r,
       added_any: ($nl > 0),
       removed_any: ($ol > 0 and (($n | contains($o)) | not))};
    def edit_delta:
      if .name == "Edit" then
        pair_delta(.input.old_string; .input.new_string)
      elif .name == "MultiEdit" then
        ((.input.edits // []) | reduce .[] as $e
          ({added:0, removed:0, added_any:false, removed_any:false};
           pair_delta($e.old_string; $e.new_string) as $d |
           {added: (.added + $d.added),
            removed: (.removed + $d.removed),
            added_any: (.added_any or $d.added_any),
            removed_any: (.removed_any or $d.removed_any)}))
      elif .name == "Write" then
        ((.input.content // "") | lines) as $cl |
        {added: $cl, removed: 0,
         added_any: ($cl > 0), removed_any: false}
      else {added:0, removed:0, added_any:false, removed_any:false} end;
    def code_delta:
      ((.message.content // [])
        | map(select(.type == "tool_use") | edit_delta)
        | reduce .[] as $d
            ({added:0, removed:0, added_any:false, removed_any:false};
             {added: (.added + $d.added),
              removed: (.removed + $d.removed),
              added_any: (.added_any or $d.added_any),
              removed_any: (.removed_any or $d.removed_any)}));
    def msg_tokens:
      ((.message.usage.input_tokens // 0)
       + (.message.usage.output_tokens // 0)
       + (.message.usage.cache_creation_input_tokens // 0));
    def is_real_user:
      if .type != "user" then false
      else (.message.content // null) as $c |
        if ($c | type) == "string" then true
        elif ($c | type) == "array" then
          ($c | all(.[]; (.type // "") != "tool_result"))
        else false end
      end;
    reduce .[] as $x ({groups: [], cur: null};
      if ($x | is_real_user) then
        (if .cur != null and .cur.last_id != null
           then .groups += [.cur] else . end)
        | .cur = {
            msg_id: ($x.uuid // ($x.timestamp + "_u")),
            kind: "turn",
            ts: $x.timestamp,
            tokens: 0, added: 0, removed: 0,
            added_any: false, removed_any: false,
            last_id: null,
            seen: {}
          }
      elif ($x.type == "assistant"
            and ($x.message.usage // null) != null
            and ($x.message.id // null) != null) then
        (if .cur == null then
           .cur = {
             msg_id: ("orphan_" + ($x.timestamp // "0")),
             kind: "turn",
             ts: $x.timestamp,
             tokens: 0, added: 0, removed: 0,
             last_id: null,
             seen: {}
           }
         else . end)
        | $x.message.id as $mid
        # Dedup tokens per msg_id (thinking + text + tool_use of one
        # assistant call appear as separate transcript lines with the same
        # msg_id and same usage); accumulate code_delta per line because
        # tool_use can live on a different line than text.
        | (if (.cur.seen[$mid] // false) then .
           else .cur.seen[$mid] = true
                | .cur.tokens += ($x | msg_tokens) end)
        | ($x | code_delta) as $cd
        | .cur.added += $cd.added
        | .cur.removed += $cd.removed
        | .cur.added_any = (.cur.added_any or $cd.added_any)
        | .cur.removed_any = (.cur.removed_any or $cd.removed_any)
        | .cur.last_id = $mid
        | .cur.ts = $x.timestamp
      else . end)
    | (if .cur != null and .cur.last_id != null
         then .groups + [.cur] else .groups end)
    | .[(-16):]
    | map({
        msg_id: .msg_id,
        kind: .kind,
        tokens: .tokens,
        ts: .ts,
        added: .added,
        removed: .removed,
        added_any: .added_any,
        removed_any: .removed_any
      })
  ' "$TRANSCRIPT" 2>/dev/null) || TURNS_JSON="[]"

  # Defensive log: Stop fired with a real transcript but jq produced nothing.
  # Should be rare. Captures context so future misses (like the missing-bar
  # bug) are diagnosable instead of silent. Disable by unsetting the env var.
  if [ "$TURNS_JSON" = "[]" ]; then
    LOG="${AIRGBMATRIX_LOG:-/tmp/airgbmatrix-notify.log}"
    {
      printf '\n=== %s SID=%s STATE=%s ===\n' "$(date -Iseconds)" "$SID" "$STATE"
      printf 'transcript=%s lines=%s\n' \
        "$TRANSCRIPT" "$(wc -l < "$TRANSCRIPT" 2>/dev/null || echo '?')"
      printf 'TURNS_JSON empty after jq — turn record will not be added.\n'
    } >> "$LOG" 2>/dev/null || true
  fi
fi

# SessionStart hook with source in {compact, clear} → emit a full-column
# marker for the histogram. Fires for both manual (/compact, /clear) and
# auto-compaction (when the context window fills). msg_id is keyed on the
# session+source+second so re-invocations within the same second collapse
# (idempotent under max-merge), but distinct events stay distinct.
if [ "$ARG" = "working" ] && { [ "$SOURCE" = "compact" ] || [ "$SOURCE" = "clear" ]; }; then
  MARKER_TS="$(date -u +%Y-%m-%dT%H:%M:%S.000+00:00)"
  MARKER_MID="${SID}:${SOURCE}:$(date -u +%Y%m%d%H%M%S)"
  TURNS_JSON=$(printf '%s' "$TURNS_JSON" | jq -c \
    --arg mid "$MARKER_MID" --arg kind "$SOURCE" --arg ts "$MARKER_TS" \
    '. + [{msg_id:$mid, kind:$kind, tokens:0, added:0, removed:0, ts:$ts}]' \
    2>/dev/null) || TURNS_JSON="[{\"msg_id\":\"$MARKER_MID\",\"kind\":\"$SOURCE\",\"tokens\":0,\"added\":0,\"removed\":0,\"ts\":\"$MARKER_TS\"}]"
fi

if [ "$BLINK_ON_PERMISSIONS" = "1" ] && [ -n "$AWAITING" ]; then
  # --- BLINK_ON_PERMISSIONS feature gate: include awaiting field ---
  PAYLOAD=$(jq -nc \
    --arg id "$SID" \
    --arg state "$STATE" \
    --argjson pending "$PENDING" \
    --argjson awaiting "$AWAITING" \
    --argjson turns "$TURNS_JSON" \
    '{id:$id, state:$state, pending:$pending, awaiting:$awaiting}
     + (if ($turns | length) > 0 then {turns:$turns} else {} end)' 2>/dev/null) || exit 0
  # --- end BLINK_ON_PERMISSIONS ---
else
  PAYLOAD=$(jq -nc \
    --arg id "$SID" \
    --arg state "$STATE" \
    --argjson pending "$PENDING" \
    --argjson turns "$TURNS_JSON" \
    '{id:$id, state:$state, pending:$pending}
     + (if ($turns | length) > 0 then {turns:$turns} else {} end)' 2>/dev/null) || exit 0
fi

curl -sS -X POST "http://${HOST}:${PORT}/session" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" \
  --max-time 2 \
  >/dev/null 2>&1 || true
exit 0
