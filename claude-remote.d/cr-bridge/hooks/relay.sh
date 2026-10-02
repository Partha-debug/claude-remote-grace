#!/bin/bash
# Observer hook: forward the hook JSON to the claude-remote server. Never blocks Claude,
# never prints (async hook output would be fed back into Claude's context).
[[ -S "${CR_HOOK_SOCK:-}" ]] || { cat >/dev/null; exit 0; }
curl -s -o /dev/null --noproxy '*' -m "${2:-2}" --unix-socket "$CR_HOOK_SOCK" \
  -H 'Content-Type: application/json' --data-binary @- \
  "http://cr/hook?ev=${1:-unknown}&effort=${CLAUDE_EFFORT:-}&t=$(date +%s.%N)" 2>/dev/null
exit 0
