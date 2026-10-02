# Sourced by `bash -lic` inside the tmux pane, after your normal login files ran.
# Adds internet access, starts claude with your exact arguments, and shows a
# small menu when Claude exits.

unset TMUX TMUX_PANE            # Claude's Bash tool must not drive our tmux
module load WebProxy >/dev/null 2>&1 || echo "claude-remote: warning: 'module load WebProxy' failed (no internet?)"
export HTTP_PROXY="${http_proxy:-}" HTTPS_PROXY="${https_proxy:-}"
export no_proxy="localhost,127.0.0.1,$CR_NODE" NO_PROXY="localhost,127.0.0.1,$CR_NODE"
export CLAUDE_REMOTE=1 CLAUDE_REMOTE_JOB_ENV="$CR_DIR/slurm.env"

cd "$CR_CWD" || { echo "claude-remote: cannot cd to $CR_CWD"; sleep 30; exit 1; }
mapfile -d '' CR_ARGS < "$CR_DIR/args.bin"

CR_PLUGIN=()
[[ -d "$CR_HERE/cr-bridge" ]] && CR_PLUGIN=(--plugin-dir "$CR_HERE/cr-bridge")

# Flags worth keeping when Claude is restarted with --continue after it exits.
cr_sticky_args() {
  local i a
  CR_STICKY=()
  for ((i = 0; i < ${#CR_ARGS[@]}; i++)); do
    a="${CR_ARGS[$i]}"
    case "$a" in
      --dangerously-skip-permissions|--allow-dangerously-skip-permissions|--strict-mcp-config|--verbose|--chrome|--no-chrome)
        CR_STICKY+=("$a") ;;
      --permission-mode|--model|--effort|--settings|--mcp-config|--plugin-dir|--add-dir|--agent|--agents|--append-system-prompt|--system-prompt|--fallback-model|--setting-sources|--allowedTools|--allowed-tools|--disallowedTools|--disallowed-tools)
        CR_STICKY+=("$a" "${CR_ARGS[$((i + 1))]}"); i=$((i + 1)) ;;
      --permission-mode=*|--model=*|--effort=*|--settings=*|--mcp-config=*|--plugin-dir=*|--add-dir=*|--agent=*)
        CR_STICKY+=("$a") ;;
    esac
  done
}

# --add-dir goes last: it takes several values, so placed first it could swallow a prompt argument
CR_UPLOADS=(--add-dir "$CR_DIR/uploads")
clear
claude "${CR_PLUGIN[@]}" "${CR_ARGS[@]}" "${CR_UPLOADS[@]}"

while true; do
  cr_sticky_args
  echo
  echo "  Claude exited.  What next?"
  echo "    1. End the job now   (default in 10 minutes)"
  echo "    2. Restart Claude with --continue"
  echo "    3. Open a shell here (type 'exit' to end the job)"
  read -r -t 600 -n 1 -p "  Choice [1-3]: " choice || choice=1
  echo
  case "$choice" in
    2) clear; claude "${CR_PLUGIN[@]}" "${CR_STICKY[@]}" --continue "${CR_UPLOADS[@]}" ;;
    3) echo "Shell on $CR_NODE. Type 'exit' to end the job."; TMOUT=1800 bash -i; exit 0 ;;
    *) exit 0 ;;
  esac
done
