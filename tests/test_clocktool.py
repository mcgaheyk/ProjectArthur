"""The Mac's clock tool (scripts/clock.py, arthur/clocktool.py): push, logs, pause and resume
against a door running here with the stub program; the clock-side helper against a
temporary root; and that no secret is ever printed."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from onclockhelp import DEPLOY, KEY, REPO, DoorThread, make_root

from arthur import clocktool as ct
from arthur.sources.logfile import RobotLog
from arthur.tools import onclock_files as files

pytestmark = pytest.mark.usefixtures("loopback_only")

MPY = shutil.which("micropython")
CROSS = shutil.which("mpy-cross")
FAKE_USER = "made-up-user@example.invalid"
FAKE_PASS = "made-up-password-Zq9"


@pytest.fixture
def key_file(tmp_path):
    p = tmp_path / "door-key"
    p.write_text(KEY.hex() + "\n")
    return p


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def cli(*args: str) -> int:
    return ct.main(list(args))


# -- push ------------------------------------------------------------------------------------


def test_push_sends_a_bundle_then_only_the_config(root, key_file, tmp_path, capsys):
    t = DoorThread(root)
    try:
        clock = f"127.0.0.1:{t.port}"
        assert cli("--clock", clock, "--key-file", str(key_file), "push", "--source",
                   "--no-wait") == 0
        out = capsys.readouterr().out
        assert "push: bundle" in out and "in slot b with config" in out
    finally:
        t.stop()
    state = json.loads((root / "state.json").read_text())
    assert state["active"] == "b"
    assert (root / "config.json").read_bytes() == ct.config_bytes()
    slot_b = root / "slots" / "b"
    want, _ = files.slot_sources(), None
    assert sorted(p.relative_to(slot_b).as_posix() for p in slot_b.rglob("*") if p.is_file()) \
        == sorted([*want, "manifest.json"])
    # The program restarts into b (a new door on the same root), and only the config changes.
    t = DoorThread(root)
    try:
        assert t.door.running == "b"
        toml = tmp_path / "display.toml"
        text = (REPO / "config" / "display.toml").read_text()
        toml.write_text(text.replace("page_pct = 30", "page_pct = 25", 1))
        assert "page_pct = 25" in toml.read_text()
        before = t.app.config_loads
        assert cli("--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file), "push",
                   "--source", "--config", str(toml)) == 0
        out = capsys.readouterr().out
        assert "push: config" in out and "installed and reloaded" in out
        assert t.app.config_loads == before + 1
        assert (root / "config.json").read_bytes() == ct.config_bytes(toml)
        assert cli("--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file), "push",
                   "--source", "--config", str(toml)) == 0
        assert "nothing to do" in capsys.readouterr().out
    finally:
        t.stop()


@pytest.mark.skipif(CROSS is None, reason="mpy-cross is not installed: the compiled bundle "
                    "was NOT tested")
def test_push_compiled_sends_mpy_files(root, key_file):
    t = DoorThread(root)
    try:
        assert cli("--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file), "push",
                   "--no-wait") == 0
    finally:
        t.stop()
    b = root / "slots" / "b"
    assert (b / "arthur" / "render" / "screens.mpy").exists()
    assert not (b / "arthur" / "render" / "screens.py").exists()
    assert (b / "lib" / "typing.py").exists()


def test_push_with_the_wrong_key_fails_plainly(root, tmp_path, capsys):
    bad = tmp_path / "bad-key"
    bad.write_text(bytes(32).hex())
    t = DoorThread(root)
    try:
        assert cli("--clock", f"127.0.0.1:{t.port}", "--key-file", str(bad), "status") == 1
    finally:
        t.stop()
    assert "FAILED: the clock's door: the door refused" in capsys.readouterr().out


def test_pause_resume_restart_and_status(root, key_file, capsys):
    t = DoorThread(root)
    try:
        base = ["--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file)]
        assert cli(*base, "pause") == 0
        assert t.app.paused
        assert cli(*base, "status") == 0
        assert '"paused": true' in capsys.readouterr().out
        assert cli(*base, "resume") == 0
        assert not t.app.paused
        assert cli(*base, "restart") == 0
    finally:
        t.stop()
    assert t.app._code == 42


REFUSED = ("config.json refused (ConfigError: unknown key(s) in [timings]: zz_new); "
           "running on the kept copy config.good.a.json")


def test_config_warning_only_when_the_status_says_so():
    assert ct.config_warning({"app": {"config_error": REFUSED}}) == (
        f"WARNING: the clock is not using the config on its flash ({ct.ROOT}/config.json); "
        f"it runs on a kept copy: {REFUSED}")
    # An older program, without the field; a good one; a status without an app.
    for st in ({"app": {"paused": False}}, {"app": {"config_error": None}}, {}, None):
        assert ct.config_warning(st) is None


def test_status_and_push_warn_when_the_clock_runs_on_a_kept_copy(root, key_file, capsys):
    t = DoorThread(root)
    try:
        base = ["--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file)]
        assert cli(*base, "status") == 0  # the stub's status has no such field
        assert "WARNING" not in capsys.readouterr().out
        plain = t.app.status
        t.app.status = lambda: dict(plain(), config_error=REFUSED)
        assert cli(*base, "status") == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln.startswith("WARNING")]
        assert len(lines) == 1 and REFUSED in lines[0] and "kept copy" in lines[0]
        assert json.JSONDecoder().raw_decode(out)[0]["app"]["config_error"] == REFUSED
        assert cli(*base, "push", "--source", "--no-wait") == 0
        out = capsys.readouterr().out
        assert out.count("WARNING: the clock is not using the config on its flash") == 1
        assert "push: bundle" in out  # what push sends is unchanged
    finally:
        t.stop()


# -- logs ------------------------------------------------------------------------------------

STATE_LINE = json.loads((REPO / "tests" / "data" / "real-cycles.jsonl").read_text()
                        .splitlines()[0])


def test_logs_pull_writes_the_feeders_formats(root, key_file, tmp_path, capsys):
    t = DoorThread(root)
    log_dir = tmp_path / "logs"
    try:
        t.app.log.append(dict(STATE_LINE))
        t.app.log.append({"fetched": "2026-11-16T04:30:00+00:00", "kind": "activity",
                          "timestamp": "2026-11-16T04:22:00+00:00", "action": "Cat Detected"})
        t.app.log.append({"kind": "console", "ts": "2026-11-16T04:18:17.000Z", "level": "INFO",
                          "name": "arthur.onclock", "msg": "Whisker connected"})
        base = ["--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file)]
        assert cli(*base, "logs", "pull", "--log-dir", str(log_dir)) == 0
        assert "1 robot lines, 1 history lines" in capsys.readouterr().out
        assert cli(*base, "logs", "pull", "--log-dir", str(log_dir)) == 0  # nothing twice
        t.app.log.append({"kind": "console", "ts": "2026-11-16T04:19:00.000Z",
                          "level": "WARNING", "name": "arthur.onclock", "msg": "second"})
        assert cli(*base, "logs", "pull", "--log-dir", str(log_dir)) == 0
    finally:
        t.stop()
    spike = sorted(log_dir.glob("spike-2*.jsonl"))
    assert len(spike) == 1
    assert [json.loads(x) for x in spike[0].read_text().splitlines()] == [STATE_LINE]
    assert len(list(log_dir.glob("spike-activity-*.jsonl"))) == 1
    console = (log_dir / "feeder-console.log").read_text().splitlines()
    assert [c.split(" ", 2)[2] for c in console if "arthur.onclock" in c] == [
        "INFO arthur.onclock: config loaded",  # the stub's own first line
        "INFO arthur.onclock: Whisker connected", "WARNING arthur.onclock: second"]
    assert console[0][:4] == "2026" and console[0][19] == ","  # asctime, as the feeder's
    # The studio's reader takes the pulled log as it takes the feeder's.
    log = RobotLog(log_dir)
    log.refresh()
    assert len(log.timeline.lines) >= 1


def test_logs_start_again_after_the_program_restarts(root, key_file, tmp_path):
    log_dir = tmp_path / "logs"
    t = DoorThread(root)
    try:
        t.app.log.append({"kind": "console", "msg": "first run"})
        with ct.connect(ct.build_parser().parse_args(
                ["--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file), "status"])) as c:
            ct.pull_logs(c, log_dir)
    finally:
        t.stop()
    t = DoorThread(root)  # a new run: its log numbers start again
    try:
        t.app.log.append({"kind": "console", "msg": "second run"})
        with ct.connect(ct.build_parser().parse_args(
                ["--clock", f"127.0.0.1:{t.port}", "--key-file", str(key_file), "status"])) as c:
            counts = ct.pull_logs(c, log_dir)
    finally:
        t.stop()
    assert counts["console"] == 3  # "config loaded", "door open" and "second run"
    assert "second run" in (log_dir / "feeder-console.log").read_text()


# -- secrets ---------------------------------------------------------------------------------


def test_install_dry_run_never_shows_a_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHISKER_USERNAME", FAKE_USER)
    monkeypatch.setenv("WHISKER_PASSWORD", FAKE_PASS)
    key = tmp_path / "key"
    assert cli("--serial", "TESTSERIAL", "--dry-run", "--key-file", str(key), "install",
               "--source") == 0
    out = capsys.readouterr().out
    assert FAKE_USER not in out and FAKE_PASS not in out
    assert "secrets.json" in out and "not shown" in out
    assert "adb -s TESTSERIAL push" in out
    assert not key.exists()  # a dry run writes nothing, on the Mac either


def test_install_without_the_login_names_only_the_variables(monkeypatch, capsys):
    monkeypatch.delenv("WHISKER_USERNAME", raising=False)
    monkeypatch.setenv("WHISKER_PASSWORD", FAKE_PASS)
    assert cli("--serial", "X", "--dry-run", "install") == 1
    out = capsys.readouterr().out
    assert "WHISKER_USERNAME" in out and FAKE_PASS not in out


def test_usb_commands_need_a_serial(capsys):
    with pytest.raises(SystemExit):
        cli("install")
    assert "--serial" in capsys.readouterr().err


class Recorder(ct.Device):
    """Records what would be sent, and the pushed files' modes and contents at that moment."""

    def __init__(self) -> None:
        super().__init__("S", False, False)
        self.calls: list[tuple[str, ...]] = []
        self.pushed: dict[str, tuple[int, str]] = {}

    def run(self, kind: str, *a: str, show: bool = True) -> str:
        self.calls.append((kind, *a))
        if kind == "push":
            p = Path(a[0])
            self.pushed[a[1]] = (stat.S_IMODE(p.stat().st_mode), p.read_text())
        return ""


