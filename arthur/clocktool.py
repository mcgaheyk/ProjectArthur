"""The Mac's tool for the program on the clock (D-0029): ``scripts/clock.py``. CPython only.

Building::

    clock.py build                       the clock's MicroPython (Docker, emulated ARMv7)
    clock.py bundle [--out DIR]          what would go in a code slot, with its manifest

Over the USB cable, with ``adb -s SERIAL`` (``--serial`` is always required; ``--dry-run``
prints exactly what would be written where and runs nothing)::

    clock.py --serial S install          binary, boot, launcher, slot a, config, secrets; start
    clock.py --serial S status
    clock.py --serial S logs             the boot notes and the program's console
    clock.py --serial S remove           everything of ours, checked gone

Over Wi-Fi, through the door (``--clock HOST``; the key is in ``.clock-door-key``)::

    clock.py --clock HOST push           config only if only the config changed, else a bundle
    clock.py --clock HOST status
    clock.py --clock HOST logs pull      append to logs/spike-*.jsonl and logs/feeder-console.log
    clock.py --clock HOST logs follow    the same, every 2 s, until Ctrl-C
    clock.py --clock HOST pause|resume|restart

``--emulate`` runs the USB commands against an emulated ARMv7 "clock" in Docker instead
(``clock.py emulator up`` first); its door is then at ``--clock 127.0.0.1:17531``.

Secrets: ``install`` reads WHISKER_USERNAME and WHISKER_PASSWORD from the environment (run it
with ``uv run --env-file .env``) and makes a new door key. They go to the clock in
``secrets.json`` (mode 600, staged in /tmp and removed from there at once); the door key also
stays on the Mac in ``.clock-door-key`` (mode 600, gitignored). None of the three is ever
printed, logged or echoed, in any mode.

On the clock the tool touches only ``/data/awtrix-ng/state/arthur/``, the autostart file
``/data/awtrix-ng/state/autostart`` (and only when it is ours) and ``/tmp/arthur*``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from arthur.onclock import door as doorproto
from arthur.tools import onclock_files as files

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy" / "clock"
BINARY = DEPLOY / "micropython" / "dist" / "micropython-armv7"
KEY_FILE = REPO / ".clock-door-key"

ROOT = "/data/awtrix-ng/state/arthur"
AUTOSTART = "/data/awtrix-ng/state/autostart"
RAM = "/tmp/arthur"
TOOL = "/tmp/arthur-tool"
STAGE = "/tmp/arthur-install"
SECRETS_TMP = "/tmp/arthur-secrets.json"
CTL = "/tmp/awtrix-release/bin/awtrix-tc002d ctl restart-autostart"
#: Bytes that must stay free on /data after an install (AWTRIX refuses its own uploads
#: below 1 MiB; this leaves it 2).
FLASH_RESERVE = 2 * 1024 * 1024
DEFAULT_HEAP = "3M"
EMU = "arthur-clock-emu"
EMU_DOOR_PORT = 17531
SECRET_NAMES = ("WHISKER_USERNAME", "WHISKER_PASSWORD")


class ToolError(Exception):
    """A refusal or failure, said plainly. Never carries a secret."""


def say(text: str = "") -> None:
    print(text, flush=True)


# =========================================================================================
# The device: adb, the emulated container, or a dry run
# =========================================================================================


def q(s: str) -> str:
    return s if s and all(c.isalnum() or c in "-_./:=@,+" for c in s) else "'" + s + "'"


class Device:
    """Runs ``adb -s SERIAL`` commands (or, emulated, docker). A dry run prints them only."""

    def __init__(self, serial: str | None, emulate: bool, dry: bool) -> None:
        self.serial, self.emulate, self.dry = serial, emulate, dry

    def adb_form(self, kind: str, *a: str) -> list[str]:
        s = self.serial or "SERIAL"
        if kind in ("push", "pull"):
            return ["adb", "-s", s, kind, a[0], a[1]]
        return ["adb", "-s", s, "shell", a[0]]

    def real_form(self, kind: str, *a: str) -> list[str]:
        if not self.emulate:
            return self.adb_form(kind, *a)
        if kind == "push":
            return ["docker", "cp", a[0], EMU + ":" + a[1]]
        if kind == "pull":
            return ["docker", "cp", EMU + ":" + a[0], a[1]]
        return ["docker", "exec", EMU, "sh", "-c", a[0]]

    def run(self, kind: str, *a: str, show: bool = True) -> str:
        cmd = self.adb_form(kind, *a)
        if self.dry or show:
            say("  $ " + " ".join(q(x) for x in cmd) + ("   [emulated]" if self.emulate else ""))
        if self.dry:
            return ""
        p = subprocess.run(self.real_form(kind, *a), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, check=False)
        return p.stdout

    def push(self, local: Path | str, remote: str) -> str:
        return self.run("push", str(local), remote)

    def shell(self, cmd: str, show: bool = True) -> str:
        return self.run("shell", cmd, show=show)

    def ctl(self, args: str, py: str = TOOL + "/micropython") -> list[tuple[str, str]]:
        """Run the clock-side helper and return its ``R key=value`` lines."""
        out = self.shell(f"{py} -X heapsize=512K {TOOL}/clockctl.py {args}")
        pairs = results(out)
        if not self.dry and get(pairs, "done") != "1":
            raise ToolError("the clock-side helper failed: " + (get(pairs, "error")
                                                                or out.strip()[-300:]))
        return pairs


def results(out: str) -> list[tuple[str, str]]:
    pairs = []
    for line in out.splitlines():
        if line.startswith("R ") and "=" in line:
            k, v = line[2:].split("=", 1)
            pairs.append((k, v))
    return pairs


def get(pairs: list[tuple[str, str]], key: str, default: str | None = None) -> str | None:
    for k, v in reversed(pairs):
        if k == key:
            return v
    return default


# =========================================================================================
# What goes on the clock
# =========================================================================================


def render_autostart(root: str = ROOT, tmp: str = RAM, heap: str = DEFAULT_HEAP) -> str:
    """``deploy/clock/autostart.sh`` with its three settings filled in."""
    if not all(c.isalnum() or c in "/_-." for c in root + tmp) or not heap[:-1].isdigit() \
            or heap[-1] not in "KM":
        raise ToolError("bad root, tmp or heap for the launcher")
    out = []
    for line in (DEPLOY / "autostart.sh").read_text().splitlines():
        if line.startswith("ROOT="):
            line = "ROOT=" + root
        elif line.startswith("TMP="):
            line = "TMP=" + tmp
        elif line.startswith("HEAP="):
            line = "HEAP=" + heap
        out.append(line)
    return "\n".join(out) + "\n"


def config_bytes(path: Path | None = None) -> bytes:
    """The config as the clock reads it (``arthur.tools.export_config``), canonical, so the
    same TOML always gives the same bytes and the door's config id can be compared."""
    from arthur.tools.export_config import export

    data = export(path)
    data["_source"] = "config/display.toml"
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def config_id(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()[:16]


def check_cross() -> None:
    cross = files.mpy_cross()
    if cross is None:
        raise ToolError("mpy-cross is not on PATH (brew install micropython), or use --source")
    v = subprocess.run([cross, "--version"], capture_output=True, text=True, check=False).stdout
    if "mpy v6.3" not in v:
        raise ToolError("mpy-cross must emit mpy v6.3 (MicroPython 1.2x), got: " + v.strip())


def make_slot(source: bool, stub: Path | None) -> tuple[Path, bytes]:
    if not source:
        check_cross()
    extra = {"arthur/onclock/main.py": stub} if stub else None
    return files.temp_slot(compiled=not source, extra=extra)


def read_secrets(environ: Mapping[str, str]) -> dict[str, str] | list[str]:
    """The Whisker login from the environment, or the names that are missing."""
    missing = [n for n in SECRET_NAMES if not (environ.get(n) or "").strip()]
    if missing:
        return missing
    return {"whisker_username": environ["WHISKER_USERNAME"].strip(),
            "whisker_password": environ["WHISKER_PASSWORD"]}


def read_key(path: Path = KEY_FILE) -> bytes:
    try:
        text = path.read_text().strip()
        key = bytes.fromhex(text)
    except (OSError, ValueError):
        raise ToolError(f"no door key in {path.name} (it is made by `install`)") from None
    if len(key) != 32:
        raise ToolError(f"the door key in {path.name} is not 32 bytes")
    return key


def write_key(key: bytes, path: Path = KEY_FILE) -> None:
    fd = os.open(path.with_suffix(".new"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.hex() + "\n")
    os.chmod(path.with_suffix(".new"), 0o600)
    os.replace(path.with_suffix(".new"), path)


# =========================================================================================
# USB: install, status, logs, remove
# =========================================================================================


def stage_tool(dev: Device) -> None:
    """The clock-side helper and a MicroPython to run it, in /tmp/arthur-tool (RAM)."""
    if not dev.dry and not BINARY.exists():
        raise ToolError(f"no clock binary at {BINARY.relative_to(REPO)}: run `clock.py build`")
    base = Path(tempfile.mkdtemp(prefix="arthur-tool-"))
    try:
        t = base / "arthur-tool"
        t.mkdir()
        if BINARY.exists():
            shutil.copy2(BINARY, t / "micropython")
        shutil.copy2(DEPLOY / "clockctl.py", t / "clockctl.py")
        dev.shell(f"rm -rf {TOOL}")
        dev.push(t, "/tmp/")
        dev.shell(f"chmod 755 {TOOL}/micropython")
    finally:
        shutil.rmtree(base)


def _size(p: Path) -> int:
    return p.stat().st_size if p.exists() else 0


def cmd_install(a: argparse.Namespace, dev: Device) -> int:
    secrets_or_missing = read_secrets(os.environ)
    if isinstance(secrets_or_missing, list):
        raise ToolError("not set in the environment: " + ", ".join(secrets_or_missing)
                        + " (run with `uv run --env-file .env`)")
    if not dev.dry and not BINARY.exists():
        raise ToolError(f"no clock binary at {BINARY.relative_to(REPO)}: run `clock.py build`")
    slot, manifest = make_slot(a.source, a.program_stub)
    cfg = config_bytes(a.config)
    base = Path(tempfile.mkdtemp(prefix="arthur-install-"))
    try:
        st = base / "arthur-install"
        st.mkdir()
        if BINARY.exists():
            shutil.copy2(BINARY, st / "micropython")
        shutil.copy2(DEPLOY / "boot.py", st / "boot.py")
        (st / "autostart.sh").write_text(render_autostart(heap=a.heap))
        (st / "config.json").write_bytes(cfg)
        shutil.copytree(slot, st / "slot")
        expect = {}
        import hashlib

        for name in ("micropython", "boot.py", "config.json"):
            if (st / name).exists():
                expect[name] = hashlib.sha256((st / name).read_bytes()).hexdigest()
        (st / "expect.json").write_text(json.dumps(expect))
        n_slot, slot_bytes = files.slot_size(slot)
        sizes = [("micropython", _size(BINARY)), ("boot.py", _size(st / "boot.py")),
                 ("config.json", len(cfg)), ("state.json", 60),
                 ("secrets.json", 200)]
        total = sum(s for _, s in sizes) + slot_bytes + len((st / "autostart.sh").read_bytes())

        say("== install: checks first")
        stage_tool(dev)
        chk = dev.ctl(f"check {ROOT} {AUTOSTART}")
        state = get(chk, "autostart", "absent")
        if state not in ("absent", "ours"):
            raise ToolError(f"{AUTOSTART} exists and is {state}, not ours. Nothing written.")
        if get(chk, "root") == "present":
            raise ToolError(f"{ROOT} exists already: push over Wi-Fi, or remove first. "
                            "Nothing written.")
        if not dev.dry:
            if get(chk, "state_dir") != "present":
                raise ToolError("/data/awtrix-ng/state is missing: is this an AWTRIX NG clock?")
            if dev.shell(f"ls {CTL.split()[0]}", show=False).strip() != CTL.split()[0]:
                raise ToolError("the daemon's ctl program is not at " + CTL.split()[0])
            free = get(chk, "data_free_bytes")
            if free is None:
                raise ToolError("could not read the free space on /data. Nothing written.")
            if int(free) < total + FLASH_RESERVE:
                raise ToolError(f"/data has {int(free):,} bytes free; the install needs "
                                f"{total:,} and must leave {FLASH_RESERVE:,}. Nothing written.")

        say("\n== install: about to write on the clock's flash (/data)")
        for name, size in sizes:
            note = " (mode 600; the Whisker login and the door key, not shown)" \
                if name == "secrets.json" else ""
            say(f"  {ROOT}/{name}  (~{size:,} bytes){note}" if name in ("state.json",
                                                                        "secrets.json")
                else f"  {ROOT}/{name}  ({size:,} bytes)")
        say(f"  {ROOT}/slots/a/  ({n_slot} files, {slot_bytes:,} bytes, bundle "
            f"{files.bundle_id(manifest)}, {'source' if a.source else 'compiled .mpy'})")
        say(f"  {ROOT}/slots/b/  (empty)")
        say(f"  {AUTOSTART}  ({len((st / 'autostart.sh').read_bytes()):,} bytes, mode 700, "
            f"heap {a.heap})")
        say(f"  total about {total:,} bytes; staged first in {STAGE}/ and {SECRETS_TMP} (RAM)")
        say(f"  on the Mac: {a.key_file.name} (mode 600, the door key, not shown)")

        say("\n== install: staging in RAM and copying")
        dev.shell(f"rm -rf {STAGE}")
        dev.push(st, "/tmp/")
        dev.ctl(f"place {STAGE} {ROOT}")
        dev.shell(f"chmod 755 {ROOT}/micropython")
        key = secrets.token_bytes(32)
        values = dict(secrets_or_missing)
        values["door_key"] = key.hex()
        _push_secrets(dev, values)
        del values
        if not dev.dry:
            write_key(key, a.key_file)
        del key
        dev.shell(f"cp {STAGE}/autostart.sh {AUTOSTART} && chmod 700 {AUTOSTART}")
        dev.shell(f"rm -rf {STAGE}")
        dev.shell(CTL)

        say("\n== install: checking it came up")
        ok = dev.dry
        for _ in range(1 if dev.dry else 10):
            if not dev.dry:
                time.sleep(3)
            p = dev.ctl(f"status {ROOT} {AUTOSTART}")
            ok = dev.dry or (get(p, "autostart") == "ours" and get(p, "autostart.mode") == "0o700"
                             and int(get(p, "processes", "0") or 0) >= 1)
            if ok:
                break
        dev.shell(f"rm -rf {TOOL}")
        say("\ninstall: " + ("(dry run)" if dev.dry else "OK: installed and running" if ok
                             else "NOT CONFIRMED running"))
        say(f"UNDO: uv run python scripts/clock.py --serial {a.serial or 'SERIAL'} remove")
        return 0 if ok else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(slot.parent, ignore_errors=True)


def _push_secrets(dev: Device, values: dict[str, str]) -> None:
    """secrets.json to the clock: a mode-600 file in a private temp folder, pushed to /tmp,
    copied with mode 600 and removed from /tmp in the same command; the Mac's copy is gone
    before this returns. Nothing here prints a value."""
    if dev.dry:  # nothing is written, not even on the Mac
        dev.push("(a mode-600 temporary file: secrets.json, not shown)", SECRETS_TMP)
    else:
        _push_secrets_file(dev, values)
    dev.shell(f"umask 077; cp {SECRETS_TMP} {ROOT}/secrets.json && chmod 600 "
              f"{ROOT}/secrets.json; rm -f {SECRETS_TMP}")


def _push_secrets_file(dev: Device, values: dict[str, str]) -> None:
    d = Path(tempfile.mkdtemp(prefix="arthur-s-"))
    local = d / "arthur-secrets.json"
    try:
        fd = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f)
        dev.push(local, SECRETS_TMP)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def cmd_usb_status(a: argparse.Namespace, dev: Device) -> int:
    stage_tool(dev)
    p = dev.ctl(f"status {ROOT} {AUTOSTART}")
    dev.shell(f"rm -rf {TOOL}")
    if dev.dry:
        return 0
    for k, v in p:
        if k != "done":
            say(f"  {k}: {v}")
    return 0


def cmd_usb_logs(a: argparse.Namespace, dev: Device) -> int:
    for name in ("boot.txt", "console.prev.log", "console.log"):
        say(f"== {RAM}/{name}")
        out = dev.shell(f"cat {RAM}/{name}", show=dev.dry)
        if not dev.dry:
            say(out.rstrip() or "(empty)")
    return 0


def cmd_remove(a: argparse.Namespace, dev: Device) -> int:
    say(f"== remove: deletes {AUTOSTART} (only if ours), {ROOT}/ and {RAM}*")
    stage_tool(dev)
    dev.ctl(f"unhook {AUTOSTART}")
    dev.shell(CTL)
    if not dev.dry:
        time.sleep(2)
    for _ in range(1 if dev.dry else 3):
        pids = get(dev.ctl(f"procs {ROOT} {AUTOSTART}"), "pids", "") or ""
        if not pids.strip():
            break
        say("  our program is still running; stopping it")
        dev.shell("kill " + pids)
        time.sleep(1)
    dev.ctl(f"wipe {ROOT}")
    p = dev.ctl(f"status {ROOT} {AUTOSTART}")
    dev.shell(f"rm -rf {RAM} {STAGE} {SECRETS_TMP} {TOOL}")
    left = dev.shell(f"ls -d {ROOT} {RAM} {STAGE} {SECRETS_TMP} {TOOL}", show=dev.dry)
    gone = dev.dry or (get(p, "autostart") in ("absent", "foreign") and get(p, "root") == "absent"
                       and get(p, "processes") == "0"
                       and all("No such file" in ln for ln in left.strip().splitlines()))
    say("\nremove: " + ("(dry run)" if dev.dry else "OK: autostart, files and process all gone"
                          if gone else "NOT CLEAN"))
    if not gone and not dev.dry:
        say("  left: " + " ".join(f"{k}={v}" for k, v in p if k != "done"))
        say("  " + left.strip())
    return 0 if gone else 1


# =========================================================================================
# Wi-Fi, through the door
# =========================================================================================


def parse_clock(text: str) -> tuple[str, int]:
    text = text.removeprefix("http://").rstrip("/")
    host, _, port = text.partition(":")
    return host, int(port) if port else doorproto.DOOR_PORT


def connect(a: argparse.Namespace):
    from arthur.tools.doorclient import DoorClient

    if not a.clock:
        raise ToolError("--clock HOST is needed for this (the clock's address on the Wi-Fi)")
    host, port = parse_clock(a.clock)
    return DoorClient(host, port, read_key(a.key_file))


def cmd_push(a: argparse.Namespace) -> int:
    t0 = time.monotonic()
    slot, manifest = make_slot(a.source, a.program_stub)
    try:
        cfg = config_bytes(a.config)
        want_code, want_cfg = files.bundle_id(manifest), config_id(cfg)
        n, size = files.slot_size(slot)
        if a.dry_run:
            say(f"push (dry run): would compare with the clock at {a.clock}:")
            say(f"  code: bundle {want_code} ({n} files, {size:,} bytes); if the running slot "
                "differs, only the files the inactive slot lacks are sent, then the program "
                "restarts into it")
            say(f"  config: {want_cfg} ({len(cfg):,} bytes); if only it differs, it replaces "
                f"{ROOT}/config.json and the program reloads it")
            return 0
        with connect(a) as c:
            st = c.status()
            code_same = st["slots"].get(st["running"]) == want_code and not st["restart_pending"]
            cfg_same = st["config"] == want_cfg
            if code_same and cfg_same:
                say(f"push: nothing to do (code {want_code}, config {want_cfg} already there)")
                return 0
            if code_same:
                c.config_put(cfg)
                say(f"push: config {want_cfg} installed and reloaded "
                    f"({time.monotonic() - t0:.2f} s)")
                return 0
            res = c.bundle(slot, manifest, config=None if cfg_same else cfg, progress=say)
            say(f"push: bundle {res['bundle']} in slot {res['slot']}"
                + ("" if cfg_same else f" with config {want_cfg}")
                + f": {res['files_sent']} files, {res['bytes_sent']:,} bytes sent "
                f"({time.monotonic() - t0:.2f} s); the program is restarting")
        if a.no_wait:
            return 0
        _wait_for_code(a, want_code)
        say(f"push: the program is back, running bundle {want_code} "
            f"({time.monotonic() - t0:.2f} s from the start of the push)")
        return 0
    finally:
        shutil.rmtree(slot.parent, ignore_errors=True)


def _wait_for_code(a: argparse.Namespace, want: str, limit_s: float = 120.0) -> dict:
    """Wait until the program answers again, running the bundle ``want``."""
    from arthur.tools.doorclient import DoorRefused

    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        try:
            with connect(a) as c:
                st = c.status()
            if st["slots"].get(st["running"]) == want:
                return st
        except (OSError, DoorRefused):
            pass
        time.sleep(0.5)
    raise ToolError("the program did not come back with the new bundle within "
                    f"{limit_s:.0f} s; see `status` (it may have fallen back)")


def cmd_door_status(a: argparse.Namespace) -> int:
    with connect(a) as c:
        st = c.status()
    say(json.dumps(st, indent=2, sort_keys=True))
    return 0


def cmd_simple(verb: str) -> Callable[[argparse.Namespace], int]:
    def run(a: argparse.Namespace) -> int:
        with connect(a) as c:
            res = getattr(c, verb)()
        say(f"{verb}: ok" + (f" (paused: {res['paused']})" if "paused" in res else ""))
        return 0
    return run


# -- logs ------------------------------------------------------------------------------------

SPIKE_KINDS = ("state", "info", "error")
ACTIVITY_KINDS = ("activity", "pet_weight")


def _local_dt(ts: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(ts)).astimezone()
    except ValueError:
        return datetime.now().astimezone()


class LogSink:
    """Where the clock's log lines go on the Mac, in the formats the Mac's feeder writes:
    robot lines to ``spike-YYYYMMDD.jsonl``, history lines to ``spike-activity-*.jsonl``
    (``arthur.spikelog.DailyJsonl``), and console lines to ``feeder-console.log``."""

    def __init__(self, log_dir: Path) -> None:
        from arthur.spikelog import DailyJsonl

        self.log_dir = Path(log_dir)
        self._day = date.today()
        self.states = DailyJsonl(self.log_dir, "spike", today=lambda: self._day)
        self.activity = DailyJsonl(self.log_dir, "spike-activity", today=lambda: self._day)
        self.counts = {"spike": 0, "activity": 0, "console": 0}

    def write(self, line: Any) -> None:
        kind = line.get("kind") if isinstance(line, dict) else None
        if kind in SPIKE_KINDS or kind in ACTIVITY_KINDS:
            self._day = _local_dt(line.get("ts") or line.get("fetched")).date()
            if kind in SPIKE_KINDS:
                self.states.write(line)
                self.counts["spike"] += 1
            else:
                self.activity.write(line)
                self.counts["activity"] += 1
            return
        if kind == "console":
            when = _local_dt(line.get("ts"))
            text = (f"{when:%Y-%m-%d %H:%M:%S},{when.microsecond // 1000:03d} "
                    f"{str(line.get('level', 'INFO')).upper()} "
                    f"{line.get('name', 'arthur.clock')}: "
                    f"{line.get('msg', '')}")
        else:
            when = datetime.now().astimezone()
            text = (f"{when:%Y-%m-%d %H:%M:%S},{when.microsecond // 1000:03d} INFO "
                    f"arthur.clock: {json.dumps(line, ensure_ascii=False)}")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_dir / "feeder-console.log", "a", encoding="utf-8") as f:
            f.write(text + "\n")
        self.counts["console"] += 1

    def close(self) -> None:
        self.states.close()
        self.activity.close()


