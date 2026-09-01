#!/usr/bin/env bash
# Usage: notify.sh <working|stopped|closed|pending>
# Reads Copilot CLI or Claude Code hook JSON from stdin and POSTs session state
# to the LED board.
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
# tokens = relative token use within one user turn. Claude Code exposes exact
# usage fields; Copilot CLI persists model-visible content but not assistant
# usage, so its parser estimates per-turn volume from text size.
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

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // .sessionId // empty' 2>/dev/null || true)
TRANSCRIPT=$(printf '%s' "$INPUT" | jq -r '.transcript_path // .transcriptPath // empty' 2>/dev/null || true)
# SessionStart hook input includes a `source` field: startup | resume |
# compact | clear. Used below to emit /compact and /clear histogram markers
# (works for both manual /compact and auto-compaction triggered when
# context fills up).
SOURCE=$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null || true)

if [ -z "${SID:-}" ]; then
  exit 0
fi

# Copilot YAML subagents inherit user-level userPromptSubmitted hooks, but use
# transient session IDs while writing into the parent session's transcript.
# They never receive sessionStart/sessionEnd. Track IDs seen by sessionStart;
# the state-directory fallback covers the initial prompt, which Copilot can
# emit just before sessionStart. Claude payloads use snake_case and bypass this.
SESSION_REGISTRY="${AIRGBMATRIX_RUNTIME_DIR:-${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/airgbmatrix-sessions}"
SESSION_MARKER="$SESSION_REGISTRY/$SID"
CLOSE_QUEUE="${AIRGBMATRIX_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/airgbmatrix}/close-queue"
IS_COPILOT=false
if printf '%s' "$INPUT" | jq -e 'has("sessionId")' >/dev/null 2>&1; then
  IS_COPILOT=true
fi

