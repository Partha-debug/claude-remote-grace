"""Live state of the Claude session, shared by every connected browser.

Inputs:  hook events (unix socket), the session transcript, the tmux screen.
Outputs: chat items, status, pending approvals and dialogs, pushed to browsers.
"""
import asyncio
import calendar
import glob
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time

import menus
import sessions
from tmuxctl import Tmux, clean_text, input_line
from transcript import Transcript

SNAPSHOT_SHARE_MAX = 20 * 1024 * 1024
# Claude's status line under the input box, e.g. "⏸ plan mode on (shift+tab to cycle)".
# Changing mode fires no hook, so the screen is the only live source.
MODE_LINE = re.compile(r"(manual mode|accept edits|plan mode|auto mode|bypass permissions|don.t ask(?: mode)?) on\b")
MODE_IDS = {"manual mode": "default", "accept edits": "acceptEdits", "plan mode": "plan", "auto mode": "auto",
            "bypass permissions": "bypassPermissions", "don't ask": "dontAsk", "don't ask mode": "dontAsk"}


def screen_mode(screen):
    """Permission mode shown in the last lines of the Claude screen, or None."""
    for line in reversed(screen.rstrip("\n").split("\n")[-8:]):
        m = MODE_LINE.search(line)
        if m:
            return MODE_IDS.get(m.group(1).replace("\u2019", "'"), m.group(1))
    return None
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
TEXT_EXT = {".py", ".sh", ".bash", ".slurm", ".sbatch", ".job", ".txt", ".log", ".out", ".err", ".md", ".csv",
            ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".in", ".inp", ".lmp", ".data",
            ".xyz", ".pdb", ".gro", ".itp", ".top", ".mdp", ".tex", ".bib", ".c", ".h", ".cpp", ".hpp", ".f",
            ".f90", ".f95", ".jl", ".r", ".m", ".js", ".ts", ".css", ".xml", ".dat", ".ipynb", ".patch", ".diff"}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".%03dZ" % (int(time.time() * 1000) % 1000)


def iso_epoch(ts):
    """'2026-10-02T19:31:10.123Z' → epoch seconds (UTC); 0 if unparsable."""
    try:
        return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0


