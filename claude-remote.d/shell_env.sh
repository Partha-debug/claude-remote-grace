# Sourced inside a shell tab after your login files ran: add internet access, start in the
# job's folder, then hand over to a normal interactive bash.
unset TMUX TMUX_PANE
module load WebProxy >/dev/null 2>&1
export HTTP_PROXY="${http_proxy:-}" HTTPS_PROXY="${https_proxy:-}"
export no_proxy="localhost,127.0.0.1,$CR_NODE" NO_PROXY="localhost,127.0.0.1,$CR_NODE"
export CLAUDE_REMOTE=1 CLAUDE_REMOTE_JOB_ENV="$CR_DIR/slurm.env"
cd "$CR_CWD" 2>/dev/null
echo "Shell on $CR_NODE (inside your claude-remote job; shares its cores and memory)."
exec bash -i