# Reject auxiliary Copilot stops before they can create a lifecycle watcher.
if [ "$ARG" = "stopped" ] && [ "$IS_COPILOT" = true ] \
    && [ -n "$TRANSCRIPT" ]; then
  TRANSCRIPT_SID=$(basename "$(dirname "$TRANSCRIPT")")
  if [[ "$TRANSCRIPT_SID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] \
      && [ "$TRANSCRIPT_SID" != "$SID" ]; then
    exit 0
  fi
fi

if [ "$IS_COPILOT" = true ] && [[ "$SID" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  if [ "$ARG" = "pending" ]; then
    COPILOT_STATE_ROOT="${COPILOT_HOME:-$HOME/.copilot}/session-state"
    if [ ! -e "$SESSION_MARKER" ] \
        && [ ! -d "$COPILOT_STATE_ROOT/$SID" ]; then
      exit 0
    fi
  fi
fi

# SessionEnd cannot run when a terminal closes the CLI process abruptly.
# Keep a detached watcher tied to the real Copilot/Claude process as a
# liveness fallback. SessionStart/resume updates the marker identity so one
# watcher can follow a replacement process without deleting the resumed tile.
OWNER_GENERATION=""
if [[ "$SID" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  if [ "${AIRGBMATRIX_DISABLE_WATCHDOG:-0}" != "1" ]; then
    if command -v setsid >/dev/null 2>&1; then
      setsid -f python3 "$SCRIPT_DIR/session_watchdog.py" drain \
        "$CLOSE_QUEUE" </dev/null >/dev/null 2>&1
    else
      nohup python3 "$SCRIPT_DIR/session_watchdog.py" drain \
        "$CLOSE_QUEUE" </dev/null >/dev/null 2>&1 &
    fi
  fi
  OWNER=$(python3 "$SCRIPT_DIR/session_watchdog.py" owner "$$" 2>/dev/null || true)
  if [[ "$OWNER" =~ ^[0-9]+[[:space:]][0-9]+[[:space:]][^[:space:]]+$ ]]; then
    read -r OWNER_PID OWNER_START OWNER_GENERATION <<< "$OWNER"
    OWNER_IDENTITY="${OWNER_PID} ${OWNER_START}"
  fi
  if [ "$ARG" = "working" ]; then
    mkdir -p "$SESSION_REGISTRY" 2>/dev/null || true
    : > "$SESSION_MARKER" 2>/dev/null || true
  fi
  if [ "$ARG" = "closed" ]; then
    MARKER_OWNER=$(cat "$SESSION_MARKER" 2>/dev/null || true)
    if [ -z "${OWNER_IDENTITY:-}" ] || [ "$MARKER_OWNER" = "$OWNER_IDENTITY" ]; then
      rm -f "$SESSION_MARKER" 2>/dev/null || true
    fi
  elif [ "${AIRGBMATRIX_DISABLE_WATCHDOG:-0}" != "1" ]; then
    mkdir -p "$SESSION_REGISTRY" 2>/dev/null || true
    if [ -n "$OWNER_GENERATION" ]; then
      printf '%s\n' "$OWNER_IDENTITY" > "$SESSION_MARKER" 2>/dev/null || true
      WATCH_LOCK="$SESSION_MARKER.watch.lock"
      if command -v setsid >/dev/null 2>&1; then
        setsid -f python3 "$SCRIPT_DIR/session_watchdog.py" watch \
          "$OWNER_PID" "$OWNER_START" "$SID" \
          "$SESSION_MARKER" "$WATCH_LOCK" "$CLOSE_QUEUE" \
          </dev/null >/dev/null 2>&1
      else
        nohup python3 "$SCRIPT_DIR/session_watchdog.py" watch \
          "$OWNER_PID" "$OWNER_START" "$SID" \
          "$SESSION_MARKER" "$WATCH_LOCK" "$CLOSE_QUEUE" \
          </dev/null >/dev/null 2>&1 &
      fi
    fi
  fi
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
 if head -1 "$TRANSCRIPT" 2>/dev/null | jq -e 'has("parentId")' >/dev/null 2>&1; then
  # Copilot auxiliary agents can emit agentStop with their own transient
  # sessionId while pointing at the parent session's events.jsonl. Ignore that
  # stop: redirecting it would falsely mark the still-running parent stopped,
  # while trusting it would create a phantom tile.
  # --- GitHub Copilot transcript (events.jsonl): {type,data,id,timestamp,parentId}.
  #     Prefer exact cache-excluded usage from Copilot's session-store.db.
  #     Plain Copilot installations without that store fall back to estimated
  #     model-visible content volume.
  #
  #     detailedContent is NOT inherently a code diff: read tools render file
  #     contents as synthetic diffs and shell output is arbitrary text. Correlate
  #     completions to tool.execution_start by toolCallId and count hunk lines
  #     only for Copilot's built-in file-mutating tools.
  #
  #     One turn record per parent user.message; last 16 turns. ---
  COPILOT_CONFIG_DIR=$(dirname "$(dirname "$(dirname "$TRANSCRIPT")")")
  EXACT_USAGE_JSON=$(
    python3 "$SCRIPT_DIR/copilot_usage.py" \
      "$COPILOT_CONFIG_DIR/session-store.db" "$SID" 2>/dev/null
  ) || EXACT_USAGE_JSON="{}"
  if ! printf '%s' "$EXACT_USAGE_JSON" | jq -e 'type == "object"' >/dev/null 2>&1; then
    EXACT_USAGE_JSON="{}"
  fi

  TURNS_JSON=$(jq -s -c --argjson exact "$EXACT_USAGE_JSON" '
    def textlen:
      if . == null then 0
      elif type == "string" then length
      else (tojson | length) end;
    def turn_chars:
      if .type == "user.message" then
        ((.data.transformedContent // .data.content) | textlen)
      elif .type == "assistant.message" then
        ((.data.content | textlen)
         + (.data.reasoningText | textlen)
         + ((.data.toolRequests // [])
            | if length == 0 then 0 else textlen end))
      elif .type == "tool.execution_complete" then
        (.data.result.content | textlen)
      else 0 end;
    def is_code_tool:
      . == "edit" or . == "create" or . == "apply_patch"
      or . == "str_replace_editor";
    def difflines:
      if . == null or . == "" then {a:0,r:0}
      else reduce (split("\n")[]) as $line
        ({a:0,r:0,in_hunk:false};
         if ($line | startswith("diff --git ")) then .in_hunk = false
         elif ($line | startswith("@@")) then .in_hunk = true
         elif .in_hunk and ($line | startswith("+"))
              and (($line | startswith("+++")) | not) then .a += 1
         elif .in_hunk and ($line | startswith("-"))
              and (($line | startswith("---")) | not) then .r += 1
         else . end)
        | {a,r} end;
    reduce .[] as $e ({
      groups:[], cur:null, tools:{}, aux_interactions:{}, aux_events:{},
      turn_index:-1
    };
      ($e.type) as $t
      | ($e.data.interactionId // "") as $interaction
      | (($t == "user.message"
          and (($e.data.source // "") | startswith("agent-")))
         or (($interaction != "")
             and (.aux_interactions[$interaction] // false))
         or ($e.data.parentToolCallId? != null)
         or (.aux_events[($e.parentId // "")] // false)) as $is_aux
      | if $is_aux then
          (if $interaction == "" then .
           else .aux_interactions[$interaction] = true end)
          | (if ($e.id // "") == "" then .
             else .aux_events[$e.id] = true end)
        elif $t == "user.message" then
          (if .cur != null then .groups += [.cur] else . end)
          | .turn_index += 1
          | .cur = {
              msg_id:($e.id // ($e.timestamp+"_u")),
              kind:"turn",
              ts:$e.timestamp,
              turn_index:.turn_index,
              chars:($e | turn_chars),
              tokens:([1, ((($e | turn_chars)/4)|floor)] | max),
              added:0,
              removed:0,
              added_any:false,
              removed_any:false
            }
        elif $t == "tool.execution_start" then
          .tools[$e.data.toolCallId] = ($e.data.toolName // "")
        elif $t == "tool.execution_complete" then
          (if .cur == null then
             .cur = {
               msg_id:("orphan_"+($e.timestamp//"0")),
               kind:"turn",
               ts:$e.timestamp,
               turn_index:null,
               chars:0,
               tokens:1,
               added:0,
               removed:0,
               added_any:false,
               removed_any:false
             }
           else . end)
          | .cur.chars += ($e | turn_chars)
          | (.tools[$e.data.toolCallId] // "") as $tool
          | (if ($e.data.success == true and ($tool | is_code_tool)) then
               (($e.data.result.detailedContent) | difflines) as $d
               | .cur.added += $d.a
               | .cur.removed += $d.r
               | .cur.added_any = (.cur.added_any or ($d.a > 0))
               | .cur.removed_any = (.cur.removed_any or ($d.r > 0))
             else . end)
          | .cur.tokens = ([1, ((.cur.chars/4)|floor)] | max)
          | .cur.ts = $e.timestamp
        elif ($t == "session.compaction_complete"
              and $e.data.success == true) then
          .groups += [{
            msg_id:("copilot:"+$e.id),
            kind:"compact",
            tokens:0,
            ts:$e.timestamp,
            added:0,
            removed:0,
            added_any:false,
            removed_any:false
          }]
        elif ($t == "assistant.turn_end" or $t == "assistant.message") then
          (if .cur != null then
             (.cur.chars += ($e | turn_chars)
              | .cur.tokens = ([1, ((.cur.chars/4)|floor)] | max)
              | .cur.ts = $e.timestamp)
           else . end)
        else . end)
    | (if .cur != null then .groups + [.cur] else .groups end)
    | .[(-16):]
    | map(
        if .kind == "turn"
           and (($exact[(.turn_index | tostring)] // 0) | type) == "number"
           and ($exact[(.turn_index | tostring)] // 0) > 0 then
          .tokens = $exact[(.turn_index | tostring)]
          | .metrics_version = 5
          | .token_source = "copilot-usage"
        elif .kind == "turn" then
          .metrics_version = 5
          | .token_source = "content-proxy"
        else
          .metrics_version = 5
          | .token_source = "marker"
        end
        | {msg_id,kind,metrics_version,token_source,tokens,ts,
           added,removed,added_any,removed_any}
      )
  ' "$TRANSCRIPT" 2>/dev/null) || TURNS_JSON="[]"
 else
  # One turn record per *user-prompt* (not per assistant API call). Walk the
  # transcript forward, group assistant messages by the preceding real user
  # message — "real" meaning user.content is a string, OR an array with no
  # tool_result blocks (tool_result lines are user-typed in API form but
  # they're really tool outputs, not new user prompts).
  #
  # Per-group aggregation: tokens summed across all assistant API calls in
  # the group; token use and code deltas sum across every assistant API call.
  # Edit semantics:
  # replacing N lines with M counts as M added + N removed (git-diff style).
  # Write counts content as added; removed stays 0 since we don't see prior file
  # content from the tool_use.
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
        # Dedup usage per msg_id (thinking + text + tool_use of one assistant
        # call appear as separate transcript lines with the same msg_id and
        # same usage). Sum distinct API calls and accumulate code_delta per
        # line because tool_use can live on a different line than text.
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
        metrics_version: 5,
        token_source: "claude-usage",
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
    --arg lifecycle "$ARG" \
    --arg owner_generation "$OWNER_GENERATION" \
    --argjson pending "$PENDING" \
    --argjson awaiting "$AWAITING" \
    --argjson turns "$TURNS_JSON" \
    '{id:$id, state:$state, lifecycle:$lifecycle,
      pending:$pending, awaiting:$awaiting}
     + (if $owner_generation != "" then
          {owner_generation:$owner_generation} else {} end)
     + (if ($turns | length) > 0 then {turns:$turns} else {} end)' 2>/dev/null) || exit 0
  # --- end BLINK_ON_PERMISSIONS ---
else
  PAYLOAD=$(jq -nc \
    --arg id "$SID" \
    --arg state "$STATE" \
    --arg lifecycle "$ARG" \
    --arg owner_generation "$OWNER_GENERATION" \
    --argjson pending "$PENDING" \
    --argjson turns "$TURNS_JSON" \
    '{id:$id, state:$state, lifecycle:$lifecycle, pending:$pending}
     + (if $owner_generation != "" then
          {owner_generation:$owner_generation} else {} end)
     + (if ($turns | length) > 0 then {turns:$turns} else {} end)' 2>/dev/null) || exit 0
fi

curl -sS -X POST "http://${HOST}:${PORT}/session" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" \
  --max-time 2 \
  >/dev/null 2>&1 || true
exit 0
