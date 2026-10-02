"""claude-remote web server (runs inside the SLURM job on the compute node).

Reachable only through the OnDemand portal:
    https://portal-grace.hprc.tamu.edu/node/<node>/<port>/<secret>/
Access layers: portal login (NetID + Duo) → source IP must be the portal proxy →
secret path segment → X-Forwarded-User (set by the portal) must be this user.
"""
import argparse
import asyncio
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time

from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from terminal import TerminalClient  # noqa: E402
from tmuxctl import Tmux  # noqa: E402
from live import Live  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(HERE, "static")
DEFAULT_ALLOWED_IPS = {"10.73.4.63", "127.0.0.1"}
USER = os.environ.get("USER", "")
MAX_SHELLS = 6
MAX_UPLOAD = 512 * 1024 * 1024


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_private(path, text):
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


class Server:
    def __init__(self, a):
        self.a = a
        self.state_file = os.path.join(a.dir, "server_state.json")
        prev = {}
        try:
            prev = json.load(open(self.state_file))
        except Exception:
            pass
        # a restarted server keeps the same secret (and port, if free) so the link stays valid
        self.secret = prev.get("secret") or secrets.token_urlsafe(16)
        self.prev_port = prev.get("port")
        self.allowed_ips = set(DEFAULT_ALLOWED_IPS)
        extra = os.environ.get("CR_ALLOWED_IPS", "")
        self.allowed_ips |= {x for x in extra.replace(",", " ").split() if x}
        self.port = None
        self.base = None
        self.clients = set()          # open WebSockets
        self.live = None

    # ── access control ────────────────────────────────────────────────────
    @web.middleware
    async def guard(self, request, handler):
        if request.remote not in self.allowed_ips:
            return web.Response(status=403, text="forbidden")
        if not request.path.startswith(self.base):
            return web.Response(status=404, text="not found")
        user = request.headers.get("X-Forwarded-User", "")
        if not user or not secrets.compare_digest(user, USER):
            log(f"denied request for portal user {user!r}")
            return web.Response(status=403, text="forbidden")
        if request.method not in ("GET", "HEAD") and request.headers.get("Origin", "") != self.a.portal:
            return web.Response(status=403, text="bad origin")
        resp = await handler(request)
        if not isinstance(resp, web.WebSocketResponse):
            self._security_headers(request, resp)
        return resp

    def _security_headers(self, request, resp):
        origin = self.a.portal
        host = origin.split("://", 1)[1]
        static = f"{origin}{self.base}static/"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            f"script-src {static}; "
            f"style-src {static} 'unsafe-inline'; "
            f"img-src {origin}{self.base} data: blob:; "
            f"font-src {static} data:; "
            f"connect-src wss://{host}{self.base}ws {origin}{self.base}; "
            "base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        if "/static/" in request.path:
            # per-job URLs, so caching scripts/fonts for the job's lifetime is safe and saves mobile data
            resp.headers["Cache-Control"] = "private, max-age=86400"
        elif "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"

    # ── tmux sessions: "claude" plus shell tabs "sh-N" ───────────────────────
    def tmux(self, *args):
        return subprocess.run([self.a.tmux, "-u", "-S", self.a.sock, *args],
                              capture_output=True, text=True, env={"LANG": "en_US.UTF-8", "PATH": "/usr/bin:/bin"})

    def tmux_alive(self):
        return self.tmux("has-session", "-t", "claude").returncode == 0

    def terms(self):
        r = self.tmux("list-sessions", "-F", "#{session_name}")
        names = [n for n in r.stdout.split() if n == "claude" or re.fullmatch(r"sh-\d+", n)]
        shells = sorted((n for n in names if n != "claude"), key=lambda n: int(n[3:]))
        out = [{"name": "claude", "label": "Claude"}] if "claude" in names else []
        out += [{"name": n, "label": f"Shell {n[3:]}"} for n in shells]
        return out

    def new_shell(self):
        existing = {t["name"] for t in self.terms()}
        if sum(1 for n in existing if n.startswith("sh-")) >= MAX_SHELLS:
            return None
        n = 1
        while f"sh-{n}" in existing:
            n += 1
        name = f"sh-{n}"
        cmd = f"/bin/bash '{HERE}/run_shell.sh' '{self.a.dir}' '{self.a.run}' '{self.a.node}'"
        r = self.tmux("new-session", "-d", "-s", name, "-x", "200", "-y", "50", cmd)
        return name if r.returncode == 0 else None

    # ── status ────────────────────────────────────────────────────────────
    def status(self):
        return {
            "type": "status",
            "node": self.a.node,
            "jobid": self.a.jobid,
            "cwd": self.a.cwd,
            "end": int(self.a.end),
            "now": int(time.time()),
            "claude_running": self.tmux_alive(),
            "ending_soon": os.path.exists(os.path.join(self.a.dir, "ending_soon")),
            "live": self.live.status if self.live else None,
            "terms": self.terms(),
        }

    async def broadcast(self, msg):
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except Exception:
                self.clients.discard(ws)

    async def status_loop(self):
        while True:
            await asyncio.sleep(20)
            await self.broadcast(self.status())   # doubles as the WebSocket heartbeat

    # ── handlers ──────────────────────────────────────────────────────────
    async def index(self, request):
        if request.path == self.base.rstrip("/"):
            raise web.HTTPFound(self.base)
        resp = web.FileResponse(os.path.join(STATIC, "index.html"))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def static(self, request):
        name = request.match_info["name"]
        path = os.path.realpath(os.path.join(STATIC, name))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def upload(self, request):
        """Phone/laptop → Claude: save into the job's uploads folder (readable by Claude via --add-dir)."""
        raw = os.path.basename(request.query.get("name", "upload"))
        name = re.sub(r"[^A-Za-z0-9._+-]+", "_", raw).strip("._") or "upload"
        updir = os.path.join(self.a.dir, "uploads")
        os.makedirs(updir, mode=0o700, exist_ok=True)
        path = os.path.join(updir, time.strftime("%H%M%S-") + secrets.token_hex(2) + "-" + name[-120:])
        size = 0
        with open(path, "wb") as f:
            async for chunk in request.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    f.close()
                    os.unlink(path)
                    return web.json_response({"ok": False, "error": "file too large (max 512 MB)"}, status=413)
                f.write(chunk)
        os.chmod(path, 0o600)
        log(f"upload saved ({size} bytes)")
        return web.json_response({"ok": True, "path": path, "size": size, "name": raw})

    async def ws(self, request):
        if request.headers.get("Origin", "") != self.a.portal:
            return web.Response(status=403, text="bad origin")
        ws = web.WebSocketResponse(heartbeat=None, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        self.clients.add(ws)
        term = None

        async def send(obj):
            try:
                await ws.send_str(json.dumps(obj))
            except Exception:
                pass

        def make_out(name):
            async def term_out(text):
                if text is None:
                    await send({"type": "term_closed", "name": name})
                    await self.broadcast({"type": "terms", "terms": self.terms()})
                else:
                    await send({"type": "term", "name": name, "data": text})
            return term_out

        try:
            await send(self.status())
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                t = m.get("type")
                if t == "term_open":
                    if term:
                        term.close()
                        term = None
                    name = str(m.get("name") or "claude")
                    if name not in {x["name"] for x in self.terms()}:
                        await send({"type": "term_closed", "name": name})
                        await send({"type": "terms", "terms": self.terms()})
                        continue
                    term = TerminalClient(self.a.tmux, self.a.sock, make_out(name),
                                          m.get("cols", 100), m.get("rows", 30), target=name)
                    term.start()
                elif t == "term_in" and term:
                    term.write(m.get("data", ""))
                elif t == "term_resize" and term:
                    term.resize(m.get("cols", 100), m.get("rows", 30))
                elif t == "term_close" and term:
                    term.close()
                    term = None
                elif t == "term_new":
                    name = self.new_shell()
                    await self.broadcast({"type": "terms", "terms": self.terms()})
                    await send({"type": "term_created", "name": name} if name else
                               {"type": "notice", "text": f"At most {MAX_SHELLS} shells per job."})
                elif t == "term_kill":
                    name = str(m.get("name", ""))
                    if re.fullmatch(r"sh-\d+", name):
                        self.tmux("kill-session", "-t", name)
                        await self.broadcast({"type": "terms", "terms": self.terms()})
                elif t == "terms":
                    await send({"type": "terms", "terms": self.terms()})
                elif t == "end_job":
                    log("end_job requested from the web page")
                    await self.broadcast({"type": "notice", "text": "Ending the job…"})
                    subprocess.Popen(["scancel", str(self.a.jobid)])
                elif t == "chat_open":
                    await send(self.live.snapshot())
                elif t == "send":
                    await self.live.web_send(str(m.get("text", ""))[:100000], str(m.get("cid", "")))
                elif t == "key":
                    await self.live.web_key(m.get("key"))
                elif t == "perm_answer":
                    await self.live.perm_answer(m)
                elif t == "menu_pick":
                    await self.live.menu_pick(str(m.get("fp", "")), int(m.get("index", -1)))
                elif t == "sessions":
                    await send({"type": "sessions", "list": self.live.list_sessions()})
                elif t == "history":
                    await send(self.live.history())
                elif t == "ping":
                    await send({"type": "pong"})
        finally:
            self.clients.discard(ws)
            if term:
                term.close()
        return ws

    async def file(self, request):
        r = self.live.share_file(request.match_info["sid"])
        if not r:
            raise web.HTTPNotFound()
        rec, mime = r
        path = rec["served"]
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        resp = web.FileResponse(path)
        kind = rec["kind"]
        inline = request.query.get("dl") != "1"
        if kind == "image" and inline:
            resp.content_type = mime
        elif kind == "svg" and inline:
            resp.content_type = "image/svg+xml"
            resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        elif kind == "text" and inline:
            resp.content_type = "text/plain"
            resp.charset = "utf-8"
        else:
            resp.content_type = "application/octet-stream"
            resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % rec["name"].replace('"', "")
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    def mail(self, subject, body):
        if not self.a.email:
            return
        try:
            subprocess.run(["mail", "-s", subject, self.a.email], input=body.encode(), timeout=30)
        except Exception as e:
            log(f"mail failed: {e!r}")

    async def start_hook_socket(self):
        """Unix socket for the plugin's hooks and cr-send (0600, in the job's 0700 run dir)."""
        hook_app = web.Application(client_max_size=16 * 1024 * 1024)

        async def hook(request):
            try:
                d = await request.json()
            except Exception:
                d = {}
            await self.live.hook(request.query.get("ev", ""), d, request.query.get("effort", ""))
            return web.Response(text="")

        async def permission(request):
            try:
                d = await request.json()
            except Exception:
                return web.Response(text="")
            return web.Response(text=await self.live.permission(d))

        async def share(request):
            r = await self.live.share(await request.json())
            return web.Response(text=r["text"])

        hook_app.router.add_post("/hook", hook)
        hook_app.router.add_post("/permission", permission)
        hook_app.router.add_post("/share", share)
        runner = web.AppRunner(hook_app, access_log=None)
        await runner.setup()
        path = os.path.join(self.a.run, "hook.sock")
        if os.path.exists(path):
            os.unlink(path)
        await web.UnixSite(runner, path).start()
        os.chmod(path, 0o600)

    # ── startup ───────────────────────────────────────────────────────────
    def bind(self):
        for port in ([self.prev_port] if self.prev_port else []) + [0]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return sock
            except OSError:
                sock.close()
        raise RuntimeError("could not bind a port")

    async def run(self):
        sock = self.bind()
        self.port = sock.getsockname()[1]
        self.base = f"/node/{self.a.node}/{self.port}/{self.secret}/"
        write_private(self.state_file, json.dumps({"secret": self.secret, "port": self.port}))

        app = web.Application(middlewares=[self.guard], client_max_size=16 * 1024 * 1024)
        app.router.add_get(self.base.rstrip("/"), self.index)
        app.router.add_get(self.base, self.index)
        app.router.add_get(self.base + "static/{name:.+}", self.static)
        app.router.add_get(self.base + "ws", self.ws)
        app.router.add_get(self.base + "f/{sid}/{name}", self.file)
        app.router.add_post(self.base + "upload", self.upload)

        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)   # never log request headers
        await runner.setup()
        await web.SockSite(runner, sock).start()

        url = f"{self.a.portal}{self.base}"
        self.a.url = url
        self.live = Live(self.a, Tmux(self.a.tmux, self.a.sock), self.broadcast,
                         lambda: bool(self.clients), self.mail)
        await self.start_hook_socket()
        asyncio.create_task(self.live.loop())
        write_private(os.path.join(self.a.dir, "conn.json"), json.dumps({
            "url": url, "node": self.a.node, "port": self.port, "jobid": self.a.jobid,
            "tmux_sock": self.a.sock, "end": int(self.a.end), "cwd": self.a.cwd,
            "started": int(time.time()),
        }))
        log(f"listening on port {self.port}")
        asyncio.create_task(self.status_loop())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        def on_signal():
            stop.set()
            loop.call_later(3, os._exit, 0)      # nothing to save: never let a slow shutdown block the restart
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, on_signal)
        await stop.wait()
        for ws in list(self.clients):           # tell pages we're restarting; they reconnect by themselves
            try:
                await ws.close(code=1012, message=b"server restarting")
            except Exception:
                pass
        await runner.cleanup()


def main():
    p = argparse.ArgumentParser()
    for k in ("dir", "run", "node", "jobid", "end", "tmux", "sock", "cwd", "portal"):
        p.add_argument("--" + k, required=True)
    p.add_argument("--email", default="")
    a = p.parse_args()
    asyncio.run(Server(a).run())
    os._exit(0)


if __name__ == "__main__":
    main()
