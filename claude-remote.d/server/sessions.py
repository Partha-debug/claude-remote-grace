"""A folder's saved Claude conversations — used by the web server's ☰ sheet and by the
login-node picker (`claude-remote -r`).

    python sessions.py CWD [SEARCH]    →  one line per conversation:  <session-id>\t<label>
"""
import glob
import json
import os
import re
import sys
import time


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def project_dirs(cwd, cfg=None):
    """Claude files sessions under projects/<cwd with every non-alphanumeric → '-'> (long names are
    truncated to 200 characters plus a hash)."""
    cfg = cfg or config_dir()
    enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    if len(enc) <= 200:
        d = os.path.join(cfg, "projects", enc)
        return [d] if os.path.isdir(d) else []
    return [d for d in glob.glob(os.path.join(cfg, "projects", enc[:200] + "*")) if os.path.isdir(d)]


def session_title(path):
    """Best short title: the latest ai-title / custom title, else the first prompt."""
    title, first = None, None
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            tail = f.read()
    except OSError:
        return ""
    for chunk in (tail, head):
        for raw in reversed(chunk.split(b"\n")) if chunk is tail else chunk.split(b"\n"):
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            t = d.get("type")
            if chunk is tail and t in ("ai-title", "custom-title", "summary") and not title:
                title = d.get("title") or d.get("aiTitle") or d.get("customTitle") or d.get("summary")
            if chunk is head and t == "user" and not first and not d.get("isMeta"):
                c = (d.get("message") or {}).get("content")
                if isinstance(c, str) and not c.startswith("<"):
                    first = c
        if title:
            break
    return (title or first or "").strip().replace("\n", " ")[:120]


def list_sessions(cwd, limit=40, cfg=None):
    out = []
    for d in project_dirs(cwd, cfg):
        for f in glob.glob(os.path.join(d, "*.jsonl")):
            try:
                st = os.stat(f)
            except OSError:
                continue
            out.append((st.st_mtime, f, st.st_size))
    out.sort(reverse=True)
    return [{"id": os.path.basename(f)[:-6], "mtime": int(m), "size": s, "title": session_title(f)}
            for m, f, s in out[:limit]]


def _ago(t):
    d = time.time() - t
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 7 * 86400:
        return f"{int(d // 86400)} d ago"
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _size(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


if __name__ == "__main__":
    cwd = sys.argv[1]
    search = " ".join(sys.argv[2:]).lower()
    for s in list_sessions(cwd):
        if search and search not in (s["title"] + " " + s["id"]).lower():
            continue
        title = re.sub(r"\s+", " ", s["title"] or "(untitled)")
        print(f"{s['id']}\t{title[:64]}  ·  {_ago(s['mtime'])} · {_size(s['size'])} · {s['id'][:8]}")