def pull_logs(client: Any, log_dir: Path) -> dict[str, int]:
    """Fetch every new line from the clock's memory log and append it on the Mac. Remembers
    where it got to in ``<log_dir>/clock-logs.json`` (per program run)."""
    mark_path = Path(log_dir) / "clock-logs.json"
    try:
        mark = json.loads(mark_path.read_text())
    except (OSError, ValueError):
        mark = {}
    sink = LogSink(log_dir)
    after = mark.get("next", 0)
    run = mark.get("run")
    try:
        while True:
            res = client.logs(after)
            if res["run"] != run:  # the program restarted: its log starts again from 1
                if after != 0:
                    after = 0
                    run = res["run"]
                    continue
                run = res["run"]
            for _, line in res["lines"]:
                sink.write(line)
            if res["next"] == after or not res["lines"]:
                break
            after = res["next"]
    finally:
        sink.close()
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        mark_path.write_text(json.dumps({"run": run, "next": after}))
    return sink.counts


def cmd_logs(a: argparse.Namespace) -> int:
    from arthur.tools.doorclient import DoorRefused

    log_dir = Path(a.log_dir)
    if a.mode == "pull":
        with connect(a) as c:
            counts = pull_logs(c, log_dir)
        say(f"logs: {counts['spike']} robot lines, {counts['activity']} history lines, "
            f"{counts['console']} console lines appended under {log_dir}/")
        return 0
    say(f"logs follow: appending under {log_dir}/ every 2 s; Ctrl-C stops")
    try:
        while True:
            try:
                with connect(a) as c:
                    while True:
                        counts = pull_logs(c, log_dir)
                        if any(counts.values()):
                            say(f"  +{counts['spike']} robot, +{counts['activity']} history, "
                                f"+{counts['console']} console")
                        time.sleep(2)
            except (OSError, DoorRefused) as exc:
                say(f"  lost the clock ({type(exc).__name__}); trying again in 10 s")
                time.sleep(10)
    except KeyboardInterrupt:
        return 0


