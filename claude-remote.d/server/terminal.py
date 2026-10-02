"""PTY bridge: one `tmux attach` client per open Terminal tab."""
import asyncio
import codecs
import fcntl
import os
import pty
import signal
import struct
import termios


class TerminalClient:
    """Runs `tmux -S SOCK attach -t claude` in a pseudo-terminal and relays bytes."""

    def __init__(self, tmux_bin, sock, on_output, cols=100, rows=30, target="claude"):
        self.tmux_bin = tmux_bin
        self.sock = sock
        self.target = target
        self.on_output = on_output          # async callable(str)
        self.cols, self.rows = cols, rows
        self.pid = None
        self.fd = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._loop = asyncio.get_running_loop()

    def start(self):
        pid, fd = pty.fork()
        if pid == 0:  # child
            env = {
                "HOME": os.environ.get("HOME", "/"),
                "USER": os.environ.get("USER", ""),
                "PATH": "/usr/bin:/bin",
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
                "LANG": "en_US.UTF-8",
            }
            try:
                os.execve(self.tmux_bin, [self.tmux_bin, "-u", "-S", self.sock, "attach", "-t", self.target], env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        self.resize(self.cols, self.rows)
        os.set_blocking(fd, False)
        self._loop.add_reader(fd, self._readable)

    def _readable(self):
        try:
            data = os.read(self.fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self.close()
            asyncio.ensure_future(self.on_output(None))
            return
        text = self._decoder.decode(data)
        if text:
            asyncio.ensure_future(self.on_output(text))

    def write(self, text):
        if self.fd is not None:
            try:
                os.write(self.fd, text.encode("utf-8", "replace"))
            except OSError:
                pass

    def resize(self, cols, rows):
        cols = max(20, min(int(cols), 500))
        rows = max(5, min(int(rows), 200))
        self.cols, self.rows = cols, rows
        if self.fd is not None:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def close(self):
        if self.fd is not None:
            try:
                self._loop.remove_reader(self.fd)
            except Exception:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                pass
            self.pid = None
