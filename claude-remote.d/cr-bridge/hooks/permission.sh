#!/bin/bash
# PermissionRequest hook: wait for an answer from the web chat (the terminal dialog stays
# usable meanwhile; whichever answers first wins). Prints the decision JSON, or nothing.
[[ -S "${CR_HOOK_SOCK:-}" ]] || { cat >/dev/null; exit 0; }
curl -s --noproxy '*' -m 86400 --unix-socket "$CR_HOOK_SOCK" \
  -H 'Content-Type: application/json' --data-binary @- \
  "http://cr/permission?effort=${CLAUDE_EFFORT:-}" 2>/dev/null
exit 0
