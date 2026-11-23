"""The whole program on the clock, end to end under MicroPython, as it is installed.

``sh`` runs the real ``deploy/clock/autostart.sh`` (filled in by ``render_autostart``), which
runs the real ``boot.py`` on slot a as ``scripts/clock.py bundle --out`` builds it (compiled
``.mpy``, the default), which runs the real ``arthur.onclock.main``: the app, the clock's
Whisker source and the update door, against the fake Whisker over TLS with the throwaway CA,
the repository's fake clock and a fake Open-Meteo, all on 127.0.0.1
(``tests/onclock_e2e.py``). The Mac's side is ``scripts/clock.py --clock`` itself.

One run, in real time (the robot's timeline compressed :data:`SPEED` times), covers, in
order: the first start with no tokens (a password login); a real visit from
``tests/data/real-cycles.jsonl``; a visit with no weight; a token refused (renewed), then a
renewal refused (a password login again); the live connection going silent; an outage with
a visit found in the history afterwards; ``push`` of a config change; ``push`` of a code
change; a bundle that fails at import and one that starts and dies, each falling back;
new code that fails pushed with a config only it would take (the old slot runs on its kept
copy of the config), then a good push that clears it; ``pause``, the Mac's feeder taking
the screen, ``resume``; the fake clock restarted; ``logs pull``; last, the same stranding
with the kept copies deleted, which spends both slots, as it did before kept copies. The
checks below each read what the run recorded.

Skips, saying the on-clock end-to-end test did NOT run, when ``micropython`` is missing.
``ARTHUR_MICROPYTHON`` names another binary (the clock's ARMv7 build under emulation).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from onclock_e2e import (
    DOOR_KEY,
    PASSWORD,
    REPO,
    SAMPLE,
    USERNAME,
    EmuInstall,
    Fakes,
    Install,
    visit_records,
)
from wirefake import raw_from_record
from wirehelp import real_lines

from arthur.clocktool import config_bytes, make_slot
from arthur.config import config_from_json
from arthur.mapping import to_display_state
from arthur.model import FeederHealth
from arthur.render import render
from arthur.sources.logfile import RobotLog
from arthur.sources.openmeteo import parse_open_meteo

MPY = os.environ.get("ARTHUR_MICROPYTHON") or shutil.which("micropython")
#: ``ARTHUR_E2E_EMULATED=1``: the run on the emulated ARMv7 clock instead (by hand, once;
#: Docker, ``deploy/clock/micropython/dist/micropython-armv7``). Functional only.
EMULATED = os.environ.get("ARTHUR_E2E_EMULATED") == "1"
SLOW = 8.0 if EMULATED else 1.0
#: By hand, for the memory measurements: ``ARTHUR_E2E_DAY=1`` leaves out the code pushes
#: (each restarts the program), so the whole run is one program's "day";
#: ``ARTHUR_E2E_HEAP`` sets the launcher's heap (default the installer's, 3M).
DAY_ONLY = os.environ.get("ARTHUR_E2E_DAY") == "1"
HEAP = os.environ.get("ARTHUR_E2E_HEAP", "3M")
needs_mpy = pytest.mark.skipif(
    MPY is None and not EMULATED, reason="micropython is not on PATH: the on-clock program's end-to-end test "
    "(install layout, boot.py, the real source and door) did NOT run (brew install "
    "micropython)")
pytestmark = [needs_mpy, pytest.mark.usefixtures("loopback_only")]

#: The robot's timeline is played this many times faster than it happened.
SPEED = 15.0
NO_DATA_AFTER_S = 20
FALLBACK_S = 12
HERE = Path(__file__).resolve().parent


def short_toml(base: Path, *, fur: str = "#FF7A00", text_color: str = "#FFE4C4",
               extra_caption: str | None = None) -> Path:
    """config/display.toml with short timings for a run in real time."""
    text = (REPO / "config" / "display.toml").read_text()
    text = text.replace("no_data_after_s = 600", f"no_data_after_s = {NO_DATA_AFTER_S}")
    text = text.replace("cat_left_fallback_s = 120", f"cat_left_fallback_s = {FALLBACK_S}")
    text = text.replace('fur       = "#FF7A00"', f'fur       = "{fur}"')
    text = text.replace('text      = "#FFE4C4"', f'text      = "{text_color}"')
    if extra_caption:
        text = text.replace('in_use = ["IN SESH"', f'in_use = ["{extra_caption}", "IN SESH"')
    path = base / f"display-{fur[1:]}-{extra_caption or 'x'}.toml"
    path.write_text(text)
    return path


def stub_main(base: Path, name: str, tail: str) -> Path:
    """The real ``arthur/onclock/main.py`` with ``tail`` appended (a code change)."""
    path = base / f"main-{name}.py"
    path.write_text((REPO / "arthur" / "onclock" / "main.py").read_text() + "\n" + tail)
    return path


CHANGED = "PUSHED_IN_TEST = 2\n"
BROKEN = 'raise RuntimeError("this bundle is broken at import on purpose")\n'
DIES = '''
_real_main = main


async def main(root, source_factory, **kw):
    import asyncio

    task = asyncio.create_task(_real_main(root, source_factory, **kw))
    await asyncio.sleep(3)
    task.cancel()
    raise RuntimeError("this bundle dies after 3 s on purpose")
'''


class Run:
    """What the run recorded."""

    def __init__(self) -> None:
        #: While set, the robot sends a small update every few seconds (the real one is
        #: polled every 300 s against a NO DATA line of 600; this run's line is 20 s).
        self.keepalive = True
        self.marks: dict[str, float] = {}
        self.data: dict = {}
        self.statuses: list[dict] = []
        self.door_texts: list[str] = []

    def mark(self, name: str) -> float:
        t = time.time()
        self.marks[name] = t
        return t


async def play(fakes: Fakes, records, t0: float) -> float:
    """Push each record when its compressed time comes; returns when the last went."""
    for t, rec in records:
        due = t0 + t / SPEED
        delay = due - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        await fakes.push_record(rec)
    return time.time()


async def until(cond, timeout: float, what: str) -> float:
    end = time.monotonic() + timeout * SLOW
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting for " + what)
        await asyncio.sleep(0.1)
    return time.time()


async def scenario(base: Path, tls_files) -> tuple[Run, Install, Fakes]:
    run = Run()
    fakes = Fakes(tls_files)
    lines = real_lines()
    fakes.whisker.robots[0] = raw_from_record(lines[0]["record"])
    now = time.time()
    fakes.add_history(now - 3 * 3600, "robotStatusCatDetect")
    fakes.add_history(now - 3 * 3600 + 70, "catWeight", 4.67)
    await fakes.start()
    inst = EmuInstall(base, heap=HEAP) if EMULATED else Install(base, mpy=MPY, heap=HEAP)
    toml = short_toml(base)
    run.data["bundle_a"] = inst.lay_out(toml)
    run.data["cfg0"] = json.loads(config_bytes(toml))  # what install writes as config.json
    async def keepalive():
        rssi = -41.0
        while True:
            await asyncio.sleep(6)
            if run.keepalive and fakes.whisker.subscribers:
                rssi = -42.0 if rssi == -41.0 else -41.0
                await fakes.whisker.push({"wifiRssi": rssi}, partial=True)

    ka = asyncio.create_task(keepalive())
    try:
        await inst.start(fakes)
        run.data["bundle_a"] = run.data["bundle_a"] or getattr(inst, "bundle_id", "")
        await _phases(run, inst, fakes, base)
    finally:
        ka.cancel()
        run.data["launcher_alive_at_end"] = inst.launcher_alive()
        run.data["launcher_exit"] = await inst.stop()
        inst.collect()
        # The emulated daemon restarts a launcher that exited (after the halt), and each
        # launcher starts a new console.log: keep what came before.
        console, before = inst.console(), run.data.pop("console_before_halt", "")
        run.data["console"] = console if console.startswith(before) else before + console
        run.data["boot"] = inst.boot_notes()
        await fakes.close()
    return run, inst, fakes


async def _status(run: Run, inst: Install) -> dict:
    rc, out, _ = await inst.clock_py("status")
    assert rc == 0, out
    run.door_texts.append(out)
    st = json.JSONDecoder().raw_decode(out)[0]  # a warning line may follow the JSON
    run.statuses.append(st)
    return st


def rejected_config(cfg: dict) -> bytes:
    """``cfg`` with a key the program's code rejects (as a newer program's config might)."""
    data = dict(cfg, timings=dict(cfg["timings"], zz_new_key=1))
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def door_bundle(inst: Install, stub: Path, config: bytes) -> dict:
    """A bundle and a config sent straight through the door (``scripts/clock.py push``
    exports the config with the Mac's code, so it cannot send one this code rejects)."""
    from arthur.tools.doorclient import DoorClient

    slot, manifest = make_slot(not inst.compiled, stub)
    try:
        with DoorClient("127.0.0.1", inst.door_port, DOOR_KEY) as c:
            return c.bundle(slot, manifest, config=config)
    finally:
        shutil.rmtree(slot.parent, ignore_errors=True)


async def _phases(run: Run, inst: Install, fakes: Fakes, base: Path) -> None:
    w = fakes.whisker
    # -- 1. the first start: no tokens, a password login ----------------------------------
    run.mark("start")
    run.marks["first_frame"] = await inst.wait_console("first frame pushed", 60)
    await inst.wait_console("Whisker connected", 60)
    run.marks["ready"] = await inst.wait_console("-> ready", 60)
    run.data["login_after_start"] = dict(w.counts)
    run.data["tokens_after_login"] = json.loads(inst.read_root("tokens.json"))
    await until(lambda: "slot a is healthy" in inst.boot_notes(), 30, "healthy")
    run.mark("healthy")
    run.data["first_status"] = await _status(run, inst)
    await asyncio.sleep(2)

    # -- 2. a real visit -----------------------------------------------------------------
    run.mark("visit1")
    end = await play(fakes, visit_records(1, 11), time.time())
    await asyncio.sleep(12)
    run.mark("visit1_end")
    run.data["visit1_last_push"] = end

    # -- 3. a visit with no weight --------------------------------------------------------
    run.mark("visit2")
    await play(fakes, visit_records(14, 23, drop_weight=True), time.time())
    await asyncio.sleep(12)
    run.mark("visit2_end")

    # -- 4. a token refused (renewed), then a renewal refused (a password login) ----------
    run.mark("tokens")
    before = dict(w.counts)
    w.revoke()
    await w.drop_sockets()
    await until(lambda: w.counts["refresh"] > before["refresh"], 30, "a renewal")
    await until(lambda: w.subscribers, 30, "the live connection back")
    run.data["after_renewal"] = dict(w.counts)
    run.data["tokens_file_after_renewal"] = inst.read_root("tokens.json")
    w.reject_refresh = True
    w.revoke()
    await w.drop_sockets()
    await until(lambda: w.counts["login"] > before["login"], 60, "a password login")
    await until(lambda: w.subscribers, 30, "the live connection back")
    w.reject_refresh = False
    run.data["after_relogin"] = dict(w.counts)
    run.data["tokens_file_after_relogin"] = inst.read_root("tokens.json")
    run.mark("tokens_end")

    # -- 5. the live connection goes silent -----------------------------------------------
    run.mark("silent")
    ws_before = w.counts["ws"]
    run.keepalive = False
    w.silent = True
    await inst.wait_console("stale after", 30)
    w.silent = False
    await until(lambda: w.counts["ws"] > ws_before and w.subscribers, 30, "reconnect")
    run.keepalive = True
    await asyncio.sleep(8)
    run.mark("silent_end")

    # -- 6. an outage, and a visit found in the history afterwards ------------------------
    run.mark("outage")
    run.keepalive = False
    w.ws_script = ["refuse500"] * 200
    w.fail["graphql"] = [("status", 503)] * 200
    await w.drop_sockets()
    t_out = time.time()
    off = len(inst.console())
    await inst.wait_console("-> no_data", NO_DATA_AFTER_S + 20, after=off)
    run.mark("no_data")
    fakes.add_history(t_out + 3, "robotStatusCatDetect")
    fakes.add_history(t_out + 8, "catWeight", 5.5)
    w.robots[0]["odometerCleanCycles"] += 1
    w.ws_script = []
    w.fail["graphql"] = []
    await until(lambda: w.subscribers, 60, "the live connection after the outage")
    await w.push({"wifiRssi": -40.0}, partial=True)
    await inst.wait_console("Not watching from", 30, after=off)
    run.marks["moved"] = await inst.wait_console("Last visit moved", 30, after=off)
    await inst.wait_console("Whisker contact regained", 30, after=off)
    run.keepalive = True
    run.data["outage_start"] = t_out
    run.mark("outage_end")

    # -- 7. push: a config change -----------------------------------------------------------
    run.mark("config_push")
    new_toml = short_toml(base, fur="#FF7A08", text_color="#FFE4C8", extra_caption="E2E")
    run_id = run.statuses[-1]["run"]
    off = len(inst.console())
    rc, out, secs = await inst.clock_py("push", "--config", str(new_toml))
    assert rc == 0, out
    run.data["config_push"] = {"seconds": secs, "out": out}
    await inst.wait_console("config reloaded", 10, after=off)
    run.mark("config_reloaded")
    st = await _status(run, inst)
    run.data["config_push"]["same_run"] = st["run"] == run_id
    run.data["cfg1"] = json.loads(inst.read_root("config.json"))
    await asyncio.sleep(8)
    run.mark("config_push_end")

    # The log ring is in the program's memory: a restart loses what was not pulled. The Mac
    # pulls before the code pushes, as `logs follow` would have.
    logs = base / "pulled"
    rc, out, secs = await inst.clock_py("logs", "pull", "--log-dir", str(logs))
    assert rc == 0, out
    run.data["logs_pull_before_restarts"] = {"seconds": secs, "out": out}

    if not DAY_ONLY:  # the restarts would cut the "day" into pieces
        # -- 8. push: a code change -------------------------------------------------------------
        run.mark("code_push")
        pid = inst.launcher_pid()
        rc, out, secs = await inst.clock_py("push", "--config", str(new_toml), "--program-stub",
                                            str(stub_main(base, "changed", CHANGED)))
        assert rc == 0, out
        run.data["code_push"] = {"seconds": secs, "out": out}
        run.mark("code_back")
        await asyncio.sleep(4)
        st = await _status(run, inst)
        run.data["code_push"].update(status=st, launcher_pid_same=inst.launcher_pid() == pid,
                                     launcher_alive=inst.launcher_alive())
        run.mark("code_push_end")

        # -- 9. a bundle that fails at import, and one that starts and then dies ---------------
        for name, tail in (("broken", BROKEN), ("dies", DIES)):
            run.mark(name)
            notes = len(inst.boot_notes())
            rc, out, secs = await inst.clock_py("push", "--config", str(new_toml), "--no-wait",
                                                "--program-stub", str(stub_main(base, name, tail)))
            assert rc == 0, out
            await until(lambda n=notes: "falling back" in inst.boot_notes()[n:], 90,
                        "fall-back")
            run.mark(name + "_fell_back")
            run.marks[name + "_frames"] = await until(
                lambda t=run.marks[name + "_fell_back"]: any(r["t"] > t for r in fakes.received),
                60, "frames after the fall-back")
            await asyncio.sleep(6)  # the restored slot becomes healthy again
            st = await _status(run, inst)
            run.data[name] = {"status": st, "notes": inst.boot_notes()[notes:],
                              "launcher_alive": inst.launcher_alive()}

        # -- 9b. new code that fails, pushed with a config only it would take ------------------
        # Before kept copies this spent both slots: the new code fails, boot falls back, the
        # old code rejects the new config.json. Now the old slot runs on its kept copy.
        run.mark("strand")
        notes, off = len(inst.boot_notes()), len(inst.console())
        bad = rejected_config(run.data["cfg1"])
        run.data["strand_config"] = hashlib.sha256(bad).hexdigest()[:16]
        await asyncio.to_thread(door_bundle, inst, stub_main(base, "strand", BROKEN), bad)
        await until(lambda: "falling back" in inst.boot_notes()[notes:], 90, "fall-back")
        run.marks["strand_kept"] = await inst.wait_console("running on the kept copy", 60,
                                                           after=off)
        await until(lambda: "slot b is healthy" in inst.boot_notes()[notes:], 60, "healthy")
        st = await _status(run, inst)
        run.data["strand"] = {"status": st, "notes": inst.boot_notes()[notes:],
                              "console": inst.console()[off:],
                              "door_text": run.door_texts[-1]}
        # A working bundle with a valid, different config takes over and clears the error.
        run.mark("unstrand")
        good_toml = short_toml(base, fur="#FF7A10", text_color="#FFE4D0")
        rc, out, secs = await inst.clock_py("push", "--config", str(good_toml), "--program-stub",
                                            str(stub_main(base, "fixed", "PUSHED_IN_TEST = 3\n")))
        assert rc == 0, out
        await asyncio.sleep(2)
        st = await _status(run, inst)
        run.data["unstrand"] = {"status": st, "out": out,
                                "config": hashlib.sha256(config_bytes(good_toml))
                                .hexdigest()[:16]}
        run.mark("unstrand_end")

    # -- 10. pause, the Mac's feeder on the screen, resume ---------------------------------
    run.mark("pause")
    rc, out, _ = await inst.clock_py("pause")
    assert rc == 0, out
    run.mark("paused")
    await until(lambda: not w.subscribers, 15, "the live connection closed")
    run.mark("ws_closed")
    from arthur.transports.awtrix_ng import AwtrixNgTransport

    mac = AwtrixNgTransport(f"http://127.0.0.1:{fakes.clock_port}")
    cfg = config_from_json(run.data["cfg1"])
    frame = render(to_display_state(None, FeederHealth(None, True, "api"),
                                    datetime.now(UTC), cfg), datetime.now(UTC), cfg)
    run.data["mac_sha"] = hashlib.sha256(frame.to_rgb888()).hexdigest()
    try:
        for _ in range(6):
            await mac.show(frame, 90)
            await asyncio.sleep(1)
    finally:
        await mac.close()
    run.mark("takeover_end")
    off = len(inst.console())
    rc, out, _ = await inst.clock_py("resume")
    assert rc == 0, out
    run.mark("resumed")
    await until(lambda: w.subscribers, 30, "the live connection after resume")
    await inst.wait_console("Not watching from", 30, after=off)
    run.mark("resume_end")

    # -- 11. the fake clock restarted ---------------------------------------------------------
    run.mark("clock_restart")
    off = len(inst.console())
    await fakes.stop_clock()
    # Down until the program has tried to push and failed (a frame changes, or the 30 s
    # heartbeat falls due): only then can it say it is reachable again.
    await inst.wait_console("clock unreachable", 45, after=off)
    await fakes.start_clock()
    await inst.wait_console("clock reachable again", 30, after=off)
    run.mark("clock_back")
    await asyncio.sleep(3)

    if DAY_ONLY:
        # The memory runs: a whole idle round too, so the weather page and a scene (whose
        # plan is the program's largest piece of memory) are in the "day".
        run.mark("idle_round")
        await asyncio.sleep(200 * (2 if EMULATED else 1))
        run.mark("idle_round_end")

    # -- 12. logs pull, and the last status ------------------------------------------------
    run.data["final_status"] = await _status(run, inst)
    logs = base / "pulled"
    rc, out, secs = await inst.clock_py("logs", "pull", "--log-dir", str(logs))
    assert rc == 0, out
    run.data["logs_pull"] = {"seconds": secs, "out": out, "dir": str(logs)}
    run.data["clocks"] = [{"apps": sorted(c.apps), "enlarge": c.enlarge_apps,
                           "rotation": c.in_rotation()} for c in fakes.clocks]
    run.mark("end")

    if not DAY_ONLY:
        # -- 13. the same stranding with the kept copies deleted: both slots spent --------------
        # What happened before kept copies (and what still happens when none loads): the
        # program halts and only USB would reach the clock. Last, since it ends the run.
        run.data["launcher_alive_before_halt"] = inst.launcher_alive()
        run.data["console_before_halt"] = inst.console()
        notes = len(inst.boot_notes())
        for s in ("a", "b"):
            inst.remove_root(f"config.good.{s}.json")
        await asyncio.to_thread(door_bundle, inst, stub_main(base, "strand", BROKEN),
                                rejected_config(run.data["cfg1"]))

        def halted() -> bool:
            try:
                return bool(inst.state().get("halted"))
            except ValueError:  # read while being replaced (the emulated clock's cat)
                return False

        await until(halted, 120, "both slots spent")
        run.mark("halted")
        run.data["halted"] = {"state": inst.state(), "notes": inst.boot_notes()[notes:]}


@pytest.fixture(scope="module")
def e2e(tmp_path_factory, tls_files):
    import socket as socketmod

    real = socketmod.getaddrinfo

    def guard(host, *a, **kw):
        name = host.decode() if isinstance(host, bytes) else host
        if name not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("the end-to-end run tried to reach " + repr(name))
        return real(host, *a, **kw)

    keep = os.environ.get("ARTHUR_E2E_KEEP")
    if keep and (Path(keep) / "run.json").exists():
        return load_result(Path(keep))  # checking a kept run again, without running it
    base = Path(keep) if keep else tmp_path_factory.mktemp("e2e")
    base.mkdir(parents=True, exist_ok=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socketmod, "getaddrinfo", guard)
        run, inst, fakes = asyncio.run(scenario(base, tls_files))
    print("\nend-to-end marks (s from start):", json.dumps(
        {k: round(v - run.marks["start"], 2) for k, v in run.marks.items()}))
    result = (run, SimpleNamespace(root=inst.root, tmp=inst.tmp), SimpleNamespace(
        received=fakes.received, issued=fakes.issued, weather_hits=fakes.weather_hits,
        refresh_tokens=sorted(fakes.whisker.refresh_tokens)))
    save_result(base, result)
    return result


def save_result(base: Path, result) -> None:
    """The run's record, so the checks can be run again on it (``ARTHUR_E2E_KEEP=DIR``)."""
    run, inst, fakes = result
    (base / "run.json").write_text(json.dumps({
        "marks": run.marks, "data": run.data, "statuses": run.statuses,
        "door_texts": run.door_texts, "root": str(inst.root), "tmp": str(inst.tmp),
        "fakes": vars(fakes)}, default=str))


def load_result(base: Path):
    d = json.loads((base / "run.json").read_text())
    run = Run()
    run.marks, run.data, run.statuses, run.door_texts = (d["marks"], d["data"], d["statuses"],
                                                         d["door_texts"])
    return (run, SimpleNamespace(root=Path(d["root"]), tmp=Path(d["tmp"])),
            SimpleNamespace(**d["fakes"]))


# =============================================================================================
# The checks
# =============================================================================================


def _cfg(data: dict):
    return config_from_json(data)


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def frames_in(fakes: Fakes, t0: float, t1: float) -> list[dict]:
    return [r for r in fakes.received if t0 <= r["t"] <= t1]


def cpython_frames(run: Run, fakes: Fakes, t0: float, t1: float, cfg) -> dict:
    """Every frame the fake clock got in ``[t0, t1]``, against what CPython draws from the
    pulled log (``arthur.sources.logfile``) for the same moment: the beat it was pushed on,
    or up to three before (a push lands after its beat). Returns counts and the states."""
    log = RobotLog(Path(run.data["logs_pull"]["dir"]), just_cleaned_s=cfg.timings.just_cleaned_s,
                   visit_min_s=cfg.timings.visit_min_s)
    log.refresh()
    tl = log.timeline
    weather = parse_open_meteo(SAMPLE, _dt(fakes.weather_hits[0])) if fakes.weather_hits \
        else None
    out = {"frames": 0, "matched": 0, "kinds": {}, "unmatched": []}
    cache: dict = {}
    for r in frames_in(fakes, t0, t1):
        out["frames"] += 1
        beat = int(r["t"] * 8) / 8
        found = None
        for k in range(4):
            b = _dt(beat - k / 8)
            st = tl.at(b)
            if st is None:
                continue
            health = FeederHealth(last_contact_at=b, ws_connected=True)
            for wx in (weather, None):
                key = (b, wx is None)
                if key not in cache:
                    state = to_display_state(st.snapshot, health, b, cfg, weather=wx)
                    cache[key] = (hashlib.sha256(render(state, b, cfg).to_rgb888())
                                  .hexdigest(), state.kind)
                sha, kind = cache[key]
                if sha == r["sha"]:
                    found = kind
                    break
            if found:
                break
        if not found:
            # Not on a whole beat: the program draws at the moment its beat woke, which is a
            # few ms after the tick (more on a slow or busy machine), and dates what it saw
            # a little before the log line it writes. Look at each millisecond of the 250 ms
            # before the frame arrived; those found are counted apart, with how far off.
            for ms in range(250):
                b = _dt(r["t"]) - timedelta(milliseconds=ms)
                st = tl.at(b)
                if st is None:
                    continue
                health = FeederHealth(last_contact_at=b, ws_connected=True)
                state = to_display_state(st.snapshot, health, b, cfg, weather=weather)
                if hashlib.sha256(render(state, b, cfg).to_rgb888()).hexdigest() == r["sha"]:
                    found = state.kind
                    off = round((b.timestamp() - beat) * 1000)
                    out.setdefault("off_beat_ms", []).append(off)
                    break
        if found:
            out["matched"] += 1
            out["kinds"][found] = out["kinds"].get(found, 0) + 1
        else:
            out["unmatched"].append(round(r["t"] - t0, 3))
    return out


def test_first_start_logs_in_once_and_saves_the_tokens(e2e):
    run, _inst, fakes = e2e
    assert run.data["login_after_start"]["login"] == 1
    assert run.data["login_after_start"]["refresh"] == 0
    tok = run.data["tokens_after_login"]
    assert tok["refresh_token"] == fakes.issued[0]["refresh_token"]
    assert run.marks["first_frame"] - run.marks["start"] < 15 * SLOW
    st = run.data["first_status"]
    assert st["app"]["version"] == run.data["bundle_a"] == st["slots"]["a"]
    assert st["running"] == "a" and st["tries"]["a"] == 0  # healthy cleared the count


def test_a_real_visit_draws_the_same_frames_as_cpython(e2e):
    run, _inst, fakes = e2e
    cfg = _cfg(run.data["cfg0"])
    res = cpython_frames(run, fakes, run.marks["visit1"], run.marks["visit1_end"], cfg)
    print("visit 1:", json.dumps(res))
    assert {"in_use", "waiting", "cleaning", "ready"} <= set(res["kinds"]), res["kinds"]
    assert res["frames"] > 50 and (EMULATED or res["unmatched"] == []), res


def test_a_visit_with_no_weight_waits_without_a_countdown(e2e):
    run, _inst, fakes = e2e
    cfg = _cfg(run.data["cfg0"])
    res = cpython_frames(run, fakes, run.marks["visit2"], run.marks["visit2_end"], cfg)
    print("visit 2 (no weight):", json.dumps(res))
    assert {"in_use", "waiting", "cleaning", "ready"} <= set(res["kinds"]), res["kinds"]
    assert EMULATED or res["unmatched"] == [], res
    # No weight: the status stays CAT_DETECTED until the clean, and the waiting screen came
    # only after the fallback time.
    log = RobotLog(Path(run.data["logs_pull"]["dir"]))
    log.refresh()
    st = log.timeline.at(_dt(run.marks["visit2"] + 553 / SPEED - 2))
    assert st.snapshot.status == "CAT_DETECTED" and st.snapshot.cat_left_at is None


def test_tokens_renewal_rejected_renewal_and_relogin(e2e):
    run, _inst, fakes = e2e
    a, b = run.data["after_renewal"], run.data["after_relogin"]
    assert a["refresh"] >= 1 and a["login"] == 1
    assert b["login"] == 2
    # The flash: written at the first login and at the re-login, not at the renewal.
    assert run.data["tokens_file_after_renewal"] == json.dumps(run.data["tokens_after_login"]) \
        or json.loads(run.data["tokens_file_after_renewal"]) == run.data["tokens_after_login"]
    relog = json.loads(run.data["tokens_file_after_relogin"])
    assert relog["refresh_token"] != run.data["tokens_after_login"]["refresh_token"]
    assert relog["refresh_token"] in fakes.refresh_tokens


def test_silence_and_an_outage(e2e):
    run, _inst, _fakes = e2e
    con = run.data["console"]
    assert "stale after" in con
    assert "Whisker contact lost" in con and "Whisker contact regained" in con
    assert "Not watching from" in con and "Last visit moved" in con
    # NO DATA came within the configured time of the outage, not before.
    lag = run.marks["no_data"] - run.data["outage_start"]
    # (counted from the outage's start; the last contact was up to 6 s before it)
    assert NO_DATA_AFTER_S - 7 <= lag <= NO_DATA_AFTER_S + 10 * SLOW, lag


def test_push_of_a_config_change(e2e):
    run, _inst, fakes = e2e
    cp = run.data["config_push"]
    print("config push:", round(cp["seconds"], 2), "s;", cp["out"].strip())
    assert "config" in cp["out"] and cp["same_run"]  # no restart
    assert cp["seconds"] < 10 * SLOW
    new = _cfg(run.data["cfg1"])
    assert "E2E" in new.captions["in_use"]
    # The colours on the screen: the old fur and text before the push, the new after it
    # (a frame drawn just before the reload may land just after it, so 0.3 s are left).
    old_c, new_c = {"ff7a00", "ffe4c4"}, {"ff7a08", "ffe4c8"}
    before = frames_in(fakes, run.marks["config_push"] - 30, run.marks["config_push"])
    after = frames_in(fakes, run.marks["config_reloaded"] + 0.3, run.marks["config_push_end"])
    print("config push: frames", len(before), "before,", len(after), "after")
    assert any(old_c & set(r["colors"]) for r in before)
    assert not any(new_c & set(r["colors"]) for r in before)
    assert after and not any(old_c & set(r["colors"]) for r in after)
    assert any(new_c & set(r["colors"]) for r in after)


def restart_gap(console: str) -> float | None:
    """Seconds from the first "stopped (exit 42)" to the next "first frame pushed" (the
    screen keeps the old frame meanwhile; AWTRIX shows it until its lifetime runs out)."""
    stop = None
    for line in console.splitlines():
        if stop is None and "stopped (exit 42)" in line:
            stop = datetime.fromisoformat(line.split()[0])
        elif stop is not None and "first frame pushed" in line:
            return round((datetime.fromisoformat(line.split()[0]) - stop).total_seconds(), 3)
    return None


def test_push_of_a_code_change(e2e):
    run, _inst, _fakes = e2e
    if DAY_ONLY:
        pytest.skip("ARTHUR_E2E_DAY: no code pushes in this run")
    cp = run.data["code_push"]
    st = cp["status"]
    print("code push:", round(cp["seconds"], 2), "s;", cp["out"].strip().splitlines()[-1])
    gap = restart_gap(run.data["console"])
    print("from the old program's last beat to the new one's first frame:", gap, "s")
    assert gap is not None and gap < 5 * SLOW
    assert st["running"] == "b" and st["active"] == "b"
    assert st["app"]["version"] == st["slots"]["b"] != st["slots"]["a"]
    assert cp["launcher_pid_same"] and cp["launcher_alive"]
    assert "starting slot b, try 1" in run.data["boot"]
    assert "program ended with 42" in run.data["boot"]


@pytest.mark.parametrize("name,why", [("broken", "import: RuntimeError"),
                                      ("dies", "run: RuntimeError")])
def test_a_bad_bundle_falls_back(e2e, name, why):
    run, _inst, _fakes = e2e
    if DAY_ONLY:
        pytest.skip("ARTHUR_E2E_DAY: no code pushes in this run")
    d = run.data[name]
    st = d["status"]
    print(name + ":", "fell back after", round(run.marks[name + "_fell_back"] - run.marks[name],
                                                 1), "s; frames again after",
          round(run.marks[name + "_frames"] - run.marks[name], 1), "s")
    assert st["running"] == "b" and st["active"] == "b"
    assert st["tries"]["a"] == 3
    assert st["errors"]["a"].startswith(why), st["errors"]
    assert "falling back from slot a to slot b" in d["notes"]
    assert d["launcher_alive"]


def test_a_bad_config_pushed_with_failing_code_runs_on_the_kept_copy(e2e):
    run, _inst, _fakes = e2e
    if DAY_ONLY:
        pytest.skip("ARTHUR_E2E_DAY: no code pushes in this run")
    d = run.data["strand"]
    st, app = d["status"], d["status"]["app"]
    print("strand: fell back and ran on the kept copy after",
          round(run.marks["strand_kept"] - run.marks["strand"], 1), "s")
    # boot fell back from the failing new code to the old slot, which kept running.
    assert "falling back from slot a to slot b" in d["notes"]
    assert st["running"] == "b" and st["active"] == "b" and st["tries"]["a"] == 3
    assert st["tries"]["b"] == 0 and not st["halted"]
    # On its kept copy, saying why; the flash holds the refused file.
    assert st["config"] == run.data["strand_config"] != app["config_in_use"]
    assert app["config_in_use"] == app["config_kept"]
    assert app["config_error"].startswith("config.json refused (")
    assert "zz_new_key" in app["config_error"]
    assert app["config_error"].endswith("config.good.b.json")
    assert d["console"].count("running on the kept copy") == 1
    assert "WARNING: the clock is not using the config on its flash" in d["door_text"]
    # The good push: warned first, then took over and cleared it.
    u = run.data["unstrand"]
    assert u["out"].count("WARNING: the clock is not using the config on its flash") == 1
    us, uapp = u["status"], u["status"]["app"]
    assert us["running"] == "a" and us["config"] == u["config"]
    assert uapp["config_error"] is None
    assert uapp["config_in_use"] == uapp["config_kept"] == u["config"]


def test_without_kept_copies_the_same_push_spends_both_slots(e2e):
    """What happened before kept copies: the clock halted, and only USB reaches it."""
    run, _inst, _fakes = e2e
    if DAY_ONLY:
        pytest.skip("ARTHUR_E2E_DAY: no code pushes in this run")
    h = run.data["halted"]
    assert h["state"]["halted"] and h["state"]["tries"] == {"a": 3, "b": 3}
    assert "both slots failed" in h["notes"]
    assert h["state"]["errors"]["b"].startswith("import: RuntimeError")
    assert h["state"]["errors"]["a"].startswith("run: exited with 1")


def test_pause_lets_the_mac_take_the_screen_then_resume(e2e):
    run, _inst, fakes = e2e
    p, r = run.marks["paused"], run.marks["resumed"]
    # The pause is checked up to the moment ``resume`` was sent, not to when the command
    # returned: the program draws again on its next beat, within 125 ms of the door's
    # answer, and that frame can reach the clock before the Mac's command has exited.
    end = run.marks["takeover_end"]
    during = [x for x in fakes.received if p + 0.5 < x["t"] < end]
    stray = [(round(x["t"] - p, 3), round(x["t"] - end, 3), x["ua"]) for x in during
             if x["sha"] != run.data["mac_sha"] or x["ua"] == "arthur-clock"]
    assert during and not stray, stray  # only the Mac's
    first = min(x["t"] for x in fakes.received if x["t"] > end and x["ua"] == "arthur-clock")
    print("resume: the program's first frame came", round(first - r, 3),
          "s after the Mac's command returned (negative: before)")
    after = [x for x in fakes.received if x["t"] > max(r, first)]
    assert after and all(x["ua"] == "arthur-clock" for x in after)
    assert run.marks["ws_closed"] - p < 15 * SLOW


def test_the_fake_clock_restarted(e2e):
    run, _inst, _fakes = e2e
    clocks = run.data["clocks"]
    assert len(clocks) == 2
    c = clocks[-1]
    assert "litter" in c["apps"] and not c["enlarge"] and c["rotation"] == ["litter"]
    assert run.marks["clock_back"] - run.marks["clock_restart"] < 60 * SLOW


def test_every_frame_has_the_lifetime_and_the_mark(e2e):
    run, _inst, fakes = e2e
    ours = [r for r in fakes.received if r["sha"] != run.data.get("mac_sha")]
    assert {(r["lifetime"], r["expiry"]) for r in ours} == {(90000, "mark")}


def test_logs_pull_replays_the_states_the_program_showed(e2e):
    """Every "display: a -> b" the program logged in its first run, at its moment, is the
    state the studio's reader gives for the pulled log (healthy contact assumed, as the
    reader's replay does)."""
    run, _inst, _fakes = e2e
    print("logs pull:", run.data["logs_pull"]["out"].strip(), round(
        run.data["logs_pull"]["seconds"], 2), "s")
    logs = Path(run.data["logs_pull"]["dir"])
    assert list(logs.glob("spike-2*.jsonl")) and list(logs.glob("spike-activity-*.jsonl"))
    console = (logs / "feeder-console.log").read_text()
    assert "arthur.whisker: Whisker connected" in console
    cfg = _cfg(run.data["cfg0"])
    log = RobotLog(logs, just_cleaned_s=cfg.timings.just_cleaned_s,
                   visit_min_s=cfg.timings.visit_min_s)
    log.refresh()
    checked = 0
    for line in console.splitlines():
        if " -> " not in line or "display:" not in line:
            continue
        when = datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").astimezone(UTC)
        if not run.marks["visit1"] <= when.timestamp() <= run.marks["visit2_end"]:
            continue
        shown = line.rsplit(" -> ", 1)[1].split(" ")[0]
        b = when + timedelta(milliseconds=1)
        st = log.timeline.at(b)
        state = to_display_state(st.snapshot, FeederHealth(b, True), b, cfg)
        assert state.kind == shown, (line, state.kind)
        checked += 1
    assert checked >= 6


def test_nothing_secret_anywhere(e2e):
    """The password, the email, the door key and every token value the fake issued are in
    no console, boot note, pulled log, door answer or file under the root (but the two files
    that hold them)."""
    run, inst, fakes = e2e
    secrets = [PASSWORD, USERNAME, DOOR_KEY.hex()]
    for t in fakes.issued:
        secrets += [v for v in t.values() if v]
    texts = {"console": run.data["console"], "boot": run.data["boot"],
             "door answers": "\n".join(run.door_texts)}
    for p in Path(run.data["logs_pull"]["dir"]).rglob("*"):
        if p.is_file():
            texts[str(p)] = p.read_text(errors="replace")
    for p in inst.root.rglob("*"):
        if p.is_file() and p.name not in ("secrets.json", "tokens.json", "micropython"):
            texts[str(p)] = p.read_bytes().decode("latin-1")
    for p in inst.tmp.rglob("*"):
        if p.is_file():
            texts[str(p)] = p.read_text(errors="replace")
    assert len(texts) > 50
    for name, text in texts.items():
        for s in secrets:
            assert s not in text, f"a secret ({s[:4]}...) in {name}"
    # The launcher was still the same process at the end, and stopped cleanly; with the
    # halt at the end of the run, alive until then, and it ended with boot's exit 3.
    if "halted" in run.data:
        assert run.data["launcher_alive_before_halt"]
        assert run.data["launcher_exit"] == (0 if EMULATED else 3)  # remove, or the halt
    else:
        assert run.data["launcher_alive_at_end"]
        assert run.data["launcher_exit"] == (0 if EMULATED else 143)  # remove, or SIGTERM


def test_memory_and_time(e2e):
    run, _inst, _fakes = e2e
    first, last = run.data["first_status"]["app"], run.data["final_status"]["app"]
    print("memory and time (Mac, MicroPython 64-bit):", json.dumps({
        "heap_after_gc_start": first["heap_after_gc"], "heap_after_gc_end":
            last["heap_after_gc"], "heap_after_gc_max_end_run": last["heap_after_gc_max"],
        "beats": last["beats"], "gc": last["gc"], "late_beats": last["late_beats"],
        "woke_late": last["woke_late"], "worst_late": last["worst_late"]}))
    assert last["heap_after_gc"] < 2_000_000
