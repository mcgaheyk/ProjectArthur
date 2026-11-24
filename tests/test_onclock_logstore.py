"""The log kept on the flash (``arthur.onclock.logstore``, D-0035), and the ring with it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from arthur.onclock import clocklog, logstore
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


def test_after_the_first_line_nothing_reaches_the_flash_until_a_flush(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder)
    log.append(line(0))  # a run's first line goes at once, before any timed flush
    assert log.pending == 0 and log.flushes == 1
    seg = folder / segments(folder)[0]
    assert seg.read_text() == "1 " + json.dumps(line(0)) + "\n"
    for i in range(1, 5):
        log.append(line(i))
    assert log.pending == 4 and len(seg.read_text().splitlines()) == 1
    assert log.flush() is True and log.pending == 0
    assert log.flush() is False  # nothing waiting
    assert seg.read_text().splitlines()[4] == "5 " + json.dumps(line(4))


def test_a_first_line_the_flash_refused_is_not_tried_on_every_append(tmp_path):
    log = new_log(tmp_path / "log")
    calls = []

    def refuse(items):
        calls.append(len(items))
        raise OSError(5, "I/O error")

    log.store.append = refuse
    log.append(line(0))
    for i in range(1, 5):
        log.append(line(i))
    assert calls == [1] and log.store_failures == 1 and log.pending == 4


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
    log.append(line(0))  # the first line, at once
    for i in range(1, 4):
        log.append(line(i))
    assert log.pending == 3 and log.flushes == 1
    log.append(line(4))
    assert log.pending == 0 and log.flushes == 2
    monkeypatch.setattr(clocklog, "FLUSH_BYTES", 100)
    log.append(line(9, 200))
    assert log.pending == 0 and log.flushes == 3


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


def test_a_torn_first_write_is_replaced_not_continued(tmp_path):
    folder = tmp_path / "log"
    log = new_log(folder)
    for i in range(3):
        log.append(line(i))
    log.flush()
    # The next run's first write was cut short: the file its first number names holds a
    # partial line and no newline. The run after that numbers the same and names it again.
    torn = folder / segment_name(3 + 1 + FLUSH_LINES)
    torn.write_text(str(3 + 1 + FLUSH_LINES) + ' {"kind": "console", "i": 7')
    again = new_log(folder)
    assert again.resumed_from == 3
    first = again.append(line(50))
    assert first == 3 + 1 + FLUSH_LINES and segment_name(first) == torn.name
    assert torn.read_text() == f"{first} {json.dumps(line(50))}\n"  # written from empty
    names = [s[1] for s in again.store._segs]
    assert names.count(torn.name) == 1
    assert again.store.size_bytes == sum((folder / n).stat().st_size for n in segments(folder))
    got, nxt = again.read(0, 10**6)
    more, _ = again.read(nxt, 10**6)
    assert [(s, ln.get("i")) for s, ln in got + more] == [(1, 0), (2, 1), (3, 2), (first, 50)]
    assert all(set(ln) == {"kind", "i", "pad"} for _, ln in got + more)


def test_damaged_lines_do_not_stop_the_store_or_a_reader(tmp_path):
    folder = tmp_path / "log"
    folder.mkdir()
    good = [json.dumps(console(m)) for m in ("one", "four")]
    data = (b"1 " + good[0].encode() + b"\n"
            + b'2 {"kind": "console", "msg": "caf\xc3\n'  # cut inside a two-byte character
            + b"3 {not json at all\n"
            + b"4 " + good[1].encode() + b"\n")
    (folder / segment_name(1)).write_bytes(data)
    (folder / "id").write_text("0123456789abcdef\n")
    log = new_log(folder)
    assert log.store is not None and log.resumed_from == 4  # open() worked
    got, nxt = log.read(0, 10**6)
    assert [s for s, _ in got] == [1, 3, 4] and nxt == 4
    assert got[0][1] == console("one") and got[2][1] == console("four")
    assert got[1][1] == {"damaged_line": 3, "text": "{not json at all"}
    assert log.store_status()["damaged"] == 1


def test_a_segment_that_cannot_be_read_counts_as_empty(tmp_path, monkeypatch):
    folder = tmp_path / "log"
    log = new_log(folder)
    for i in range(3):
        log.append(line(i))
    log.flush()
    (folder / segment_name(500)).write_text("500 {}\n")
    real = LogStore._read

    def unreadable(self, name):
        if name == segment_name(500):
            raise OSError(5, "I/O error")
        return real(self, name)

    monkeypatch.setattr(LogStore, "_read", unreadable)
    again = new_log(folder)
    assert again.store is not None and again.resumed_from == 3


class Faulty:
    """A file whose write keeps only ``keep`` bytes (and says so), or raises (``keep`` None)."""

    def __init__(self, f, keep):
        self.f, self.keep = f, keep

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()

    def write(self, data):
        if self.keep is None:
            raise OSError(5, "I/O error")
        return self.f.write(data[:self.keep])


@pytest.mark.parametrize("keep", [10, None], ids=["short write", "raising write"])
def test_a_failed_write_is_counted_and_never_continued(tmp_path, monkeypatch, keep):
    folder = tmp_path / "log"
    log = new_log(folder)
    log.append(line(0))  # the first line, written whole to s1
    log.append(line(1))
    log.append(line(2))

    def faulty_open(path, mode="r"):
        f = open(path, mode)  # noqa: SIM115 - closed by Faulty or the caller
        return Faulty(f, keep) if ("a" in mode or "w" in mode) else f

    monkeypatch.setattr(logstore, "open", faulty_open, raising=False)
    assert log.flush() is False
    monkeypatch.delattr(logstore, "open")
    st = log.store_status()
    assert st["failures"] == 1 and st["error"].startswith("OSError") and st["pending"] == 0
    assert [ln["i"] for _, ln in log.read(0, 10**6)[0]] == [0, 1, 2]  # the ring is whole
    log.append(line(3))
    assert log.flush() is True
    assert segments(folder) == [segment_name(1), segment_name(4)]  # a new segment
    again = new_log(folder)
    got, _ = again.read(0, 10**6)
    assert [(s, ln) for s, ln in got] == [(1, line(0)), (4, line(3))]  # nothing glued


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


MPY_DAMAGE_SCRIPT = r"""
import os, sys
sys.path.insert(0, {repo!r} + "/mpy/lib")
sys.path.insert(0, {repo!r})
from arthur.onclock import logstore
from arthur.onclock.clocklog import ClockLog, FLUSH_LINES
from arthur.onclock.logstore import LogStore, segment_name
root = {root!r}