# =========================================================================================
# Building, and the emulated clock
# =========================================================================================


def cmd_build(a: argparse.Namespace) -> int:
    return subprocess.run(["sh", str(DEPLOY / "micropython" / "build.sh")], check=False).returncode


def cmd_bundle(a: argparse.Namespace) -> int:
    if a.out:
        out = Path(a.out)
        if not a.source:
            check_cross()
        extra = {"arthur/onclock/main.py": a.program_stub} if a.program_stub else None
        manifest = files.build_slot(out, compiled=not a.source, extra=extra)
        folder = out
    else:
        folder, manifest = make_slot(a.source, a.program_stub)
    try:
        m = json.loads(manifest)["files"]
        for path in sorted(m):
            say(f"  {m[path][1]:>8,}  {path}")
        n, size = files.slot_size(folder)
        say(f"bundle {files.bundle_id(manifest)}: {n} files with the manifest, {size:,} bytes "
            f"({'source' if a.source else 'compiled .mpy'})" + (f", in {folder}" if a.out else ""))
        return 0
    finally:
        if not a.out:
            shutil.rmtree(folder.parent, ignore_errors=True)


STUB_DAEMON = r"""#!/bin/sh
# Stand-in for AWTRIX NG's daemon ctl, on the EMULATED clock only. Mimics: the autostart must
# be a regular file, mode 700; it runs with PATH and HOME set as on the clock. Stops the old
# run first. (No restart on failure: our launcher restarts on 42 by itself.)
LOG=/tmp/awtrix-release/daemon.log
PIDF=/tmp/awtrix-release/autostart.pid
[ "$1 $2" = "ctl restart-autostart" ] || { echo "stub: only 'ctl restart-autostart'"; exit 2; }
if [ -f $PIDF ]; then kill "$(cat $PIDF)" 2>/dev/null; rm -f $PIDF; fi
A=/data/awtrix-ng/state/autostart
# Tests only: the end-to-end run points the program at its fakes through this file.
T=$(cat /tmp/awtrix-release/arthur-test.json 2>/dev/null)
if [ -f "$A" ] && [ ! -L "$A" ] && [ "$(stat -c %a "$A")" = 700 ]; then
  env -i PATH=/bin:/sbin:/usr/bin:/usr/sbin HOME=/data ARTHUR_ONCLOCK_TEST="$T" "$A" >>$LOG 2>&1 &
  echo $! >$PIDF
  echo "stub daemon: autostart started, pid $!" >>$LOG
else
  echo "stub daemon: no usable autostart" >>$LOG
fi
"""


