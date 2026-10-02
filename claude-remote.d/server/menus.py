"""Detect the few simple terminal dialogs the chat page can answer with buttons.

Only whitelisted titles are recognised; anything else is left to the Terminal tab.
"""
import hashlib
import re

from tmuxctl import ANSI_RE

TITLES = [
    ("trust", re.compile(r"(Is this a project you created or one you trust|trust this folder)")),
    ("switch_model", re.compile(r"^\s*Switch model\?")),
    ("permission", re.compile(r"^\s*Do you want to (proceed|make this edit|create)")),
    ("plan", re.compile(r"(Claude has written up a plan|ready to execute\. Would you like to proceed)")),
    ("after_exit", re.compile(r"Claude exited\.\s+What next\?")),
]
NUM_RE = re.compile(r"^(\d+)\.\s+(\S.*)$")
FOOTER_RE = re.compile(r"^(Esc|Enter|Tab|↑|ctrl)", re.I)


def _cols(line):
    """(column where the label text starts, has_cursor, label)"""
    stripped = line.lstrip(" ")
    cursor = stripped.startswith("❯")
    if cursor:
        stripped = stripped[1:].lstrip(" ")
    col = len(line) - len(stripped)
    return col, cursor, stripped.strip()


def detect(screen):
    """screen: plain text of the visible pane. Returns a menu dict or None."""
    lines = [ANSI_RE.sub("", l).rstrip() for l in screen.rstrip("\n").split("\n")]
    tail = lines[-40:]
    for kind, rx in TITLES:
        idx = next((i for i, l in enumerate(tail) if rx.search(l)), None)
        if idx is None:
            continue
        title = tail[idx].strip()
        body = tail[idx + 1:]
        if kind == "after_exit":
            opts = [NUM_RE.match(l.strip()) for l in body]
            options = [m.group(2).strip() for m in opts if m]
            selected, numbered = None, True
        else:
            # the option block is anchored on the line holding the ❯ cursor
            cur = next((i for i, l in enumerate(body) if _cols(l)[1] and l.startswith(" ")), None)
            if cur is None:
                continue
            col = _cols(body[cur])[0]
            lo = cur
            while lo > 0 and body[lo - 1].strip() and _cols(body[lo - 1])[0] == col:
                lo -= 1
            hi = cur
            while hi + 1 < len(body) and body[hi + 1].strip() and _cols(body[hi + 1])[0] == col \
                    and not FOOTER_RE.match(body[hi + 1].strip()):
                hi += 1
            labels = [_cols(l)[2] for l in body[lo:hi + 1]]
            numbered = all(NUM_RE.match(x) for x in labels)
            options = [NUM_RE.match(x).group(2) if numbered else x for x in labels]
            selected = cur - lo
        if len(options) < 2:
            continue
        fp = hashlib.sha1(("\n".join([title] + options)).encode()).hexdigest()[:16]
        return {"kind": kind, "title": title, "options": options,
                "selected": selected, "numbered": numbered, "fp": fp}
    return None
