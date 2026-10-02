#!/bin/bash
# Program run inside the tmux pane:  run_claude.sh LAUNCH_DIR RUN_DIR NODE
# Rebuilds a fresh-login environment (exactly what an SSH login gives you: .bash_profile,
# .bashrc, modules, CLAUDE_CONFIG_DIR, …) instead of inheriting the batch job's environment.
# No SLURM_* variables reach Claude, so jobs it submits don't inherit this job's settings.

DIR="$1"; RUN="$2"; NODE="$3"
source "$DIR/launch.env"

exec env -i \
  HOME="$HOME" USER="$USER" LOGNAME="$USER" SHELL="${SHELL:-/bin/bash}" \
  TERM=tmux-256color COLORTERM=truecolor LANG=en_US.UTF-8 PATH=/usr/bin:/bin \
  XDG_RUNTIME_DIR="$RUN/xdg" \
  CR_DIR="$DIR" CR_RUN="$RUN" CR_NODE="$NODE" CR_HERE="$CR_HERE" CR_CWD="$CR_CWD" \
  CR_HOOK_SOCK="$RUN/hook.sock" \
  bash -lic 'source "$CR_HERE/claude_env.sh"'
