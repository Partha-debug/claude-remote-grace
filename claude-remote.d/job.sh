#!/bin/bash
# claude-remote job body (runs on the compute node as the SLURM batch shell).
#   job.sh LAUNCH_DIR
# Starts the web server, then tmux running the real interactive Claude, and stays
# alive (responsive to signals) until the tmux session ends.

DIR="$1"
source "$DIR/launch.env"            # CR_CWD CR_HERE CR_PORTAL CR_EMAIL
HERE="$CR_HERE"
JOBID="$SLURM_JOB_ID"
NODE="$(hostname -s)"
END="${SLURM_JOB_END_TIME:-$(date -d "$(squeue -h -j "$JOBID" -o %e)" +%s 2>/dev/null || echo 0)}"
TMUX_BIN="$HERE/bin/tmux"
PY="${CR_PYTHON:-/sw/eb/sw/Anaconda3/2025.12-2/bin/python}"
CONFIG="${SCRATCH:-$HOME}/.claude-remote/config"
[[ -f "$CONFIG" ]] && source "$CONFIG"   # optional overrides, e.g. CR_PYTHON=…

RUN="/tmp/claude_remote_$JOBID"
mkdir -p -m 700 "$RUN" "$RUN/xdg" "$DIR/uploads"
touch "$RUN/alive"
SOCK="$RUN/tmux.sock"
echo "$JOBID" > "$DIR/jobid"
( umask 077; env | grep '^SLURM_' | sed 's/^/export /' > "$DIR/slurm.env" )

log() { echo "[$(date +%T)] $*"; }

send_mail() {  # send_mail SUBJECT BODY
  [[ -n "$CR_EMAIL" ]] || return 0
  printf '%s\n' "$2" | mail -s "$1" "$CR_EMAIL" 2>/dev/null || log "mail failed"
}

cleanup() {
  rm -f "$RUN/alive"
  if [[ -n "${SRV:-}" ]]; then pkill -P "$SRV" 2>/dev/null; kill "$SRV" 2>/dev/null; fi
  "$TMUX_BIN" -S "$SOCK" kill-server 2>/dev/null
  rm -rf "$RUN"
  rm -f "$DIR/conn.json"
}

session_id() {  # newest Claude session id for this folder, from Claude's own status files
  python3 - "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions" "$CR_CWD" <<'PYEOF' 2>/dev/null
import glob, json, os, sys
best = (0, "")
for f in glob.glob(os.path.join(sys.argv[1], "*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("cwd") == sys.argv[2] and d.get("updatedAt", 0) > best[0]:
        best = (d["updatedAt"], d.get("sessionId", ""))
print(best[1])
PYEOF
}

on_term() {
  log "TERM received (walltime or cancel)"
  local sid; sid=$(session_id)
  send_mail "claude-remote: job $JOBID on $NODE ended" \
"Your claude-remote job $JOBID has ended (wall time reached or cancelled).

Resume the conversation with:
  cd $CR_CWD && claude-remote -r ${sid:-<session-id>}
(or 'claude-remote -c' to continue the most recent session in that folder)"
  cleanup
  exit 0
}
on_usr1() {
  log "10-minute warning"
  touch "$DIR/ending_soon"
  send_mail "claude-remote: job $JOBID ends in ~10 minutes" \
"Your claude-remote job on $NODE reaches its wall time in about 10 minutes.
Folder: $CR_CWD"
}
trap on_term TERM
trap on_usr1 USR1

log "node $NODE, job $JOBID, folder $CR_CWD"

# 1. web server (minimal environment; Claude never sees it). Restarted if it ever exits,
#    on the same port and secret path, so the link keeps working.
server_loop() {
  while [[ -f "$RUN/alive" ]]; do
    env -i HOME="$HOME" USER="$USER" LOGNAME="$USER" PATH=/usr/bin:/bin LANG=en_US.UTF-8 \
      SCRATCH="${SCRATCH:-}" CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-}" \
      "$PY" "$HERE/server/app.py" --dir "$DIR" --run "$RUN" --node "$NODE" --jobid "$JOBID" \
          --end "$END" --tmux "$TMUX_BIN" --sock "$SOCK" --cwd "$CR_CWD" --portal "$CR_PORTAL" --email "$CR_EMAIL" \
      >> "$DIR/server.log" 2>&1
    [[ -f "$RUN/alive" ]] && { echo "[$(date +%T)] server exited; restarting" >> "$DIR/server.log"; sleep 2; }
  done
}
server_loop &
SRV=$!

# 2. wait for the server (its hook socket must exist before Claude starts)
for _ in $(seq 1 60); do [[ -f "$DIR/conn.json" ]] && break; sleep 1; done
if [[ ! -f "$DIR/conn.json" ]]; then
  log "server did not start; see $DIR/server.log"; tail -20 "$DIR/server.log"
fi
# 3. tmux running the real Claude in a fresh login environment
"$TMUX_BIN" -u -f "$HERE/tmux.conf" -S "$SOCK" new-session -d -s claude -x 200 -y 50 \
  "/bin/bash '$HERE/run_claude.sh' '$DIR' '$RUN' '$NODE'"
"$TMUX_BIN" -S "$SOCK" set -g status-right \
  "#[fg=colour244]claude-remote · $NODE · #(e=$END; m=\$(( (e - \$(date +%s)) / 60 )); echo \"\$((m/60))h\$((m%60))m left\") · C-] l link · C-] d detach "

URL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$DIR/conn.json" 2>/dev/null)
log "web: $URL"
# Ctrl-] l in the terminal view: pop up the web link (the link box printed at launch is hidden once attached)
( umask 077; printf '\n  Web link for this Claude (portal login, on campus or TAMU VPN):\n\n  %s\n\n  Select it with the mouse to copy · any key closes this\n' "$URL" > "$RUN/link.txt" )
"$TMUX_BIN" -S "$SOCK" bind-key l display-popup -w 92% -h 9 -E "cat '$RUN/link.txt'; read -rsn1"
send_mail "claude-remote ready on $NODE (job $JOBID)" \
"Claude is running on $NODE in $CR_CWD.

Open (portal login, on campus or TAMU VPN):
$URL

Terminal: claude-remote -attach $JOBID"

# 4. stay alive (sleep in the background so traps fire immediately)
while "$TMUX_BIN" -S "$SOCK" has-session 2>/dev/null; do
  sleep 5 & wait $!
done
log "Claude session ended; stopping"
cleanup
