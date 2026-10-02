"""Talking to the real Claude TUI through tmux: capture the screen, type text, press keys."""
import asyncio
import re

CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # C0 controls except \t \n (and ESC)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
KEYS = {"esc": "Escape", "btab": "BTab", "tab": "Tab", "up": "Up", "down": "Down",
        "left": "Left", "right": "Right", "enter": "Enter"}


class Tmux:
    def __init__(self, tmux_bin, sock, target="claude"):
        self.bin, self.sock, self.target = tmux_bin, sock, target
        self.lock = asyncio.Lock()       # one injection at a time

    async def run(self, *args, data=None):
        p = await asyncio.create_subprocess_exec(
            self.bin, "-u", "-S", self.sock, *args,
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"LANG": "en_US.UTF-8", "PATH": "/usr/bin:/bin"})
        out, _ = await p.communicate(data)
        return p.returncode, out.decode("utf-8", "replace")

    async def alive(self):
        rc, _ = await self.run("has-session", "-t", self.target)
        return rc == 0

    async def capture(self, escapes=False, history=0):
        args = ["capture-pane", "-p", "-t", self.target]
        if escapes:
            args.append("-e")
        if history:
            args += ["-S", f"-{history}"]
        rc, out = await self.run(*args)
        return out if rc == 0 else ""

    async def keys(self, *names):
        await self.run("send-keys", "-t", self.target, *[KEYS.get(n, n) for n in names])

    async def literal(self, text):
        await self.run("send-keys", "-t", self.target, "-l", text)

    async def paste(self, text):
        """Bracketed paste (multi-line text stays one message until Enter)."""
        await self.run("load-buffer", "-b", "cr", "-", data=text.encode("utf-8"))
        await self.run("paste-buffer", "-b", "cr", "-d", "-p", "-t", self.target)


def clean_text(text):
    """Remove control characters (an ESC could end the bracketed paste and become keystrokes)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x1b", "")
    return CTRL_RE.sub("", text)


def input_line(screen_with_escapes):
    """Return (text_in_input_box, is_placeholder) from the last line that starts with ❯ at column 0.

    The input box is drawn between two horizontal rules near the bottom. Claude shows grey
    suggestion text there when it is empty; real typed text is drawn in the default colour.
    """
    lines = screen_with_escapes.rstrip("\n").split("\n")
    for raw in reversed(lines[-30:]):
        plain = ANSI_RE.sub("", raw)
        if plain.startswith("❯"):
            text = plain[1:].strip()
            if not text:
                return "", False
            # what styling precedes the first text character after the prompt?
            after = raw.split("❯", 1)[1]
            lead = re.match(r"((?:\x1b\[[0-9;]*m|\s)*)", after).group(1)
            grey = bool(re.search(r"\x1b\[(?:[0-9;]*;)?(2|90|37|38;5;(?:24[0-9]|25[0-5]|8)|38;2;1[0-6][0-9];)", lead))
            return text, grey
    return None, False
