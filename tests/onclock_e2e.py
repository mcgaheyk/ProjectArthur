"""The whole program on the clock, as installed, against fakes on 127.0.0.1 (tests only).

:class:`Install` lays out an install root exactly as ``scripts/clock.py install`` does (the
launcher filled in by ``render_autostart``, the real ``boot.py``, slot a written by
``scripts/clock.py bundle --out``, ``config.json`` from ``config_bytes``, ``secrets.json``
with a made-up login and door key, ``state.json``), with ``ROOT/micropython`` a small
script that runs the ``micropython`` binary. :class:`Fakes` runs the fake Whisker
(``tests/wirefake.py``, over TLS with the throwaway CA), the repository's fake clock
(``arthur.sim.server``, which rejects unknown keys like the real one; restartable on its
port) and a fake Open-Meteo, and records every frame the clock receives.

The launcher is started with ``sh``; ``ARTHUR_ONCLOCK_TEST`` points the program at the fakes
(the one way the program differs from an install on the clock). Time is real: nothing in the
running program can be sped up from outside, so the robot's own timeline is compressed
instead (the robot's timestamps are its own; the program dates what it sees when it sees it).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

from aiohttp import web
from wirefake import FakeWhisker, activity_entry, raw_from_record
from wirehelp import real_lines

from arthur.clocktool import config_bytes, render_autostart
from arthur.sim.server import CLOCKS, create_app

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy" / "clock"
SAMPLE = json.loads((Path(__file__).parent / "data" / "open-meteo-forecast.json").read_text())
PASSWORD = "e2e-Pa55word-not-real"
USERNAME = "arthur-e2e@example.com"
DOOR_KEY = bytes(range(100, 132))  # made up, tests only


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_ssl(tls_files) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tls_files["cert"], tls_files["key"])
    return ctx


class Fakes:
    """Whisker, the clock and Open-Meteo, on 127.0.0.1, in real time."""

    def __init__(self, tls_files) -> None:
        self.tls_files = tls_files
        self.whisker = FakeWhisker(username=USERNAME, password=PASSWORD, ka_s=2.0,
                                   token_ttl=3600)
        self.issued: list[dict] = []
        real_issue = self.whisker.issue_tokens

        def issue(*a, **kw):
            t = real_issue(*a, **kw)
            self.issued.append(dict(t))
            return t

        self.whisker.issue_tokens = issue
        self.clock_port = free_port()
        self.clock_runner = None
        self.clocks: list = []
        self.received: list[dict] = []  # every frame the fake clock took: t, sha, body
        self.requests: list[tuple[float, str, int]] = []
        self.weather_runner = None
        self.weather_hits: list[float] = []
        self.weather_url = ""

    @web.middleware
    async def middleware(self, request, handler):
        resp = await handler(request)
        self.requests.append((time.time(), f"{request.method} {request.path}", resp.status))
        if request.method in ("PUT", "POST") and request.path.startswith(
                "/api/v1/apps/pushed/") and resp.status == 200:
            body = json.loads(await request.read())
            rgb = base64.b64decode(body["draw"][0][5])
            colors = sorted({rgb[i:i + 3].hex() for i in range(0, len(rgb), 3)})
            self.received.append({"t": time.time(), "sha": hashlib.sha256(rgb).hexdigest(),
                                  "colors": colors,
                                  "lifetime": body.get("lifetimeMs"),
                                  "expiry": body.get("lifetimeExpiry"),
                                  "ua": request.headers.get("User-Agent", "")})
        return resp

    async def start(self) -> None:
        await self.whisker.start(ssl_context=server_ssl(self.tls_files))
        await self.start_clock()

        async def forecast(request):
            self.weather_hits.append(time.time())
            return web.json_response(SAMPLE)

        app = web.Application()
        app.router.add_get("/v1/forecast", forecast)
        self.weather_runner = web.AppRunner(app)
        await self.weather_runner.setup()
        port = free_port()
        await web.TCPSite(self.weather_runner, "127.0.0.1", port,
                          ssl_context=server_ssl(self.tls_files)).start()
        self.weather_url = f"https://127.0.0.1:{port}/v1/forecast"

    async def start_clock(self) -> None:
        app = create_app(start_tasks=False)
        app.middlewares.append(self.middleware)
        self.clock_runner = web.AppRunner(app)
        await self.clock_runner.setup()
        await web.TCPSite(self.clock_runner, "127.0.0.1", self.clock_port).start()
        self.clocks.append(app[CLOCKS]["52x16"])

    async def stop_clock(self) -> None:
        if self.clock_runner is not None:
            await self.clock_runner.cleanup()
            self.clock_runner = None

    async def close(self) -> None:
        await self.stop_clock()
        if self.weather_runner is not None:
            await self.weather_runner.cleanup()
        await self.whisker.stop()

    # -- the robot -------------------------------------------------------------------------

    async def push_record(self, record: dict) -> None:
        raw = raw_from_record(record, base=self.whisker.robots[0])
        await self.whisker.push(raw)

    def add_history(self, when: float, value: str, action=None) -> None:
        self.whisker.activities.append(activity_entry(when, value, action))


class Install:
    """An install root and RAM folder as ``scripts/clock.py install`` lays them out."""

    def __init__(self, base: Path, *, mpy: str, heap: str = "3M", compiled: bool = True,
                 settings: dict | None = None) -> None:
        self.base = base
        self.root = base / "arthur"
        self.tmp = base / "ram"
        self.mpy = mpy
        self.heap = heap
        self.compiled = compiled
        self.settings = settings or {}
        self.door_port = free_port()
        self.key_file = base / "door-key"
        self.proc: asyncio.subprocess.Process | None = None
        #: Waits are this many times longer (the emulated clock is slow).
        self.slow = 1.0
        self.poll_s = 0.1

    def lay_out(self, config_toml: Path) -> str:
        """Everything ``install`` writes. Returns slot a's bundle id."""
        self.root.mkdir(parents=True)
        launcher = self.mpy_wrapper()
        (self.root / "micropython").write_text(launcher)
        os.chmod(self.root / "micropython", 0o755)
        shutil.copyfile(DEPLOY / "boot.py", self.root / "boot.py")
        (self.root / "config.json").write_bytes(config_bytes(config_toml))
        (self.root / "state.json").write_text(json.dumps(
            {"v": 1, "active": "a", "tries": {"a": 0, "b": 0}, "errors": {}}))
        secrets = {"whisker_username": USERNAME, "whisker_password": PASSWORD,
                   "door_key": DOOR_KEY.hex()}
        fd = os.open(self.root / "secrets.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(secrets, f)
        self.key_file.write_text(DOOR_KEY.hex() + "\n")
        os.chmod(self.key_file, 0o600)
        (self.root / "slots").mkdir()
        bundle = self.bundle(self.root / "slots" / "a")
        (self.root / "slots" / "b").mkdir()
        (self.base / "autostart").write_text(render_autostart(
            root=str(self.root), tmp=str(self.tmp), heap=self.heap))
        os.chmod(self.base / "autostart", 0o700)
        return bundle

    def mpy_wrapper(self) -> str:
        return f"#!/bin/sh\nexec {self.mpy} \"$@\"\n"

    def bundle(self, out: Path) -> str:
        """``scripts/clock.py bundle --out`` (compiled unless ``compiled`` is False)."""
        args = [sys.executable, str(REPO / "scripts" / "clock.py"), "bundle", "--out", str(out)]
        if not self.compiled:
            args.append("--source")
        run = subprocess.run(args, capture_output=True, text=True, check=True, cwd=REPO)
        return run.stdout.strip().splitlines()[-1].split()[1].rstrip(":")

    def env(self, fakes: Fakes, extra: dict | None = None) -> dict:
        s = {"awtrix": f"http://127.0.0.1:{fakes.clock_port}",
             "ca_file": str(fakes.tls_files["ca"]), "server_name": "localhost",
             "weather_url": fakes.weather_url, "lr4_url": fakes.whisker.url + "/graphql",
             "cognito_url": fakes.whisker.url + "/", "door_port": self.door_port,
             "door_host": "127.0.0.1", "healthy_after": 5, "probe_every": 1,
             "crash_pause": 1, "client": {"stale_s": 6.0, "ws_backoff": [0.5, 2.0]}}
        s.update(self.settings)
        s.update(extra or {})
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.base),
               "ARTHUR_ONCLOCK_TEST": json.dumps(s)}
        return env

    async def start(self, fakes: Fakes) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            "sh", str(self.base / "autostart"), env=self.env(fakes),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)

    async def stop(self) -> int | None:
        if self.proc is None:
            return None
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        return self.proc.returncode

    # -- reading what the clock keeps -----------------------------------------------------

    def read_root(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def launcher_alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def launcher_pid(self) -> int | None:
        return self.proc.pid if self.launcher_alive() else None

    def collect(self) -> None:
        """After the run: ``root`` and ``tmp`` are local folders to search (here already)."""

    def console(self) -> str:
        try:
            return (self.tmp / "console.log").read_text(errors="replace")
        except OSError:
            return ""

    def boot_notes(self) -> str:
        try:
            return (self.tmp / "boot.txt").read_text(errors="replace")
        except OSError:
            return ""

    def state(self) -> dict:
        return json.loads((self.root / "state.json").read_text())

    async def wait_console(self, text: str, timeout: float, *, after: int = 0) -> float:
        """Wait until ``text`` is in the console after offset ``after``; when it showed."""
        timeout *= self.slow
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if text in self.console()[after:]:
                return time.time()
            if self.proc is not None and self.proc.returncode is not None:
                raise AssertionError(f"the launcher exited ({self.proc.returncode}) waiting "
                                     f"for {text!r}:\n" + self.console()[-3000:])
            await asyncio.sleep(self.poll_s)
        raise AssertionError(f"no {text!r} in the console within {timeout} s:\n"
                             + self.console()[-4000:])

    # -- the Mac's tool ---------------------------------------------------------------------

    async def clock_py(self, *args: str, timeout: float = 180) -> tuple[int, str, float]:
        """``scripts/clock.py --clock 127.0.0.1:PORT ...``: (exit code, output, seconds)."""
        cmd = [sys.executable, str(REPO / "scripts" / "clock.py"), "--clock",
               f"127.0.0.1:{self.door_port}", "--key-file", str(self.key_file), *args]
        t0 = time.monotonic()
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT, cwd=REPO)
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode, out.decode(errors="replace"), time.monotonic() - t0


