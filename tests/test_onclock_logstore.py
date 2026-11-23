"""The log kept on the flash (``arthur.onclock.logstore``, D-0035), and the ring with it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from arthur.onclock import clocklog
from arthur.onclock.clocklog import FLUSH_LINES, ClockLog
from arthur.onclock.logstore import LogStore, segment_first, segment_name

REPO = Path(__file__).resolve().parent.parent
MPY = shutil.which("micropython")


def line(i: int, pad: int = 0) -> dict:
    return {"kind": "console", "i": i, "pad": "x" * pad}


def new_log(folder: Path, **kw) -> ClockLog:
    return ClockLog(10**6, store=LogStore(str(folder), **kw))


def segments(folder: Path) -> list[str]:
    return sorted(n for n in os.listdir(folder) if segment_first(n) is not None)


def test_segment_names_sort_by_number():
    assert segment_name(7) == "s0000000007.jsonl"
    assert segment_first("s0000000007.jsonl") == 7
    assert segment_first("id") is None and segment_first("sX.jsonl") is None
    assert sorted([segment_name(10), segment_name(9)]) == [segment_name(9), segment_name(10)]


def test_nothing_reaches_the_flash_until_a_flush(tmp_path):
    log = new_log(tmp_path / "log")
    for i in range(5):
        log.append(line(i))
    assert log.pending == 5 and segments(tmp_path / "log") == []
    assert log.flush() is True and log.pending == 0
    assert log.flush() is False  # nothing waiting
    data = (tmp_path / "log" / segments(tmp_path / "log")[0]).read_text()
    assert data.splitlines()[0] == "1 " + json.dumps(line(0))


def test_a_restart_carries_the_numbering_on_and_reads_the_old_lines(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder)
    for i in range(10):
        log.append(line(i))
    log.flush()
    log.append(line(10))  # handed out (a reader may have seen it) but never written
    old_id = log.log_id
    assert old_id and len(old_id) == 16

    again = new_log(folder)
    assert again.log_id == old_id and again.resumed_from == 10
    first_new = again.append(line(100))
    assert first_new == 10 + 1 + FLUSH_LINES  # past anything the last run handed out
    got, nxt = again.read(0, 10**6)  # the old lines, from the flash
    assert [s for s, _ in got] == list(range(1, 11)) and nxt == 10
    got, nxt = again.read(nxt, 10**6)  # then the new run's, from RAM
    assert [s for s, _ in got] == [first_new] and got[0][1]["i"] == 100
    # A reader that saw line 11 of the last run (never written) carries on with no skip.
    got, _ = again.read(11, 10**6)
    assert [s for s, _ in got] == [first_new]


def test_a_reader_ahead_of_everything_starts_from_the_oldest_on_the_flash(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder)
    for i in range(3):
        log.append(line(i))
    log.flush()
    again = new_log(folder)
    got, _ = again.read(10**6, 10**6)
    assert [s for s, _ in got] == [1, 2, 3]


def test_flushes_at_once_past_the_line_or_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(clocklog, "FLUSH_LINES", 4)
    log = new_log(tmp_path / "log")
    for i in range(4):
        log.append(line(i))
    assert log.pending == 0 and log.flushes == 1
    monkeypatch.setattr(clocklog, "FLUSH_BYTES", 100)
    log.append(line(9, 200))
    assert log.pending == 0 and log.flushes == 2


def test_segments_roll_over_and_the_oldest_go_past_the_cap(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder, max_bytes=3000, segment_bytes=1000)
    for i in range(60):
        log.append(line(i, 40))
        if i % 5 == 4:
            log.flush()
    segs = segments(folder)
    assert len(segs) >= 2
    assert log.store.size_bytes <= 3000 + 1000
    assert sum((folder / s).stat().st_size for s in segs) == log.store.size_bytes
    first = log.store.first_seq
    assert first > 1  # the oldest were removed
    # A reader that fell behind what the flash keeps gets the oldest left, then on.
    again = new_log(folder, max_bytes=3000, segment_bytes=1000)
    got, _ = again.read(0, 10**6)
    assert got[0][0] == first and [s for s, _ in got] == list(range(first, 61))


def test_a_line_cut_short_by_a_power_loss_is_never_read(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder)
    for i in range(3):
        log.append(line(i))
    log.flush()
    seg = folder / segments(folder)[0]
    with open(seg, "a") as f:
        f.write('4 {"kind": "console", "i": 3')  # no newline: the power went
    again = new_log(folder)
    assert again.resumed_from == 3
    again.append(line(50))
    again.flush()
    assert len(segments(folder)) == 2  # a fresh segment, not glued to the cut line
    got, nxt = again.read(0, 10**6)  # the flash, up to the ring
    more, _ = again.read(nxt, 10**6)  # then the ring
    assert [ln["i"] for _, ln in got + more] == [0, 1, 2, 50]


def test_read_from_the_flash_stops_at_max_bytes_and_at_the_ring(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder)
    for i in range(10):
        log.append(line(i, 100))
    log.flush()
    again = new_log(folder)
    one = len(json.dumps(line(0, 100)))
    got, nxt = again.read(0, one * 3)
    assert [s for s, _ in got] == [1, 2, 3] and nxt == 3


def test_a_flash_that_refuses_loses_the_batch_but_not_the_ring(tmp_path):
    log = new_log(tmp_path / "log")

    def refuse(items):
        raise OSError(28, "No space left on device")

    log.store.append = refuse
    log.append(line(0))
    assert log.flush() is False
    st = log.store_status()
    assert st["failures"] == 1 and "No space" in st["error"] and st["pending"] == 0
    got, _ = log.read(0, 10**6)
    assert [ln["i"] for _, ln in got] == [0]


def test_a_flash_that_will_not_open_leaves_the_log_in_ram(tmp_path):
    blocker = tmp_path / "log"
    blocker.write_text("a file where the folder should be")
    log = ClockLog(10**6, store=LogStore(str(blocker)))
    assert log.store is None and log.log_id is None
    assert log.append(line(0)) == 1
    assert log.store_status()["error"]


def test_without_a_store_nothing_changes():
    log = ClockLog(10_000)
    assert log.log_id is None and log.store_status() is None and log.flush() is False
    assert log.append(line(0)) == 1


MPY_SCRIPT = """
import sys
sys.path.insert(0, {repo!r} + "/mpy/lib")
sys.path.insert(0, {repo!r})
from arthur.onclock.clocklog import ClockLog, FLUSH_LINES
from arthur.onclock.logstore import LogStore
folder = {folder!r}
log = ClockLog(100000, store=LogStore(folder, max_bytes=4000, segment_bytes=1000))
for i in range(40):
    log.append({{"kind": "console", "i": i, "pad": "x" * 100}})
    if i % 7 == 6:
        log.flush()