def files(folder):
    return sorted([n for n in os.listdir(folder) if n.startswith("s")])

# A run's first line is on the flash at once; a run that dies early reuses no number.
f1 = root + "/one"
a = ClockLog(100000, store=LogStore(f1))
sa = [a.append({{"i": i}}) for i in range(5)]
assert files(f1) == [segment_name(1)], files(f1)
b = ClockLog(100000, store=LogStore(f1))
assert b.resumed_from == 1, b.resumed_from
sb = b.append({{"i": 9}})
assert sb == 2 + FLUSH_LINES and sb > max(sa), sb

# A torn first write under the next run's first number: written again from empty.
f2 = root + "/two"
os.mkdir(f2)
first = 1 + 1 + FLUSH_LINES  # the last line on the flash is 1
with open(f2 + "/" + segment_name(1), "wb") as f:
    f.write(b"1 {{}}\n")
with open(f2 + "/" + segment_name(first), "wb") as f:
    f.write(str(first).encode() + b' {{"i": 7')
c = ClockLog(100000, store=LogStore(f2))
assert c.resumed_from == 1
assert c.append({{"i": 50}}) == first
with open(f2 + "/" + segment_name(first), "rb") as f:
    assert f.read() == str(first).encode() + b' {{"i": 50}}\n'
assert [s[1] for s in c.store._segs].count(segment_name(first)) == 1

# A line cut inside a multi-byte character, and one that is not JSON.
f3 = root + "/three"
os.mkdir(f3)
with open(f3 + "/" + segment_name(1), "wb") as f:
    f.write(b'1 {{"m": "caf\xc3\xa9"}}\n2 {{"m": "caf\xc3\n3 {{not json\n4 {{"m": "ok"}}\n')
