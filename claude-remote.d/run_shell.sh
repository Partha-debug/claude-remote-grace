#!/bin/bash
# A plain shell on the compute node (Terminal tab → "+"), same fresh-login environment as Claude
# (HPRC's banner/quota scripts only print on login nodes, so compute-node shells start clean):
#   run_shell.sh LAUNCH_DIR RUN_DIR NODE
DIR="$1"; RUN="$2"; NODE="$3"
source "$DIR/launch.env"
exec env -i \
  HOME="$HOME" USER="$USER" LOGNAME="$USER" SHELL="${SHELL:-/bin/bash}" \
  TERM=tmux-256color COLORTERM=truecolor LANG=en_US.UTF-8 PATH=/usr/bin:/bin \
  XDG_RUNTIME_DIR="$RUN/xdg" CR_DIR="$DIR" CR_NODE="$NODE" CR_HERE="$CR_HERE" CR_CWD="$CR_CWD" \
  bash -lic 'source "$CR_HERE/shell_env.sh"'
