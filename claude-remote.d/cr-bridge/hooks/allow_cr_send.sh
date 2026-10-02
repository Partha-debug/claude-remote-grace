#!/bin/bash
# PreToolUse(Bash, if "cr-send *"): auto-allow plain cr-send calls (no shell operators).
cmd=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)
case "$cmd" in *[\;\|\&\$\`\<\>\(\)]*|*$'\n'*) exit 0 ;; esac
[[ "$cmd" == "cr-send "* ]] && printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"claude-remote: cr-send only shows a file in your chat"}}'
exit 0
