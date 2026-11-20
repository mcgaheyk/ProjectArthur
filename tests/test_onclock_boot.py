"""Starting up on the clock: deploy/clock/boot.py (stage 0) and deploy/clock/autostart.sh
(the launcher), against a temporary install root.

boot.py runs under CPython and under the ``micropython`` binary (as on the clock), with the
stub program in its slots. The launcher runs under ``sh``, against a fake ``micropython``
where that is the cheapest proof, and once against the real one with the real boot.py and
door: a bundle sent through the door switches the slot and the launcher starts it again.
Without ``micropython`` on PATH the MicroPython runs are skipped, saying so.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from onclockhelp import DEPLOY, KEY, fill_slot, free_port, make_root

from arthur.clocktool import render_autostart
from arthur.onclock import door as d
from arthur.tools.doorclient import DoorClient

pytestmark = pytest.mark.usefixtures("loopback_only")

MPY = shutil.which("micropython")
no_mpy = pytest.mark.skipif(MPY is None, reason="micropython is not installed (brew install "
                            "micropython): boot.py was NOT tested as the clock runs it")
PYTHONS = [pytest.param("cpython", id="cpython"), pytest.param("micropython", id="micropython",
                                                                marks=no_mpy)]

DRIVER = """
import sys
sys.path.insert(0, {deploy!r})
import boot
sys.exit(boot.Boot({root!r}, {tmp!r}, {port}, healthy_after={healthy}, probe_every=0.1,
                   crash_pause=0).run())