def cmd_emulator(a: argparse.Namespace) -> int:
    subprocess.run(["docker", "rm", "-f", EMU], capture_output=True, check=False)
    if a.action == "down":
        say("emulated clock removed")
        return 0
    subprocess.run(["docker", "run", "-d", "--init", "--name", EMU, "--platform",
                    "linux/arm/v7", "-p", f"127.0.0.1:{EMU_DOOR_PORT}:{doorproto.DOOR_PORT}",
                    "alpine:3.21", "sleep", "2147483647"], check=True, capture_output=True)
    sh = ["docker", "exec", EMU, "sh", "-c"]
    subprocess.run([*sh, "mkdir -p /data/awtrix-ng/state /tmp/awtrix-release/bin"], check=True)
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(STUB_DAEMON)
    subprocess.run(["docker", "cp", f.name, EMU + ":/tmp/awtrix-release/bin/awtrix-tc002d"],
                   check=True)
    os.unlink(f.name)
    # Like the clock's busybox: no grep, head, tr or sleep (nothing of ours may need them).
    strip = ("chmod 755 /tmp/awtrix-release/bin/awtrix-tc002d && "
             "rm -f /bin/grep /usr/bin/head /usr/bin/tr /bin/sleep")
    subprocess.run([*sh, strip], check=True)
    say(f"emulated clock '{EMU}' is up (ARMv7 Alpine under QEMU): /data/awtrix-ng/state, a "
        "stub daemon ctl; grep, head, tr and sleep removed. Use --emulate for the USB "
        f"commands and --clock 127.0.0.1:{EMU_DOOR_PORT} for the door.")
    return 0