d = ClockLog(100000, store=LogStore(f3))
assert d.store is not None and d.resumed_from == 4, d.resumed_from
got, nxt = d.read(0, 100000)
assert [q for q, _ in got] == [1, 3, 4], got
assert got[0][1] == {{"m": "caf\u00e9"}}, got[0]
assert got[1][1] == {{"damaged_line": 3, "text": "{{not json"}}, got[1]
assert d.store_status()["damaged"] == 1

# A short write: counted, the ring whole, the next batch in a new segment.
f4 = root + "/four"
e = ClockLog(100000, store=LogStore(f4))
e.append({{"i": 0}})
e.append({{"i": 1}})

class Short:
    def __init__(self, f):
        self.f = f
    def __enter__(self):
        return self
    def __exit__(self, *a):
        self.f.close()
    def write(self, data):
        return self.f.write(data[:3])

logstore.open = lambda path, mode="r": Short(open(path, mode))
assert e.flush() is False
del logstore.open
assert e.store_status()["failures"] == 1
assert [ln["i"] for _, ln in e.read(0, 100000)[0]] == [0, 1]
e.append({{"i": 2}})
assert e.flush() is True
assert files(f4) == [segment_name(1), segment_name(3)], files(f4)
again = ClockLog(100000, store=LogStore(f4))
got, _ = again.read(0, 100000)
assert got == [(1, {{"i": 0}}), (3, {{"i": 2}})], got
print("ok")
"""


@pytest.mark.skipif(MPY is None, reason="micropython is not installed (brew install "
                    "micropython): damaged lines and failed writes were NOT tested as the "
                    "clock runs them")
def test_damage_and_failed_writes_under_micropython(tmp_path):
    script = tmp_path / "drive.py"
    script.write_text(MPY_DAMAGE_SCRIPT.format(repo=str(REPO), root=str(tmp_path)))
    out = subprocess.run([MPY, str(script)], capture_output=True, text=True, timeout=60,
                         check=False, cwd=tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "ok"


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


def test_a_run_that_dies_before_its_first_timed_flush_reuses_no_number(tmp_path):
    from arthur.clocktool import pull_logs

    folder, log_dir = tmp_path / "log", tmp_path / "logs"
    a = new_log(folder)
    a_seqs = [a.append(console(f"a{i}")) for i in range(5)]
    assert ClockLog(10**6, store=LogStore(str(folder))).resumed_from == a_seqs[0]
    pull_logs(FakeDoor(a, "run1"), log_dir)  # the Mac had all of run A from RAM
    del a  # the power went: four lines never written
    b = new_log(folder)
    b_seqs = [b.append(console(f"b{i}")) for i in range(5)]
    assert min(b_seqs) > max(a_seqs)
    pull_logs(FakeDoor(b, "run2"), log_dir)
    assert pulled(log_dir) == [f"a{i}" for i in range(5)] + [f"b{i}" for i in range(5)]


def test_three_runs_that_each_die_early_lose_nothing_for_the_follower(tmp_path):
    from arthur.clocktool import pull_logs

    folder, log_dir = tmp_path / "log", tmp_path / "logs"
    want, seen = [], []
    for run in range(3):
        log = new_log(folder)
        seqs = [log.append(console(f"r{run} {i}")) for i in range(4)]
        assert not seen or min(seqs) > max(seen)
        seen += seqs
        want += [f"r{run} {i}" for i in range(4)]
        pull_logs(FakeDoor(log, f"run{run}"), log_dir)
        assert pulled(log_dir) == want  # no line twice, none skipped
        del log  # dies before its first timed flush


def test_the_follower_gets_past_a_damaged_line(tmp_path):
    from arthur.clocktool import pull_logs

    folder, log_dir = tmp_path / "log", tmp_path / "logs"
    folder.mkdir()
    data = (b"1 " + json.dumps(console("one")).encode() + b"\n"
            + b'2 {"kind": "console", "msg": "caf\xc3\n'
            + b"3 {not json\n"
            + b"4 " + json.dumps(console("four")).encode() + b"\n")
    (folder / segment_name(1)).write_bytes(data)
    (folder / "id").write_text("0123456789abcdef\n")
    log = new_log(folder)
    log.append(console("five"))
    pull_logs(FakeDoor(log, "run1"), log_dir)
    got = pulled(log_dir)
    assert got[0] == "one" and got[2:] == ["four", "five"]
    assert json.loads(got[1]) == {"damaged_line": 3, "text": "{not json"}
