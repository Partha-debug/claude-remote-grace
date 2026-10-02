#!/bin/bash
# SessionStart: tell Claude about the web chat and cr-send (stdout becomes context).
cat >/dev/null
[[ -n "${CR_HOOK_SOCK:-}" ]] || exit 0
cat <<TXT
This Claude Code session runs inside a SLURM job (claude-remote) on HPRC Grace node ${CR_NODE:-?}. The user may be following it from a web chat on a phone or laptop, as well as from a terminal.
- To show the user a file for review (plots/images, scripts, job files, logs, PDFs, CSV), run: cr-send <path> [<path>…] [-m "short caption"]. It appears as a card in their chat. Prefer PNG images for plots.
- This job's SLURM variables were removed from your environment so jobs you submit with sbatch don't inherit them; they are saved in ${CLAUDE_REMOTE_JOB_ENV:-the job folder} if you need them.
TXT