# =========================================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clock.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--serial", help="USB: the clock's adb serial (from `adb devices -l`)")
    p.add_argument("--emulate", action="store_true",
                   help="USB commands against the emulated clock in Docker")
    p.add_argument("--clock", help="Wi-Fi: the clock's address, HOST or HOST:PORT")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be written where, and run nothing")
    p.add_argument("--key-file", type=Path, default=KEY_FILE, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build the clock's MicroPython (Docker)")
    for name in ("bundle", "install", "push"):
        s = sub.add_parser(name)
        s.add_argument("--source", action="store_true",
                       help="ship .py sources, not compiled .mpy")
        s.add_argument("--program-stub", type=Path, help=argparse.SUPPRESS)  # tests only
        if name == "bundle":
            s.add_argument("--out", help="write the slot here (a new folder)")
        else:
            s.add_argument("--config", type=Path, help="the TOML (default config/display.toml)")
        if name == "install":
            s.add_argument("--heap", default=DEFAULT_HEAP,
                           help=f"MicroPython heap on the clock (default {DEFAULT_HEAP})")
        if name == "push":
            s.add_argument("--no-wait", action="store_true",
                           help="do not wait for the program to come back after a bundle")
    sub.add_parser("status")
    sub.add_parser("remove")
    lg = sub.add_parser("logs")
    lg.add_argument("mode", nargs="?", choices=("pull", "follow"), default="pull")
    lg.add_argument("--log-dir", default="logs")
    for name in ("pause", "resume", "restart"):
        sub.add_parser(name)
    em = sub.add_parser("emulator")
    em.add_argument("action", choices=("up", "down"))
    return p


def main(argv: list[str] | None = None) -> int:
    from arthur.tools.doorclient import DoorFailed, DoorRefused

    p = build_parser()
    a = p.parse_args(argv)
    usb = bool(a.serial or a.emulate)
    if a.serial and a.emulate:
        p.error("--serial and --emulate are exclusive")
    try:
        if a.command == "build":
            return cmd_build(a)
        if a.command == "bundle":
            return cmd_bundle(a)
        if a.command == "emulator":
            return cmd_emulator(a)
        if a.command in ("install", "remove") or (a.command in ("status", "logs") and usb):
            if not usb and not a.dry_run:
                p.error("--serial SERIAL is required (see `adb devices -l`); the tool never "
                        "picks a device on its own")
            if not usb and a.dry_run:
                a.serial = None
            dev = Device(a.serial, a.emulate, a.dry_run)
            table = {"install": cmd_install, "remove": cmd_remove, "status": cmd_usb_status,
                     "logs": cmd_usb_logs}
            try:
                code = table[a.command](a, dev)
            except BaseException:
                if not a.dry_run:  # leave nothing of ours in the clock's RAM
                    dev.shell(f"rm -rf {TOOL} {STAGE} {SECRETS_TMP}", show=False)
                raise
            if a.dry_run:
                say("\n(dry run: nothing was sent to the clock; the steps above assume every "
                    "check passes)")
            return code
        if a.command == "push":
            return cmd_push(a)
        if a.command == "status":
            return cmd_door_status(a)
        if a.command == "logs":
            return cmd_logs(a)
        return cmd_simple(a.command)(a)
    except ToolError as exc:
        say(f"REFUSED: {exc}" if "Nothing written" in str(exc) else f"FAILED: {exc}")
        return 1
    except (DoorRefused, DoorFailed) as exc:
        say(f"FAILED: the clock's door: {exc}")
        return 1
    except OSError as exc:
        say(f"FAILED: could not reach the clock ({type(exc).__name__}: {exc.strerror or exc})")
        return 1
