"""The log ring kept in memory on the clock (``arthur.onclock.clocklog.ClockLog``)."""

from __future__ import annotations

import json

from arthur.onclock.clocklog import ClockLog


def line(i: int, pad: int = 0) -> dict:
    return {"kind": "console", "i": i, "pad": "x" * pad}


def size(i: int, pad: int = 0) -> int:
    return len(json.dumps(line(i, pad)))


def test_sequence_numbers_count_from_one_and_read_after():
    log = ClockLog(10_000)
    assert (log.first_seq, log.last_seq, len(log)) == (1, 0, 0)
    assert log.read(0, 10_000) == ([], 0)
    seqs = [log.append(line(i)) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    got, nxt = log.read(0, 10_000)
    assert [s for s, _ in got] == seqs and nxt == 5
    assert [ln["i"] for _, ln in got] == list(range(5))
    got, nxt = log.read(3, 10_000)
    assert [s for s, _ in got] == [4, 5] and nxt == 5
    assert log.read(5, 10_000) == ([], 5)  # nothing new: ask again with the same number


def test_oldest_lines_dropped_first_under_the_byte_cap():
    one = size(0, 50)
    log = ClockLog(one * 3 + 5)
    for i in range(10):
        log.append(line(i, 50))
    assert len(log) == 3 and log.size_bytes <= one * 3 + 5
    assert (log.first_seq, log.last_seq, log.dropped) == (8, 10, 7)
    got, nxt = log.read(2, 10**6)  # a reader that fell behind gets what is left
    assert [s for s, _ in got] == [8, 9, 10] and nxt == 10


def test_a_line_bigger_than_the_cap_is_kept_alone():
    log = ClockLog(100)
    log.append(line(0))
    log.append(line(1, 500))
    assert len(log) == 1 and log.first_seq == 2
    assert log.read(0, 10)[0][0][1]["i"] == 1  # always at least one line


def test_read_stops_at_max_bytes():
    log = ClockLog(10**6)
    for i in range(10):
        log.append(line(i, 10))
    one = size(0, 10)
    got, nxt = log.read(0, one * 4 + 1)
    assert [s for s, _ in got] == [1, 2, 3, 4] and nxt == 4
    got, nxt = log.read(nxt, one * 4 + 1)
    assert [s for s, _ in got] == [5, 6, 7, 8] and nxt == 8


def test_a_reader_from_before_a_restart_starts_again():
    log = ClockLog(10_000)
    for i in range(3):
        log.append(line(i))
    got, nxt = log.read(500, 10_000)  # the program restarted; the reader had 500
    assert [s for s, _ in got] == [1, 2, 3] and nxt == 3


def test_read_text_gives_the_stored_json():
    log = ClockLog(10_000)
    log.append({"kind": "state", "record": {"status": "READY"}})
    (seq, text), = log.read_text(0, 10_000)[0]
    assert seq == 1 and json.loads(text) == {"kind": "state", "record": {"status": "READY"}}