log.flush()
again = ClockLog(100000, store=LogStore(folder, max_bytes=4000, segment_bytes=1000))
assert again.log_id == log.log_id
assert again.resumed_from == 40, again.resumed_from
s = again.append({{"kind": "console", "i": 99}})
assert s == 41 + FLUSH_LINES, s
got, nxt = again.read(0, 100000)
seqs = [q for q, _ in got]
assert seqs == list(range(seqs[0], 41)), seqs
assert seqs[0] > 1
got, nxt = again.read(nxt, 100000)
assert [q for q, _ in got] == [s]
again.flush()
print("ok", seqs[0], again.store_status()["bytes"])
"""


@pytest.mark.skipif(MPY is None, reason="micropython is not installed (brew install "
                    "micropython): the log store was NOT tested as the clock runs it")
def test_the_store_under_micropython(tmp_path):
    folder = tmp_path / "log"
    script = tmp_path / "drive.py"
    script.write_text(MPY_SCRIPT.format(repo=str(REPO), folder=str(folder)))
    out = subprocess.run([MPY, str(script)], capture_output=True, text=True, timeout=60, check=False,
                         cwd=tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.startswith("ok ")
    # What MicroPython wrote, CPython reads the same.
    log = ClockLog(10**6, store=LogStore(str(folder), max_bytes=4000, segment_bytes=1000))
    got, _ = log.read(0, 10**6)
    assert got[-1][1] == {"kind": "console", "i": 99}


# -- the Mac's follower ------------------------------------------------------------------------


class FakeDoor:
    """``logs`` as the door answers it, over a ClockLog, with a run id per start."""

    def __init__(self, log: ClockLog, run: str) -> None:
        self.log, self.run = log, run

    def logs(self, after: int) -> dict:
        lines, nxt = self.log.read(after, 24576)
        return {"ok": True, "run": self.run, "log": self.log.log_id,
                "lines": [[q, ln] for q, ln in lines], "next": nxt}


def console(msg: str) -> dict:
    return {"kind": "console", "ts": "2026-11-23T12:00:00.000Z", "level": "info", "msg": msg}


def pulled(log_dir: Path) -> list[str]:
    path = log_dir / "feeder-console.log"
    return [ln.split("arthur.clock: ", 1)[1] for ln in path.read_text().splitlines()]


def test_the_follower_keeps_its_place_across_a_restart(tmp_path):
    from arthur.clocktool import pull_logs

    folder, log_dir = tmp_path / "log", tmp_path / "logs"
    log = new_log(folder)
    log.append(console("one"))
    log.append(console("two"))
    log.flush()
    pull_logs(FakeDoor(log, "run1"), log_dir)
    log.append(console("three, while the Mac slept"))
    log.flush()
    log.append(console("four, never written"))  # the power went
    pull_logs(FakeDoor(log, "run1"), log_dir)  # the Mac saw it live

    again = new_log(folder)  # a new run on the same flash log
    again.append(console("five"))
    pull_logs(FakeDoor(again, "run2"), log_dir)
    assert pulled(log_dir) == ["one", "two", "three, while the Mac slept",
                               "four, never written", "five"]  # nothing twice, nothing lost


def test_the_follower_catches_up_from_the_flash_after_a_restart(tmp_path):
    from arthur.clocktool import pull_logs

    folder, log_dir = tmp_path / "log", tmp_path / "logs"
    log = new_log(folder)
    log.append(console("seen"))
    pull_logs(FakeDoor(log, "run1"), log_dir)
    log.append(console("missed while the Mac slept"))
    log.flush()
    again = new_log(folder)  # restarted before the Mac came back
    again.append(console("after"))
    pull_logs(FakeDoor(again, "run2"), log_dir)
    assert pulled(log_dir) == ["seen", "missed while the Mac slept", "after"]


def test_the_follower_starts_again_on_a_new_log(tmp_path):
    from arthur.clocktool import pull_logs

    log_dir = tmp_path / "logs"
    old = ClockLog(10**6)  # the program before D-0035: no log on the flash
    for i in range(5):
        old.append(console(f"old {i}"))
    pull_logs(FakeDoor(old, "run1"), log_dir)
    new = new_log(tmp_path / "log")  # the first run with it: numbering from 1 again
    new.append(console("new"))
    pull_logs(FakeDoor(new, "run2"), log_dir)
    assert pulled(log_dir)[-1] == "new"
    assert json.loads((log_dir / "clock-logs.json").read_text())["log"] == new.log_id