"""


class Clock:
    """A temporary install with both slots holding the stub program, and a RAM folder."""

    def __init__(self, tmp_path: Path, py: str) -> None:
        self.root = make_root(tmp_path)
        fill_slot(self.root / "slots" / "b", {"arthur/b.txt": b"slot b"})
        self.tmp = tmp_path / "ram"
        self.port = free_port()
        (self.root / "test-port").write_text(str(self.port))
        self.py = [sys.executable] if py == "cpython" else [MPY, "-X", "heapsize=2M"]
        self.driver = tmp_path / "driver.py"
        self.healthy = 0.3

    def mode(self, slot: str, mode: str) -> None:
        (self.root / f"test-mode-{slot}").write_text(mode)

    def start(self) -> subprocess.Popen:
        self.driver.write_text(DRIVER.format(deploy=str(DEPLOY), root=str(self.root),
                                             tmp=str(self.tmp), port=self.port,
                                             healthy=self.healthy))
        return subprocess.Popen([*self.py, str(self.driver), str(self.root)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def run(self, timeout: float = 30) -> int:
        p = self.start()
        try:
            return p.wait(timeout)
        finally:
            if p.poll() is None:
                p.kill()

    def state(self) -> dict:
        return json.loads((self.root / "state.json").read_text())

    def notes(self) -> str:
        try:
            return (self.tmp / "boot.txt").read_text()
        except OSError:
            return ""

    def wait_note(self, text: str, timeout: float = 20) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if text in self.notes():
                return
            time.sleep(0.05)
        raise AssertionError(f"no {text!r} in boot.txt:\n{self.notes()}")

    def client(self, timeout: float = 20) -> DoorClient:
        end = time.monotonic() + timeout
        while True:
            try:
                return DoorClient("127.0.0.1", self.port, KEY, timeout=10)
            except OSError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.05)

    def run_healthy_then_restart(self, slot: str) -> int:
        """Start, wait until boot calls the slot healthy, then restart it from the 'Mac'."""
        p = self.start()
        try:
            self.wait_note(f"slot {slot} is healthy")
            with self.client() as c:
                assert c.status()["running"] == slot
                c.restart()
            return p.wait(20)
        finally:
            if p.poll() is None:
                p.kill()


@pytest.fixture(params=PYTHONS)
def clock(request, tmp_path):
    return Clock(tmp_path, request.param)


def test_the_port_and_greeting_match_the_door():
    sys.path.insert(0, str(DEPLOY))
    try:
        import boot
    finally:
        sys.path.remove(str(DEPLOY))
    assert boot.DOOR_PORT == d.DOOR_PORT
    assert boot.GREETING == d.GREETING
    assert boot.RESTART == 42


def test_a_healthy_start(clock):
    assert clock.run_healthy_then_restart("a") == 42
    st = clock.state()
    assert st["active"] == "a" and st["tries"] == {"a": 0, "b": 0}
    assert "starting slot a, try 1" in clock.notes()


def test_the_count_is_reset_after_health(clock):
    st = clock.state()
    st["tries"]["a"] = 2
    (clock.root / "state.json").write_text(json.dumps(st))
    assert clock.run_healthy_then_restart("a") == 42
    assert "try 3" in clock.notes()
    assert clock.state()["tries"]["a"] == 0


def test_a_slot_that_raises_on_import_falls_back_to_the_other(clock):
    clock.mode("a", "raise-import")
    for n in (1, 2, 3):
        assert clock.run() == 42
        st = clock.state()
        assert st["active"] == "a" and st["tries"]["a"] == n
        assert st["errors"]["a"].startswith("import: RuntimeError")
    assert clock.run_healthy_then_restart("b") == 42
    st = clock.state()
    assert st["active"] == "b" and st["tries"]["b"] == 0 and st["tries"]["a"] == 3
    assert "falling back from slot a to slot b" in clock.notes()


def test_a_slot_that_dies_young_is_counted(clock):
    clock.mode("a", "die-young")
    assert clock.run() == 42
    st = clock.state()
    assert st["tries"]["a"] == 1
    assert st["errors"]["a"] == "run: RuntimeError: died young on purpose"
    assert "healthy" not in clock.notes()


def test_without_its_door_a_program_is_never_healthy(clock):
    clock.mode("a", "no-door")
    p = clock.start()
    try:
        time.sleep(clock.healthy + 2.0)
        assert p.poll() is None  # still running: it is not killed for that
        assert clock.state()["tries"]["a"] == 1
        assert "healthy" not in clock.notes()
    finally:
        p.kill()
        p.wait()


def test_both_slots_bad_ends_without_spinning(clock):
    clock.mode("a", "raise-import")
    clock.mode("b", "raise-import")
    codes = [clock.run() for _ in range(6)]
    assert codes == [42] * 6
    assert clock.run() == 3  # HALT: the daemon's back-off ends it
    st = clock.state()
    assert st["halted"] and st["tries"] == {"a": 3, "b": 3}
    assert st["errors"]["a"].startswith("import:") and st["errors"]["b"].startswith("import:")
    before = (clock.root / "state.json").read_bytes()
    mtime = (clock.root / "state.json").stat().st_mtime_ns
    assert clock.run() == 3  # the daemon tries again: no write to the flash
    assert (clock.root / "state.json").read_bytes() == before
    assert (clock.root / "state.json").stat().st_mtime_ns == mtime
    # After a power-on (/tmp is empty again) both slots get another round.
    shutil.rmtree(clock.tmp)
    assert clock.run() == 42
    st = clock.state()
    assert "halted" not in st and st["tries"]["b"] == 1


def test_a_slot_whose_files_changed_is_not_started(clock):
    with open(clock.root / "slots" / "a" / "arthur" / "onclock" / "main.py", "a") as f:
        f.write("\n# changed on the flash\n")
    assert clock.run_healthy_then_restart("b") == 42
    st = clock.state()
    assert st["active"] == "b"
    assert st["errors"]["a"] == "not whole: changed arthur/onclock/main.py"


def test_an_empty_other_slot_is_no_fall_back(clock):
    clock.mode("a", "raise-import")
    shutil.rmtree(clock.root / "slots" / "b")
    assert [clock.run() for _ in range(3)] == [42] * 3
    assert clock.run() == 3
    assert clock.state()["errors"]["b"] == "not whole: no manifest"


# -- the launcher ------------------------------------------------------------------------------

FAKE = """#!/bin/sh
root="$4"
n=$(cat "$root/count" 2>/dev/null || echo 0)
n=$((n + 1))
echo $n > "$root/count"
echo "run $n: $*"
echo $$ > "$root/pid"
code=$(sed -n "${n}p" "$root/codes")
if [ "$code" = "hang" ]; then exec sleep 30; fi
exit ${code:-0}
"""


def launcher(tmp_path: Path, codes: list[str]) -> tuple[Path, Path, Path]:
    root = tmp_path / "arthur"
    root.mkdir()
    ram = tmp_path / "ram"
    (root / "micropython").write_text(FAKE)
    (root / "micropython").chmod(0o755)
    (root / "codes").write_text("\n".join(codes) + "\n")
    script = tmp_path / "autostart"
    script.write_text(render_autostart(str(root), str(ram), "3M"))
    script.chmod(0o700)
    return root, ram, script


def test_the_launcher_restarts_at_once_on_42_and_passes_anything_else(tmp_path):
    root, ram, script = launcher(tmp_path, ["42", "42", "7"])
    p = subprocess.run(["sh", str(script)], env={"PATH": "/bin:/usr/bin"}, timeout=20,
                       check=False)
    assert p.returncode == 7
    assert (root / "count").read_text().strip() == "3"
    console = (ram / "console.log").read_text().splitlines()
    assert console == [f"run {n}: -X heapsize=3M {root}/boot.py {root} {ram}" for n in (1, 2, 3)]


def test_the_launcher_ends_on_0(tmp_path):
    root, _, script = launcher(tmp_path, ["0"])
    assert subprocess.run(["sh", str(script)], timeout=20, check=False).returncode == 0
    assert (root / "count").read_text().strip() == "1"


def test_the_launcher_keeps_the_last_console(tmp_path):
    _, ram, script = launcher(tmp_path, ["5", "6"])
    subprocess.run(["sh", str(script)], timeout=20, check=False)
    subprocess.run(["sh", str(script)], timeout=20, check=False)
    assert "run 1:" in (ram / "console.prev.log").read_text()
    assert "run 2:" in (ram / "console.log").read_text()


def test_stopping_the_launcher_stops_the_program(tmp_path):
    root, _, script = launcher(tmp_path, ["hang"])
    p = subprocess.Popen(["sh", str(script)])
    end = time.monotonic() + 10
    while not (root / "pid").exists() and time.monotonic() < end:
        time.sleep(0.05)
    time.sleep(0.2)
    child = int((root / "pid").read_text())
    os.kill(child, 0)  # running
    p.send_signal(signal.SIGTERM)
    assert p.wait(10) == 143
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child, signal.SIGKILL)
        raise AssertionError("the program outlived its launcher")


def test_the_launcher_uses_only_what_the_clock_has():
    """The clock's shell has no sleep, grep, head or tr; the launcher may use builtins and
    mkdir and cp only (and the program it starts)."""
    text = (DEPLOY / "autostart.sh").read_text()
    body = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    words = {w for ln in body for w in ln.replace(";", " ").split()}
    for tool in ("sleep", "grep", "head", "tr", "sed", "awk", "mv", "timeout", "env"):
        assert tool not in words, tool
    assert text.startswith("#!/bin/sh\n# ARTHUR-AUTOSTART v1")


# -- the whole chain on MicroPython: launcher, boot, door, a bundle, a restart ----------------


@no_mpy
def test_a_bundle_through_the_door_restarts_into_the_new_slot(tmp_path):
    root = make_root(tmp_path)
    port = free_port()
    (root / "test-port").write_text(str(port))
    os.symlink(MPY, root / "micropython")
    shutil.copyfile(DEPLOY / "boot.py", root / "boot.py")
    ram = tmp_path / "ram"
    script = tmp_path / "autostart"
    script.write_text(render_autostart(str(root), str(ram), "2M"))
    p = subprocess.Popen(["sh", str(script)], start_new_session=True)
    try:
        def connect() -> DoorClient:
            end = time.monotonic() + 30
            while True:
                try:
                    return DoorClient("127.0.0.1", port, KEY, timeout=10)
                except OSError:
                    if time.monotonic() > end:
                        raise
                    time.sleep(0.05)

        with connect() as c:
            assert c.status()["running"] == "a"
            t0 = time.monotonic()
            c.config_put(b'{"colour": "orange"}')
            assert c.status()["app"]["config_loads"] == 2
            config_s = time.monotonic() - t0
        new = tmp_path / "new"
        manifest = fill_slot(new, {"arthur/version.txt": b"two"})
        t0 = time.monotonic()
        with connect() as c:
            res = c.bundle(new, manifest)
        while True:
            try:
                with connect() as c:
                    st = c.status()
                if st["running"] == "b":
                    break
            except OSError:
                pass
            time.sleep(0.05)
        code_s = time.monotonic() - t0
        assert st["slots"]["b"] == res["bundle"] and st["active"] == "b"
        assert p.poll() is None  # the launcher restarted it; the daemon never saw an exit
        with connect() as c:
            c.restart()
        time.sleep(0.5)
        with connect() as c:
            assert c.status()["running"] == "b"
        print(f"\nlocal timings: config {config_s:.3f} s, code {code_s:.3f} s")
        assert config_s < 5 and code_s < 30
    finally:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(10)
    notes = (ram / "boot.txt").read_text()
    assert "starting slot a, try 1" in notes and "starting slot b, try 1" in notes
    assert "Traceback" not in (ram / "console.log").read_text()
