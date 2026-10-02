"""Incremental reader for Claude Code session transcripts (…/projects/<cwd>/<session>.jsonl).

The format is internal to Claude Code and changes between versions, so everything here
is tolerant: unknown line types are ignored, and any parse problem only drops that line.
Produces chat items:
  user      {text, queued}
  assistant {text}
  thinking  {text}
  tool      {name, input, tool_id, result, is_error}
  command   {name, args, output}
  notice    {text}
"""
import json
import os
import re

MAX_INITIAL_BYTES = 4 * 1024 * 1024     # big transcripts: start from the last 4 MB
MAX_RESULT_CHARS = 20000
MAX_ITEMS = 3000

CMD_RE = re.compile(r"<command-name>(.*?)</command-name>.*?<command-args>(.*?)</command-args>", re.S)
TAG_RE = re.compile(r"<(local-command-stdout|local-command-stderr)>(.*?)</\1>", re.S)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _text_of(content):
    """tool_result content: str or list of blocks → str"""
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text", ""))
            elif b.get("type") == "image":
                out.append("[image]")
    return "\n".join(out)


class Transcript:
    def __init__(self, path, id_prefix="i"):
        self.path = path
        self.id_prefix = id_prefix
        self.start = 0               # first byte that has been parsed (older history lies before it)
        self.offset = 0
        self.partial = b""
        self.items = []
        self.by_id = {}
        self.tools = {}           # tool_use_id → item
        self.last_command = None
        self.truncated = False
        self.next_id = 0
        self.hints = {}           # model / effort learned from command output
        self.context = None       # tokens in the context window after the latest reply

    # ── public ────────────────────────────────────────────────────────────
    def load_initial(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size > MAX_INITIAL_BYTES:
            self.offset = self.start = size - MAX_INITIAL_BYTES
            self.truncated = True
            self._skip_partial = True
        else:
            self._skip_partial = False
        return self.poll()

    def poll(self):
        """Read appended lines; return list of (op, item) with op 'add' or 'update'."""
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return []
        if not data:
            return []
        self.offset += len(data)
        data = self.partial + data
        lines = data.split(b"\n")
        self.partial = lines.pop()             # incomplete last line (if any)
        if getattr(self, "_skip_partial", False) and lines:
            lines.pop(0)                       # first line after a mid-file seek is partial
            self._skip_partial = False
        changes = []
        for raw in lines:
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            try:
                changes.extend(self._line(d))
            except Exception:
                continue
        if len(self.items) > MAX_ITEMS:
            drop = self.items[: len(self.items) - MAX_ITEMS]
            self.items = self.items[len(drop):]
            for it in drop:
                self.by_id.pop(it["id"], None)
        return changes

    def earlier(self, nbytes=MAX_INITIAL_BYTES):
        """Parse the chunk just before what has been read so far; returns (items, more_left)."""
        if self.start <= 0:
            return [], False
        lo = max(0, self.start - nbytes)
        try:
            with open(self.path, "rb") as f:
                f.seek(lo)
                data = f.read(self.start - lo)
        except OSError:
            return [], False
        if lo > 0:
            nl = data.find(b"\n")
            if nl < 0:                         # one huge line spans the whole chunk: step over it
                self.start = lo
                return [], True
            lo += nl + 1                       # the partial first line belongs to the next chunk
            data = data[nl + 1:]
        lines = data.split(b"\n")
        older = Transcript(self.path, id_prefix=f"h{self.start}-")
        for raw in lines:
            if not raw.strip():
                continue
            try:
                older._line(json.loads(raw))
            except Exception:
                continue
        self.start = lo
        return older.items, lo > 0

    # ── internals ─────────────────────────────────────────────────────────
    def _new(self, kind, ts=None, **kw):
        self.next_id += 1
        it = {"id": f"{self.id_prefix}{self.next_id}", "kind": kind, "ts": ts}
        it.update(kw)
        self.items.append(it)
        self.by_id[it["id"]] = it
        return ("add", it)

    def _line(self, d):
        t = d.get("type")
        if d.get("isSidechain"):
            return []
        ts = d.get("timestamp")
        if t == "assistant":
            return self._assistant(d, ts)
        if t == "user":
            return self._user(d, ts)
        if t == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "queued_command" and a.get("commandMode") == "prompt":
                return [self._new("user", ts, text=a.get("prompt", ""), queued=True)]
            if a.get("type") == "model":
                name = (a.get("identity") or {}).get("marketingName")
                if name:
                    self.hints["model"] = name
            return []
        if t == "system":
            if d.get("subtype") == "compact_boundary":
                post = (d.get("compactMetadata") or {}).get("postTokens")
                if post:
                    self.context = post
                return [self._new("notice", ts, text="Conversation compacted")]
            return []
        return []

    def _assistant(self, d, ts):
        m = d.get("message") or {}
        out = []
        u = m.get("usage") or {}
        if u.get("input_tokens") is not None:
            # same input-only formula Claude Code uses for its "used_percentage"
            self.context = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) \
                + (u.get("cache_read_input_tokens") or 0)
        if m.get("model") == "<synthetic>":
            txt = _text_of(m.get("content"))
            if txt.strip():
                out.append(self._new("notice", ts, text=txt.strip()))
            return out
        for b in m.get("content") or []:
            bt = b.get("type")
            if bt == "text" and b.get("text", "").strip():
                out.append(self._new("assistant", ts, text=b["text"]))
            elif bt == "thinking" and b.get("thinking", "").strip():
                out.append(self._new("thinking", ts, text=b["thinking"]))
            elif bt == "tool_use":
                op = self._new("tool", ts, name=b.get("name", "?"), input=b.get("input") or {},
                               tool_id=b.get("id"), result=None, is_error=False, cwd=d.get("cwd"))
                self.tools[b.get("id")] = op[1]
                out.append(op)
        return out

    def _user(self, d, ts):
        m = d.get("message") or {}
        c = m.get("content")
        if isinstance(c, list):
            out = []
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    it = self.tools.get(b.get("tool_use_id"))
                    if it is not None:
                        res = _text_of(b.get("content"))
                        if len(res) > MAX_RESULT_CHARS:
                            res = res[:MAX_RESULT_CHARS] + f"\n… ({len(res) - MAX_RESULT_CHARS} more characters)"
                        it["result"] = res
                        it["is_error"] = bool(b.get("is_error"))
                        out.append(("update", it))
                elif b.get("type") == "text" and not d.get("isMeta"):
                    out.extend(self._user_text(b.get("text", ""), d, ts))
                elif b.get("type") == "image" and not d.get("isMeta"):
                    out.append(self._new("user", ts, text="[image]", queued=False))
            return out
        if isinstance(c, str):
            if d.get("isMeta"):
                return []
            return self._user_text(c, d, ts)
        return []

    def _user_text(self, text, d, ts):
        s = text.strip()
        if not s:
            return []
        if d.get("isCompactSummary"):
            return [self._new("notice", ts, text="Conversation compacted (summary of earlier messages)")]
        if s.startswith("<command-name>"):
            mm = CMD_RE.search(s)
            name, args = (mm.group(1), mm.group(2).strip()) if mm else (s, "")
            op = self._new("command", ts, name=name, args=args, output="")
            self.last_command = op[1]
            return [op]
        tm = TAG_RE.search(s)
        if tm and s.startswith("<local-command"):
            out_text = ANSI_RE.sub("", tm.group(2)).strip()
            if self.last_command is not None:
                self.last_command["output"] = (self.last_command["output"] + "\n" + out_text).strip()
                self._learn(self.last_command)
                return [("update", self.last_command)]
            return [self._new("notice", ts, text=out_text)] if out_text else []
        if s.startswith("<task-notification>"):
            summ = re.search(r"<summary>(.*?)</summary>", s, re.S)
            return [self._new("notice", ts, text=(summ.group(1).strip() if summ else "Background task finished"))]
        if s.startswith("[Request interrupted"):
            return [self._new("notice", ts, text="Interrupted")]
        if s.startswith("<") and re.match(r"<(system-reminder|local-command-caveat|user-memory-input|bash-)", s):
            return []
        return [self._new("user", ts, text=text, queued=False)]

    def _learn(self, cmd):
        """Pick up model / effort from /model and /effort output."""
        out = cmd.get("output", "")
        mm = re.search(r"Set model to\s+`?([^`\n]+?)`?(\s+and|\s*$)", out)
        if mm:
            self.hints["model"] = mm.group(1).strip()
        em = re.search(r"Set effort level to\s+(\w+)", out)
        if em:
            self.hints["effort"] = em.group(1)