def parse_cr_send(cmd, cwd):
    """'cd $SCRATCH/x && cr-send a.png b.png -m "cap"' → (folder, [paths], caption), or None.

    Follows simple `cd … &&` / `;` chains so relative paths resolve the way the shell saw them."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    folder, seg = cwd, []
    for tok in tokens + [";"]:
        if tok in ("&&", ";", "||", "&", "|"):
            if seg and seg[0] == "cd" and len(seg) > 1:
                folder = os.path.normpath(os.path.join(folder, os.path.expanduser(os.path.expandvars(seg[1]))))
            elif seg and os.path.basename(seg[0]) == "cr-send":
                caption, paths, i = "", [], 1
                while i < len(seg):
                    if seg[i] in ("-m", "--message") and i + 1 < len(seg):
                        caption = seg[i + 1]
                        i += 2
                    else:
                        paths.append(seg[i])
                        i += 1
                return folder, paths, caption
            seg = []
        else:
            seg.append(tok)
    return None


def same_call(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


class Live:
    def __init__(self, a, tmux: Tmux, broadcast, has_clients, send_mail):
        self.a = a
        self.tmux = tmux
        self.broadcast = broadcast          # async fn(msg)
        self.has_clients = has_clients      # fn() → bool
        self.send_mail = send_mail          # fn(subject, body)
        self.transcript = None
        self.session_id = None
        self.extra = []                     # chat items not from the transcript (files, notices)
        self.status = {"busy": False, "waiting": None, "model": None, "effort": None,
                       "mode": None, "claude_running": True, "activity": None, "subagents": 0,
                       "context": None, "model_id": None}
        self.perms = {}                     # id → pending approval
        self.held = []                      # web messages waiting for a dialog to close
        self.seen_cids = set()
        self.menu = None
        self.menu_misses = 0
        self.last_submit = 0.0
        self.last_mail = 0.0
        self.shares = {}                    # share id → record
        self.streams = {}                   # MessageDisplay message_id → {"chunks": {index: text}, "final": bool}
        self.share_dir = os.path.join(a.dir, "shares")
        self.shown = {}                     # path → [epoch seconds] of every file card shown in this job
        self.restored_sessions = set()
        self.scanned_tools = set()

    # ── snapshot for a newly connected browser ─────────────────────────────
    def items(self):
        its = list(self.transcript.items) if self.transcript else []
        its += self.extra
        its.sort(key=lambda i: i.get("ts") or "")
        return its[-600:]

    def snapshot(self):
        return {"type": "snapshot", "items": self.items(), "status": self.status,
                "perms": [self._perm_public(p) for p in self.perms.values()],
                "menu": self.menu, "held": [h["text"] for h in self.held],
                "truncated": bool(self.transcript and self.transcript.start > 0),
                "streams": {k: self._stream_text(k) for k in self.streams},
                "session_id": self.session_id}

    async def push_status(self):
        await self.broadcast({"type": "status2", "status": self.status})

    # ── hook events ───────────────────────────────────────────────────────
    async def hook(self, ev, d, effort):
        mode = d.get("permission_mode")
        if mode:
            self.status["mode"] = mode
        lvl = effort
        if not lvl and isinstance(d.get("effort"), dict):
            lvl = d["effort"].get("level")
        if lvl:
            self.status["effort"] = lvl
        if ev == "SessionStart":
            path = d.get("transcript_path")
            if d.get("model"):
                self.status["model"] = d["model"] if isinstance(d["model"], str) else d["model"].get("display_name")
                self.status["model_id"] = d["model"] if isinstance(d["model"], str) else d["model"].get("id")
            if path and (not self.transcript or self.transcript.path != path):
                self.session_id = d.get("session_id")
                self.transcript = Transcript(path)
                self.transcript.load_initial()
                self._hints()
                self._restore_shares(self.transcript.items)
                await self.broadcast(self.snapshot())
        elif ev == "MessageDisplay":
            mid = d.get("message_id") or "?"
            st = self.streams.setdefault(mid, {"chunks": {}, "final": False})
            st["chunks"][int(d.get("index") or 0)] = d.get("delta") or ""
            st["final"] = st["final"] or bool(d.get("final"))
            await self.broadcast({"type": "stream", "mid": mid, "text": self._stream_text(mid), "final": st["final"]})
            return
        elif ev == "PreToolUse":
            ti = d.get("tool_input") or {}
            summary = ti.get("command") or ti.get("file_path") or ti.get("pattern") or ti.get("url") or ti.get("description") or ""
            self.status["activity"] = {"tool": d.get("tool_name"), "summary": str(summary)[:200], "since": time.time()}
        elif ev in ("SubagentStart", "SubagentStop"):
            self.status["subagents"] = max(0, self.status["subagents"] + (1 if ev == "SubagentStart" else -1))
        elif ev == "UserPromptSubmit":
            self.status["busy"] = True
            self.status["waiting"] = None
            self.last_submit = time.time()
        elif ev in ("Stop", "StopFailure"):
            self.status["busy"] = False
            self.status["waiting"] = None
            self.status["activity"] = None
            self.status["subagents"] = 0
            await self._clear_streams(final_only=False)
            await self._release_all("turn ended")
        elif ev in ("PostToolUse", "PostToolUseFailure", "PermissionDenied"):
            self.status["activity"] = None
            for p in list(self.perms.values()):
                if p["tool_name"] == d.get("tool_name") and same_call(p["tool_input"], d.get("tool_input")):
                    await self._release(p["id"], "answered in the terminal")
        elif ev == "Notification":
            nt = d.get("notification_type") or ""
            if "idle" in nt:
                self.status["waiting"] = "input"
        elif ev == "PostModelSwitch":
            for k in ("model", "new_model", "to_model", "model_display_name"):
                v = d.get(k)
                if isinstance(v, str) and v:
                    self.status["model"] = v
                    self.status["model_id"] = v
        elif ev == "SessionEnd":
            await self._release_all("session ended")
        await self.push_status()

    def _stream_text(self, mid):
        st = self.streams.get(mid) or {"chunks": {}}
        return "".join(st["chunks"][i] for i in sorted(st["chunks"]))

    async def _clear_streams(self, final_only=True):
        gone = [k for k, v in self.streams.items() if v["final"] or not final_only]
        for k in gone:
            self.streams.pop(k, None)
        if gone:
            await self.broadcast({"type": "stream_clear", "mids": gone})

    # ── sessions of this folder (for the resume drawer) and older history ───
    def list_sessions(self):
        return [dict(x, current=x["id"] == self.session_id) for x in sessions.list_sessions(self.a.cwd)]

    def history(self):
        if not self.transcript:
            return {"type": "history", "items": [], "more": False}
        items, more = self.transcript.earlier()
        items = items + self._restore_shares(items)
        return {"type": "history", "items": items, "more": more}

    def _hints(self):
        if self.transcript:
            for k in ("model", "effort"):
                if self.transcript.hints.get(k):
                    self.status[k] = self.transcript.hints.pop(k)
            self._context()

    def _context(self):
        """Context meter: tokens in the window after the latest reply, and the window size."""
        used = self.transcript.context if self.transcript else None
        if used is None:
            self.status["context"] = None
            return
        mid = str(self.status.get("model_id") or "") + str(self.status.get("model") or "")
        window = 1_000_000 if ("[1m]" in mid.lower() or "1m context" in mid.lower() or used > 200_000) else 200_000
        self.status["context"] = {"used": int(used), "window": window}

    # ── approvals (PermissionRequest hook long-polls here) ─────────────────
    def _perm_public(self, p):
        return {k: p[k] for k in ("id", "tool_name", "tool_input", "suggestions", "created", "mode")}

    async def permission(self, d):
        pid = secrets.token_hex(6)
        fut = asyncio.get_running_loop().create_future()
        p = {"id": pid, "tool_name": d.get("tool_name", "?"), "tool_input": d.get("tool_input") or {},
             "suggestions": d.get("permission_suggestions") or [], "created": time.time(),
             "mode": d.get("permission_mode"), "fut": fut, "seen_dialog": False, "gone": 0}
        self.perms[pid] = p
        self.status["waiting"] = "permission"
        await self.broadcast({"type": "perm", "perm": self._perm_public(p)})
        await self.push_status()
        try:
            out = await fut
        except asyncio.CancelledError:
            out = ""
        finally:
            self.perms.pop(pid, None)
            if not self.perms and self.status["waiting"] == "permission":
                self.status["waiting"] = None
            await self.broadcast({"type": "perm_done", "id": pid})
            await self.push_status()
        return out

    async def _release(self, pid, why):
        p = self.perms.get(pid)
        if p and not p["fut"].done():
            p["fut"].set_result("")
            await self.broadcast({"type": "notice", "text": f"Approval {why}."})

    async def _release_all(self, why):
        for pid in list(self.perms):
            await self._release(pid, why)

    async def perm_answer(self, m):
        p = self.perms.get(m.get("id"))
        if not p or p["fut"].done():
            await self.broadcast({"type": "perm_done", "id": m.get("id")})
            return
        choice = m.get("choice")
        if choice == "allow" and p["tool_name"] in ("ExitPlanMode", "AskUserQuestion"):
            dec = {"behavior": "allow", "updatedInput": p["tool_input"]}     # interactive tools need their input back
        elif choice == "allow":
            dec = {"behavior": "allow"}
        elif choice == "always":
            dec = {"behavior": "allow", "updatedPermissions": p["suggestions"]}
        elif choice == "answers":          # AskUserQuestion
            dec = {"behavior": "allow", "updatedInput": dict(p["tool_input"], answers=m.get("answers") or {})}
        else:
            dec = {"behavior": "deny", "message": (m.get("message") or "The user declined this from the web chat.")[:2000]}
        p["fut"].set_result(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": dec}}))

    # ── shares (cr-send) ──────────────────────────────────────────────────
    # Every share is also written to an index shared by all jobs ($STATE/shares.jsonl), so a later
    # job that continues the same conversation can show the same file cards again.
    def _index_path(self):
        return os.path.join(os.path.dirname(os.path.dirname(self.a.dir)), "shares.jsonl")

    def _register(self, path, caption, ts, served=None, restored=False):
        """Create a share record + chat item for one file. Returns the item, or None."""
        try:
            st = os.stat(served if served and os.path.exists(served) else path)
        except OSError:
            return None
        sid = secrets.token_urlsafe(12)
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        if served is None:
            served = path
            if st.st_size <= SNAPSHOT_SHARE_MAX:      # snapshot: you review the version Claude sent
                os.makedirs(os.path.join(self.share_dir, sid), mode=0o700, exist_ok=True)
                served = os.path.join(self.share_dir, sid, name)
                shutil.copy2(path, served)
        elif not os.path.exists(served):
            served = path                              # old snapshot pruned: fall back to the live file
        kind = "image" if ext in IMAGE_EXT else ("svg" if ext == ".svg" else
                "pdf" if ext == ".pdf" else "text" if (ext in TEXT_EXT or not ext) else "binary")
        self.shares[sid] = {"id": sid, "name": name, "path": path, "served": served, "size": st.st_size, "kind": kind}
        self.shown.setdefault(path, []).append(iso_epoch(ts))
        changed = False
        if restored and served == path:                # live file: has it changed since it was sent?
            try:
                changed = os.path.getmtime(path) > iso_epoch(ts) + 2
            except OSError:
                pass
        item = {"id": "f" + sid, "kind": "file", "ts": ts, "share": sid, "name": name, "path": path,
                "size": st.st_size, "filekind": kind, "caption": caption or "", "restored": restored, "changed": changed}
        self.extra.append(item)
        return item

    async def share(self, d):
        names = []
        for path in d.get("paths", [])[:20]:
            ts = now_iso()
            item = self._register(path, d.get("caption", ""), ts)
            if not item:
                continue
            rec = self.shares[item["share"]]
            try:
                with open(self._index_path(), "a") as f:
                    os.chmod(self._index_path(), 0o600)
                    f.write(json.dumps({"session": self.session_id, "ts": ts, "path": path, "served": rec["served"],
                                        "caption": d.get("caption", "")}) + "\n")
            except OSError:
                pass
            await self.broadcast({"type": "items", "ops": [["add", item]]})
            names.append(item["name"])
        if not names:
            return {"ok": False, "text": "cr-send: nothing was shared"}
        return {"ok": True, "text": "Shown in the user's web chat: " + ", ".join(names)}

    def _restore_shares(self, items):
        """Re-create file cards for this conversation (from the index, then from cr-send calls in the transcript)."""
        added = []
        if self.session_id and self.session_id not in self.restored_sessions:
            self.restored_sessions.add(self.session_id)
            try:
                for line in open(self._index_path()):
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("session") == self.session_id and not self._already_shown(r["path"], r["ts"]):
                        it = self._register(r["path"], r.get("caption"), r["ts"], served=r.get("served"), restored=True)
                        if it:
                            added.append(it)
            except OSError:
                pass
        for t in items:   # older sends (before the index existed): parse the cr-send command itself
            if t.get("kind") != "tool" or t.get("name") != "Bash" or t["id"] in self.scanned_tools:
                continue
            cmd = (t.get("input") or {}).get("command", "")
            if "cr-send" not in cmd or not str(t.get("result") or "").startswith("Shown in the user"):
                continue
            self.scanned_tools.add(t["id"])
            parsed = parse_cr_send(cmd, t.get("cwd") or self.a.cwd)
            if not parsed:
                continue
            folder, paths, caption = parsed
            for p in paths:
                full = os.path.normpath(os.path.join(folder, os.path.expanduser(os.path.expandvars(p))))
                if self._already_shown(full, t.get("ts") or now_iso()):
                    continue                           # shown live in this job, or restored from the index
                it = self._register(full, caption, t.get("ts") or now_iso(), served=full, restored=True)
                if it:
                    added.append(it)
        return added

    def _already_shown(self, path, ts, window=120):
        t = iso_epoch(ts)
        return any(abs(t - x) <= window for x in self.shown.get(path, []))

    def share_file(self, sid):
        rec = self.shares.get(sid)
        if not rec:
            return None
        mime = mimetypes.guess_type(rec["name"])[0] or "application/octet-stream"
        return rec, mime

    # ── messages from the web page ───────────────────────────────────────
    async def web_send(self, text, cid):
        if cid in self.seen_cids:
            return
        self.seen_cids.add(cid)
        text = clean_text(text).strip("\n")
        if not text.strip():
            return
        if self.perms or self.menu:
            self.held.append({"text": text, "cid": cid})
            await self.broadcast({"type": "held", "held": [h["text"] for h in self.held]})
            return
        await self._inject(text, cid)

    async def _inject(self, text, cid):
        async with self.tmux.lock:
            screen = await self.tmux.capture(escapes=True)
            if menus.detect(screen):
                self.held.insert(0, {"text": text, "cid": cid})
                await self.broadcast({"type": "held", "held": [h["text"] for h in self.held]})
                return
            draft, grey = input_line(screen)
            if draft is None:
                await self.broadcast({"type": "send_failed", "cid": cid, "text": text,
                                      "why": "Claude's input box isn't visible (a dialog or picker may be open). Use the Terminal tab."})
                return
            if draft and not grey:
                await self.broadcast({"type": "send_failed", "cid": cid, "text": text,
                                      "why": "There is unsent text in Claude's input box (typed in a terminal). Send or clear it first."})
                return
            before = self.last_submit
            await self.tmux.paste(text)
            await asyncio.sleep(0.3)
            await self.tmux.keys("enter")
            if not text.startswith("/"):
                for _ in range(15):
                    await asyncio.sleep(0.2)
                    if self.last_submit > before:
                        break
                else:
                    # a "/" or "@" suggestion popup may have swallowed Enter
                    draft2, grey2 = input_line(await self.tmux.capture(escapes=True))
                    if draft2 and not grey2:
                        await self.tmux.keys("enter")
            await self.broadcast({"type": "sent", "cid": cid})

    async def web_key(self, key):
        if key == "esc":
            if self.status["busy"]:
                await self.tmux.keys("esc")
        elif key == "btab":
            if self.perms or self.menu:
                await self.broadcast({"type": "notice", "text": "Answer the open question first, then change the mode."})
                await self.push_status()
                return
            before = screen_mode(await self.tmux.capture())
            await self.tmux.keys("btab")
            for _ in range(16):                      # wait (up to ~2 s) for Claude to redraw its status line
                await asyncio.sleep(0.12)
                now = screen_mode(await self.tmux.capture())
                if now and now != before:
                    break
            if now:
                self.status["mode"] = now
            await self.push_status()

    async def menu_pick(self, fp, index):
        async with self.tmux.lock:
            m = menus.detect(await self.tmux.capture())
            if not m or m["fp"] != fp or not (0 <= index < len(m["options"])):
                await self.broadcast({"type": "notice", "text": "That dialog changed; look again."})
                return
            if m["numbered"]:
                await self.tmux.literal(str(index + 1))
                if m["kind"] in ("switch_model",):
                    await asyncio.sleep(0.15)
            else:
                delta = index - (m["selected"] or 0)
                for _ in range(abs(delta)):
                    await self.tmux.keys("down" if delta > 0 else "up")
                    await asyncio.sleep(0.05)
                await self.tmux.keys("enter")
        await asyncio.sleep(0.4)
        await self._poll_menu(force=True)

    # ── background polling ─────────────────────────────────────────────────
    async def _poll_menu(self, force=False):
        screen = await self.tmux.capture()
        mode = screen_mode(screen)
        if mode and mode != self.status.get("mode"):
            self.status["mode"] = mode               # changed in a terminal (Shift+Tab) or by Claude
            await self.push_status()
        m = menus.detect(screen)
        # a pending approval whose dialog vanished was answered in the terminal
        for p in list(self.perms.values()):
            on_screen = bool(m and m["kind"] == "permission")
            if on_screen:
                p["seen_dialog"], p["gone"] = True, 0
            elif p["seen_dialog"] or time.time() - p["created"] > 8:
                p["gone"] += 1
                if p["gone"] >= 3:
                    await self._release(p["id"], "answered in the terminal")
        if m and m["kind"] == "permission" and self.perms:
            m = None                          # shown as an approval card instead
        if (m or None) != (self.menu or None):
            self.menu = m
            await self.broadcast({"type": "menu", "menu": m})

    def _find_transcript(self):
        """Fallback when SessionStart was missed: Claude's own status file names the session."""
        cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        best = None
        for f in glob.glob(os.path.join(cfg, "sessions", "*.json")):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            if d.get("cwd") == self.a.cwd and (best is None or d.get("updatedAt", 0) > best.get("updatedAt", 0)):
                best = d
        if not best or not best.get("sessionId"):
            return None
        enc = re.sub(r"[^A-Za-z0-9]", "-", self.a.cwd)[:200]
        hits = glob.glob(os.path.join(cfg, "projects", enc + "*", best["sessionId"] + ".jsonl"))
        return (best["sessionId"], hits[0]) if hits else None

    async def loop(self):
        tick = 0
        while True:
            await asyncio.sleep(0.5)
            tick += 1
            try:
                if not self.transcript and tick % 10 == 0:
                    found = self._find_transcript()
                    if found:
                        self.session_id, path = found
                        self.transcript = Transcript(path)
                        self.transcript.load_initial()
                        self._hints()
                        self._restore_shares(self.transcript.items)
                        await self.broadcast(self.snapshot())
                if not await self.tmux.alive():
                    if self.status["claude_running"]:
                        self.status["claude_running"] = False
                        await self.push_status()
                    continue
                if self.transcript:
                    ops = self.transcript.poll()
                    if ops:
                        self._hints()
                        restored = self._restore_shares([it for op, it in ops if it["kind"] == "tool"])
                        ops = list(ops) + [("add", it) for it in restored]
                        if any(op == "add" and it["kind"] == "assistant" for op, it in ops):
                            await self._clear_streams(final_only=True)
                        await self.broadcast({"type": "items", "ops": [[op, it] for op, it in ops]})
                        await self.push_status()
                if tick % 2 == 0 and (self.has_clients() or self.held or self.perms):
                    await self._poll_menu()
                if self.held and not self.perms and not self.menu:
                    h = self.held.pop(0)
                    await self.broadcast({"type": "held", "held": [x["text"] for x in self.held]})
                    await self._inject(h["text"], h["cid"])
                await self._maybe_mail()
            except Exception as e:  # keep the loop alive whatever happens
                print(f"live loop error: {e!r}", flush=True)

    async def _maybe_mail(self):
        if not self.perms or self.has_clients():
            return
        oldest = min(p["created"] for p in self.perms.values())
        if time.time() - oldest > 120 and time.time() - self.last_mail > 900:
            self.last_mail = time.time()
            p = next(iter(self.perms.values()))
            what = p["tool_input"].get("command") or p["tool_input"].get("file_path") or ""
            self.send_mail(f"claude-remote: Claude is waiting for your approval ({self.a.node})",
                           f"Claude wants to use {p['tool_name']}: {what}\n\nOpen the chat: {self.a.url}")