def test_secrets_are_staged_with_mode_600_and_removed(capsys):
    dev = Recorder()
    ct._push_secrets(dev, {"whisker_username": FAKE_USER, "whisker_password": FAKE_PASS,
                           "door_key": KEY.hex()})
    mode, text = dev.pushed[ct.SECRETS_TMP]
    assert mode == 0o600 and json.loads(text)["whisker_password"] == FAKE_PASS
    local = Path(next(c for c in dev.calls if c[0] == "push")[1])
    assert not local.exists() and not local.parent.exists()
    shell = [c[1] for c in dev.calls if c[0] == "shell"]
    want = (f"umask 077; cp {ct.SECRETS_TMP} {ct.ROOT}/secrets.json && chmod 600 "
            f"{ct.ROOT}/secrets.json; rm -f {ct.SECRETS_TMP}")
    assert shell == [want]
    out = capsys.readouterr().out
    assert FAKE_PASS not in out and FAKE_USER not in out and KEY.hex() not in out


def test_the_key_file_is_private(tmp_path):
    p = tmp_path / "k"
    ct.write_key(KEY, p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert ct.read_key(p) == KEY


def test_the_key_file_is_ignored_by_git():
    out = subprocess.run(["git", "check-ignore", ".clock-door-key",
                          "deploy/clock/micropython/dist/micropython-armv7"],
                         cwd=REPO, capture_output=True, text=True, check=False).stdout
    assert ".clock-door-key" in out and "micropython-armv7" in out


# -- the launcher's settings -------------------------------------------------------------------


def test_render_autostart_fills_in_three_lines_only():
    text = ct.render_autostart("/x/arthur", "/tmp/a", "2M")
    src = (DEPLOY / "autostart.sh").read_text().splitlines()
    out = text.splitlines()
    assert len(src) == len(out)
    changed = [(a, b) for a, b in zip(src, out, strict=True) if a != b]
    assert [b for _, b in changed] == ["ROOT=/x/arthur", "TMP=/tmp/a", "HEAP=2M"]
    with pytest.raises(ct.ToolError):
        ct.render_autostart("/x; rm -rf /", "/tmp/a", "2M")
    with pytest.raises(ct.ToolError):
        ct.render_autostart("/x/arthur", "/tmp/a", "2G")


# -- the clock-side helper -----------------------------------------------------------------------

PYTHONS = [pytest.param([sys.executable], id="cpython"),
           pytest.param([MPY or "micropython", "-X", "heapsize=512K"], id="micropython",
                        marks=pytest.mark.skipif(MPY is None, reason="micropython is not "
                                                 "installed: clockctl.py was NOT tested as "
                                                 "the clock runs it"))]


def ctl(py: list[str], *args: str) -> dict[str, str]:
    out = subprocess.run([*py, str(DEPLOY / "clockctl.py"), *args], capture_output=True,
                         text=True, check=False).stdout
    pairs = dict(line[2:].split("=", 1) for line in out.splitlines() if line.startswith("R "))
    pairs["_out"] = out
    return pairs


@pytest.mark.parametrize("py", PYTHONS)
def test_clockctl_install_status_and_removal(py, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    root = state / "arthur"
    auto = state / "autostart"
    r = ctl(py, "check", str(root), str(auto))
    assert r["autostart"] == "absent" and r["root"] == "absent" and int(r["data_free_bytes"]) > 0
    # A stage as install makes it.
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "micropython").write_bytes(b"\x7fELF fake")
    (stage / "boot.py").write_text("# boot")
    (stage / "config.json").write_text("{}")
    fill = make_root(tmp_path / "src") / "slots" / "a"
    shutil.copytree(fill, stage / "slot")
    import hashlib

    (stage / "expect.json").write_text(json.dumps(
        {n: hashlib.sha256((stage / n).read_bytes()).hexdigest()
         for n in ("micropython", "boot.py", "config.json")}))
    r = ctl(py, "place", str(stage), str(root))
    assert r.get("done") == "1", r["_out"]
    assert (root / "slots" / "a" / "manifest.json").exists() and (root / "slots" / "b").is_dir()
    assert json.loads((root / "state.json").read_text())["active"] == "a"
    assert ctl(py, "place", str(stage), str(root)).get("error", "").startswith("already")
    (root / "secrets.json").write_text(json.dumps({"whisker_password": FAKE_PASS}))
    os.chmod(root / "secrets.json", 0o600)
    auto.write_text(ct.render_autostart())
    (root / "config.good.a.json").write_text("{}")  # the program's kept copy of the config
    r = ctl(py, "status", str(root), str(auto))
    assert r["autostart"] == "ours" and r["file.secrets.json"] == "present, mode 0o600"
    assert r["file.config.good.a.json"] == "2" and r["file.config.good.b.json"] == "absent"
    assert r["slot.b.bundle"] == "none" and len(r["slot.a.bundle"]) == 16
    assert FAKE_PASS not in r["_out"]
    # Removal: our autostart goes, a foreign one never does.
    r = ctl(py, "unhook", str(auto))
    assert r["deleted"] == str(auto) and not auto.exists()
    auto.write_text("#!/bin/sh\nexec someone-elses-program\n")
    assert ctl(py, "unhook", str(auto))["autostart"] == "foreign" and auto.exists()
    assert ctl(py, "wipe", str(root))["root"] == "absent"
    assert not root.exists() and sorted(p.name for p in state.iterdir()) == ["autostart"]
    assert ctl(py, "wipe", str(state)).get("error", "").startswith("refusing")
    assert state.exists()
