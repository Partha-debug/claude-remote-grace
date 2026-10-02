# claude-remote

Run [Claude Code](https://code.claude.com) inside a SLURM job on TAMU HPRC **Grace**, and use it from
the same terminal **or from any browser or phone included through the OnDemand portal**, even after
you close your SSH session. 

```
login1                                  compute node cNNN (your job)
claude-remote ──sbatch──▶ job.sh
  waits, prints link      ├─ server (Anaconda Python + aiohttp) on 0.0.0.0:PORT
  ssh -t cNNN tmux attach │     ▲ unix socket: Claude's hooks, cr-send
                          └─ tmux ─ claude <your exact args> --plugin-dir cr-bridge
                                  └ shell tabs (sh-1, sh-2, …)
browser ─https─▶ portal-grace.hprc.tamu.edu (NetID + Duo) ─/node/cNNN/PORT/<secret>/─▶ server
```

## Setup (once)

**Claude Code must be installed and logged in on the HPRC login node first**  claude-remote runs that
same installation inside the job, so nothing below works without it.

1. **Keep Claude's files on scratch.** Home has a file quota limit and Claude Code creates many
   files, so point its install and config folders at `$SCRATCH` *before* installing:
   ```bash
   mkdir -p $SCRATCH/.claude $SCRATCH/.claude_install ~/.local/share ~/.local/bin
   ln -s $SCRATCH/.claude_install ~/.local/share/claude   # installed versions
   ln -s $SCRATCH/.claude ~/.claude                       # settings, login, sessions
   cat >> ~/.bashrc <<'EOF'
   export PATH="$HOME/.local/bin:$PATH"
   export CLAUDE_CONFIG_DIR="$SCRATCH/.claude"
   EOF
   source ~/.bashrc
   ```
   (If `~/.claude` already exists, move its contents into `$SCRATCH/.claude` first.)
2. **Install Claude Code** (the login node has internet access):
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   claude --version
   ```
3. **Log in once** on the login node: run `claude`, follow the login link it prints, then `/exit`.
   The login is stored in `$CLAUDE_CONFIG_DIR`, which every compute node can read, so jobs reuse it.
4. **Put claude-remote on your PATH** (this folder must live on `/scratch` or `/home` so compute
   nodes can see it):
   ```bash
   echo 'export PATH="$SCRATCH/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
   claude-remote -dryrun          # check: prints the job it would submit
   ```

## Quick start

```bash
cd /path/to/project            # sessions are per folder, exactly like plain `claude`
claude-remote                  # 16 cores, 16 GB, 2 h, attaches this terminal to Claude
claude-remote -c               # continue the most recent conversation in this folder
claude-remote -r               # pick an earlier conversation of this folder (before the job starts)
claude-remote -r spectra       # …only those matching "spectra";  -r <session-id> resumes directly
claude-remote -n 4 -m 8 -t 6 --model opus --permission-mode plan
```

It prints a link like `https://portal-grace.hprc.tamu.edu/node/c282/36305/<secret>/`. Open it on any
device **on campus or on the TAMU VPN**, log in to the portal (NetID + Duo), Claude keeps running until the job ends.
Before attaching it waits 15 s so you can copy the link (Enter = open Claude here now, Ctrl-C = keep
it in the background). Once attached, `Ctrl-] l` shows the link again, and it is printed when you detach.

## Command line

`claude-remote [wrapper flags] [--] [claude args…]`  wrapper flags must come first; everything after
the first non-wrapper argument goes to `claude` unchanged.

| Flag | Meaning | Default |
|---|---|---|
| `-n N` | cores (`-n` followed by a non-number is Claude's own `-n/--name`) | 16 |
| `-m G` | memory in GB, or with a unit (`16G`, `4000M`) | 16 |
| `-t T` | wall time: hours (`2`, `1.5`) or `HH:MM:SS` / `D-HH:MM:SS` | 2 |
| `-A ACCOUNT` | SLURM account | your default |
| `-partition P` | partition | from `-t`: short ≤2 h, medium ≤1 d, long ≤7 d, xlong |
| `-sbatch '--x=y'` | extra sbatch option (repeatable) | |
| `-email ADDR` | email the link, waiting-approval alerts, 10-min warning, end-of-job resume command | off |
| `-noattach` | print the link and exit | |
| `-dryrun` | show what would be submitted, without submitting | |

Commands: `-status` (jobs, node, time left, folder, link) · `-attach [JOBID]` · `-kill [JOBID]` ·
`-h`. Claude's own `-h`/`-v` run locally. Refused (they make no sense in a job): `-p/--print`, `--bg`,
and Claude's management subcommands (`mcp`, `plugin`, `auth`, `update`, …). The old spellings `-np`
and `-mem` still work. A claude-remote flag placed *after* Claude's arguments is caught before
submitting.

Inside an attached terminal: `Ctrl-] d` detach · `Ctrl-] l` show the web link · `Ctrl-] s` switch
between Claude and shell tabs.
(The tmux prefix is Ctrl-], because Ctrl-B is Claude's own "run in background" key.)

## The web page

**Chat** is a live mirror of the real Claude session (typing here types into the real Claude, so
every slash command and flag behaves exactly as in the terminal):
- streamed replies, markdown with highlighted code + copy, tool calls as a compact list (tap for the
  command, diff or output), a "working" pill with what Claude is doing right now;
- **approval cards** (Allow / Always allow / Deny with a reason)  answering in the terminal works too;
  Claude's **questions** and **plan approval** as cards; other simple terminal dialogs as buttons;
- chips: **model** (`/model`), **effort** (`/effort`), **permission mode** (tap to cycle, like
  Shift+Tab), **context meter** (tokens used of the window; tap to `/compact`);
- **📎 attach**: upload files from your phone; Claude can read them without asking;
- **files Claude sends** with `cr-send` appear as cards (images inline, code/CSV/markdown previews,
  download) and reappear when a later job continues the same conversation;
- ☰: this folder's conversations (resume / new), job info, copy link, notifications, End job.

Messages you send while an approval or dialog is open are held until it is answered (typing into an
open dialog would otherwise answer it).

**Terminal** is the exact Claude screen (xterm.js with a bundled font so every symbol Claude draws
lines up), plus **+ Shell**: login shells on the same node, in the job's folder, with internet access
(they share the job's cores and memory). Key bar: Esc, Tab, ⇧Tab, sticky Ctrl, arrows, ^C, ^D, Paste,
text size.

## cr-send (Claude → your chat)

Inside the job Claude has a `cr-send` command and is told about it at session start:

```bash
cr-send plot.png results.csv -m "SSP spectrum vs. experiment"
```

Plain `cr-send …` calls are pre-approved. Files ≤ 20 MB are snapshotted, so the card shows the
version Claude sent; every send is recorded in `$SCRATCH/.claude-remote/shares.jsonl`.

## Security

Access layers, in order: portal login (NetID + Duo) → requests must come from the portal proxy
(`10.73.4.63`; direct cluster connections are refused) → a 128-bit secret in the URL → the
portal-supplied `X-Forwarded-User` must be you. The page refuses to run inside frames or when opened by another page,
uses a strict CSP scoped to its own path, and serves shared HTML/SVG only sandboxed.

## Files

```
bin/
├── claude-remote                launcher (login node)
├── README.md
└── claude-remote.d/
    ├── job.sh                   job body: server (auto-restarting), tmux, signals, emails
    ├── run_claude.sh, claude_env.sh   fresh login environment → module load WebProxy → claude
    ├── run_shell.sh, shell_env.sh     the same for shell tabs
    ├── tmux.conf
    ├── bin/tmux                 copy of the login node's tmux (made automatically; not in git)
    ├── cr-bridge/               Claude Code plugin: hooks (relay, approvals, session context), cr-send
    ├── server/                  aiohttp app: access control, live state, transcript mirror,
    │                            tmux control, dialog detection, terminal PTYs, file shares
    └── static/                  the web page (no build step) + vendored libraries and fonts
```

Per-user state lives in `$SCRATCH/.claude-remote/` (mode 0700): `jobs/<launch>/` (job script and
log, server log, `conn.json`, snapshots of shared files, uploads; pruned after 14 days),
`shares.jsonl`, and an optional `config` (e.g. `CR_PYTHON=…`, `CR_ALLOWED_IPS=…`).


## Notes and limits

- The chat mirror reads Claude Code's session transcript, whose format is internal; tested with
  Claude Code 2.1.287. If a future version changes it, the Terminal tab still shows the exact screen.
- SUs: the job is charged for what it requests (16 cores × 2 h ≈ 32 SU) even while Claude is idle.
- Browser notifications only work while the page is open; use `-email` for alerts when it is not.