def visit_records(first: int, last: int, *, drop_weight: bool = False) -> list[tuple[float, dict]]:
    """``[(seconds after the first line, record), ...]`` for real-cycles lines
    ``first``..``last`` (heartbeats left out). ``drop_weight``: the weight never changes
    (the visit's records keep the weight from before it)."""
    from datetime import datetime

    lines = real_lines()
    t0 = datetime.fromisoformat(lines[first]["ts"])
    keep = lines[first - 1]["record"]["pet_weight"]
    out = []
    for ln in lines[first:last + 1]:
        if ln["source"] == "heartbeat":
            continue
        rec = dict(ln["record"])
        if drop_weight:
            rec["pet_weight"] = keep
        t = (datetime.fromisoformat(ln["ts"]) - t0).total_seconds()
        out.append((t, rec))
    return out


EMU = "arthur-clock-emu"
EMU_ROOT = "/data/awtrix-ng/state/arthur"
EMU_RAM = "/tmp/arthur"
RELAY_PORT = 18080


def _sh(cmd: str) -> str:
    p = subprocess.run(["docker", "exec", EMU, "sh", "-c", cmd], capture_output=True, text=True,
                       check=False)
    return p.stdout


class EmuInstall(Install):
    """The same, on the emulated ARMv7 "clock" (``scripts/clock.py emulator up``), installed
    by ``scripts/clock.py --emulate install`` with the clock's static binary. The fakes stay
    on the Mac's 127.0.0.1; the container reaches them as ``host.docker.internal`` (the
    certificates' name is given as ``localhost``). The door is the emulator's published
    port. Functional only: emulated timings mean nothing."""

    def __init__(self, base: Path, **kw) -> None:
        super().__init__(base, mpy="(the clock's binary)", **kw)
        self.door_port = 17531
        self.slow = 8.0
        self.poll_s = 0.5
        self.config_toml: Path | None = None
        self.install_out = ""
        self.remove_out = ""
        self.snapped = False  # after the run: read the copies, the clock is wiped

    def lay_out(self, config_toml: Path) -> str:
        self.config_toml = config_toml
        subprocess.run([sys.executable, str(REPO / "scripts" / "clock.py"), "emulator", "up"],
                       check=True, cwd=REPO, capture_output=True)
        return ""

    def env_for_clock(self, fakes: Fakes) -> dict:
        def there(url: str) -> str:
            return url.replace("127.0.0.1", "host.docker.internal")

        s = {"awtrix": f"http://127.0.0.1:{RELAY_PORT}",  # tests/tcprelay.py, to the Mac
             "server_name": "localhost", "weather_url": there(fakes.weather_url),
             "lr4_url": there(fakes.whisker.url) + "/graphql",
             "cognito_url": there(fakes.whisker.url) + "/", "healthy_after": 20,
             "probe_every": 2, "crash_pause": 1,
             "client": {"stale_s": 20.0, "ws_backoff": [0.5, 2.0], "timeout": 60.0}}
        s.update(self.settings)
        return s

    def prepare(self, fakes: Fakes) -> None:
        """The throwaway CA where the clock keeps its bundle, the test settings, and a relay
        from the container's 127.0.0.1 to the fake clock (the transport talks to this device
        only)."""
        from arthur.clocktool import BINARY

        subprocess.run(["docker", "exec", EMU, "mkdir", "-p", "/tmp/relay"], check=True)
        subprocess.run(["docker", "cp", str(BINARY), EMU + ":/tmp/relay/micropython"],
                       check=True)
        subprocess.run(["docker", "cp", str(Path(__file__).with_name("tcprelay.py")),
                        EMU + ":/tmp/relay/tcprelay.py"], check=True)
        subprocess.run(["docker", "exec", "-d", EMU, "/tmp/relay/micropython",
                        "/tmp/relay/tcprelay.py", str(RELAY_PORT), "host.docker.internal",
                        str(fakes.clock_port)], check=True)
        subprocess.run(["docker", "exec", EMU, "mkdir", "-p", "/tmp/awtrix-release/share"],
                       check=True)
        subprocess.run(["docker", "cp", str(fakes.tls_files["ca"]),
                        EMU + ":/tmp/awtrix-release/share/ca-certificates.crt"], check=True)
        test = self.base / "arthur-test.json"
        test.write_text(json.dumps(self.env_for_clock(fakes)))
        subprocess.run(["docker", "cp", str(test), EMU + ":/tmp/awtrix-release/arthur-test.json"],
                       check=True)

    async def start(self, fakes: Fakes) -> None:
        self.prepare(fakes)
        env = dict(os.environ, WHISKER_USERNAME=USERNAME, WHISKER_PASSWORD=PASSWORD)
        p = await asyncio.create_subprocess_exec(
            sys.executable, str(REPO / "scripts" / "clock.py"), "--emulate", "--key-file",
            str(self.key_file), "install", "--config", str(self.config_toml), env=env,
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await p.communicate()
        self.install_out = out.decode(errors="replace")
        if p.returncode != 0:
            raise AssertionError("install failed:\n" + self.install_out)
        import re

        m = re.search(r"bundle ([0-9a-f]{16})", self.install_out)
        self.bundle_id = m.group(1) if m else ""

    async def stop(self) -> int | None:
        self.snapshot()
        p = await asyncio.create_subprocess_exec(
            sys.executable, str(REPO / "scripts" / "clock.py"), "--emulate", "remove",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await p.communicate()
        self.remove_out = out.decode(errors="replace")
        return p.returncode

    def console(self) -> str:
        if self.snapped:
            return Install.console(self)
        return _sh(f"cat {EMU_RAM}/console.log 2>/dev/null")

    def boot_notes(self) -> str:
        if self.snapped:
            return Install.boot_notes(self)
        return _sh(f"cat {EMU_RAM}/boot.txt 2>/dev/null")

    def read_root(self, rel: str) -> str:
        return _sh(f"cat {EMU_ROOT}/{rel}")

    def state(self) -> dict:
        return json.loads(self.read_root("state.json"))

    def launcher_pid(self) -> int | None:
        text = _sh("cat /tmp/awtrix-release/autostart.pid 2>/dev/null").strip()
        if not text or not _sh(f"kill -0 {text} 2>/dev/null && echo yes").strip():
            return None
        return int(text)

    def launcher_alive(self) -> bool:
        return self.launcher_pid() is not None

    def snapshot(self) -> None:
        """Copy the clock's root and RAM folder out, for the searches after the run, without
        the two files that hold the secrets and without the binary."""
        for src, dest in ((EMU_ROOT, self.root), (EMU_RAM, self.tmp)):
            if dest.exists():
                shutil.rmtree(dest)
            subprocess.run(["docker", "cp", EMU + ":" + src, str(dest)], check=False,
                           capture_output=True)
        for name in ("secrets.json", "tokens.json", "micropython"):
            p = self.root / name
            if p.exists():
                p.unlink()
        self.snapped = True
