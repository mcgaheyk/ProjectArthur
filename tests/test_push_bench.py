"""scripts/push_bench.py (arthur/pushbench.py): the fixed-rate grid, the statistics, the
patterns and the feeder guard, against the fake clock with fake time."""

import io
import json
import socket
from datetime import datetime

import pytest

from arthur.config import default_config
from arthur.pushbench import (
    EXIT_CLOCK,
    EXIT_FEEDER,
    EXIT_OK,
    PATTERNS,
    TRANSITIONS,
    Row,
    Sample,
    build_parser,
    check_feeder,
    fixture_frame,
    latency_stats,
    main,
    pattern,
    result_json,
    run,
    run_grid,
    transition_steps,
)
from arthur.render import Frame
from arthur.sim.server import CLOCKS, create_app

CFG = default_config()
WALL = datetime(2026, 11, 18, 9, 30, 0)


class FakeTime:
    """A clock that only moves when the code sleeps (or a fake push says so)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


class TickingTime(FakeTime):
    """Moves 10 ms every time it is read."""

    def __call__(self) -> float:
        self.t += 0.01
        return self.t


@pytest.fixture
async def client(aiohttp_client):
    return await aiohttp_client(create_app(start_tasks=False))


def base(client) -> str:
    return str(client.make_url("")).rstrip("/")


def the_clock(client):
    (clock,) = client.app[CLOCKS].values()
    return clock


async def bench(url: str, *argv: str, tmp_path=None, fake: FakeTime | None = None):
    fake = fake or FakeTime()
    out = io.StringIO()
    opts = build_parser().parse_args([*argv, "--clock", url])
    rc = await run(opts, CFG, out=out, time_fn=fake, sleep=fake.sleep,
                   log_dir=tmp_path, wall_fn=lambda: WALL)
    return rc, out.getvalue()


# --- the grid ----------------------------------------------------------------------------


def fake_push(fake: FakeTime, durations: list[float], pushed: list[int]):
    """A push that takes ``durations[i]`` seconds of fake time (the last repeats)."""
    async def push(frame: Frame) -> Sample:
        i = len(pushed)
        pushed.append(frame.pixels[0][0])  # the tick number, see frame_at below
        start = fake.t
        fake.t += durations[min(i, len(durations) - 1)]
        return Sample(start, (fake.t - start) * 1000, ["PUT /x"], 0, 0)
    return push


def frame_at(k: int) -> Frame:
    return Frame(52, 16, (k, 0, 0))


async def test_grid_keeps_time_when_pushes_are_quick():
    fake, pushed, row = FakeTime(), [], Row("r")
    await run_grid(fake_push(fake, [0.01], pushed), frame_at, 8, 2, lambda t: row,
                   time_fn=fake, sleep=fake.sleep)
    assert pushed == list(range(16))
    assert row.dropped == 0 and row.sent == 16
    assert row.rate == pytest.approx(8, abs=0.1)


async def test_grid_drops_rather_than_queues_when_a_push_is_slow():
    fake, pushed, row = FakeTime(), [], Row("r")
    # 8/s is a tick every 0.125 s. Push 1 (tick 1) takes 0.3 s: it ends at 0.425, past ticks
    # 2 and 3 (0.25, 0.375), so those are skipped and the next push is tick 4 at 0.5.
    await run_grid(fake_push(fake, [0.01, 0.3, 0.01], pushed), frame_at, 8, 1, lambda t: row,
                   time_fn=fake, sleep=fake.sleep)
    assert pushed == [0, 1, 4, 5, 6, 7]
    assert row.dropped == 2
    assert row.sent == 6


async def test_grid_does_not_count_ticks_after_the_end_as_dropped():
    fake, pushed, row = FakeTime(), [], Row("r")
    await run_grid(fake_push(fake, [0.01, 5.0], pushed), frame_at, 4, 1, lambda t: row,
                   time_fn=fake, sleep=fake.sleep)
    assert pushed == [0, 1]
    assert row.dropped == 2  # ticks 2 and 3; the run has 4 ticks


# --- statistics --------------------------------------------------------------------------


def test_latency_stats_nearest_rank():
    st = latency_stats([float(v) for v in range(20, 0, -1)])  # 1..20, unsorted
    assert st == {"n": 20, "min": 1.0, "p50": 10.0, "p95": 19.0, "max": 20.0}
    assert latency_stats([7.0]) == {"n": 1, "min": 7.0, "p50": 7.0, "p95": 7.0, "max": 7.0}
    assert latency_stats([])["n"] == 0 and latency_stats([])["p50"] is None


def test_row_keeps_checks_and_failures_out_of_latency():
    row = Row("r", seconds=2)
    row.samples += [
        Sample(0, 3.0, ["PUT /api/v1/apps/pushed/litter"], 0, 0),
        Sample(0, 40.0, ["GET /api/v1/settings", "PUT /api/v1/apps/pushed/litter",
                         "GET /api/v1/apps"], 1, 0),
        Sample(0, 5.0, ["PUT /api/v1/apps/pushed/litter"], 1, 0, error="boom"),
    ]
    assert row.frame_ms == [3.0] and row.check_ms == [40.0]
    assert (row.sent, row.failures, row.connections, row.rate) == (2, 1, 2, 1.0)


# --- patterns ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", PATTERNS)
def test_every_pattern_gives_52x16_frames(name):
    frame_at = pattern(name, CFG, fps=8, px=3, frames=6)
    frames = [frame_at(k) for k in range(40)]
    assert all(f.size == (52, 16) for f in frames)
    assert len({tuple(f.pixels) for f in frames}) > 1


@pytest.mark.parametrize("name", sorted(TRANSITIONS))
@pytest.mark.parametrize("n", [1, 3, 6])
def test_transitions_start_on_a_and_end_on_b(name, n):
    a, b = fixture_frame("ready", CFG), fixture_frame("cleaning", CFG)
    fn = TRANSITIONS[name]
    assert fn(a, b, 0, n) == a
    assert fn(a, b, n, n) == b
    if n > 1:
        mid = fn(a, b, n // 2, n)
        assert mid != a and mid != b


def test_transition_cycle_holds_each_end():
    steps = transition_steps(6, 8)
    assert steps[:8] == [0] * 8 and steps[8:13] == [1, 2, 3, 4, 5]
    assert steps[13:21] == [6] * 8 and steps[21:] == [5, 4, 3, 2, 1]
    frame_at = pattern("wipe", CFG, fps=8, frames=6)
    assert frame_at(0) == fixture_frame("ready", CFG)
    assert frame_at(13) == fixture_frame("cleaning", CFG)
    assert frame_at(len(steps)) == frame_at(0)


def test_walk_moves_px_a_frame():
    def leftmost(f: Frame) -> int:
        return min(i % 52 for i, p in enumerate(f.pixels) if p != (0, 0, 0))

    frame_at = pattern("walk", CFG, px=2)
    assert leftmost(frame_at(20)) - leftmost(frame_at(10)) == 20


# --- end to end against the fake clock ---------------------------------------------------


async def test_rates_against_the_fake_clock(client, tmp_path):
    rc, out = await bench(base(client), "rates", "--rates", "4,8", "--seconds", "2",
                          tmp_path=tmp_path)
    assert rc == EXIT_OK, out
    assert "first push" in out and "4/s" in out and "8/s" in out
    assert not the_clock(client).enlarge_apps
    assert "litter" in the_clock(client).apps
    (line,) = (tmp_path / "pushbench-2026-11-18.jsonl").read_text().splitlines()
    rec = json.loads(line)
    assert rec["mode"] == "rates" and rec["params"]["rates"] == [4.0, 8.0]
    first, r4, r8 = rec["rows"]
    # The first push ran the checks; it is counted there, not in the latency.
    assert first["checks"]["n"] == 1 and first["latency_ms"]["n"] == 0
    assert "GET /api/v1/settings" in first["checks"]["requests"]
    assert (r4["sent"], r8["sent"]) == (8, 16)
    for row in (r4, r8):
        assert row["checks"]["n"] == 0 and row["dropped"] == 0 and row["failures"] == 0
        assert set(row) >= {"label", "sent", "dropped", "failures", "rate_per_s",
                            "connections", "latency_ms", "checks"}
        assert set(row["latency_ms"]) == {"n", "min", "p50", "p95", "max"}
        assert row["latency_ms"]["n"] == row["sent"] == len(row["latencies_ms"])
    # Keep-alive on the fake clock: one connection for the whole run.
    assert rec["connections_opened"] == 1
    assert first["connections"] == 1 and r4["connections"] == r8["connections"] == 0


async def test_json_line_matches_the_summary(client, tmp_path):
    rc, out = await bench(base(client), "counter", "--seconds", "1", tmp_path=tmp_path)
    assert rc == EXIT_OK
    rec = json.loads((tmp_path / "pushbench-2026-11-18.jsonl").read_text())
    table = {ln.split()[0]: ln.split() for ln in out.splitlines()[1:]}
    row = rec["rows"][1]
    assert row["label"] == "counter 8/s"
    cells = table["counter"]  # counter 8/s sent drop fail rate conn min p50 p95 max checks
    assert [int(c) for c in cells[2:5]] == [row["sent"], row["dropped"], row["failures"]]
    assert float(cells[5]) == pytest.approx(row["rate_per_s"], abs=0.01)
    assert int(cells[6]) == row["connections"]
    assert float(cells[8]) == pytest.approx(row["latency_ms"]["p50"], abs=0.06)


@pytest.mark.parametrize("mode", ["flatout", "idle-gap", "sustained", "walk", "dissolve",
                                  "wipe", "slide"])
async def test_every_mode_runs(client, mode):
    argv = {"flatout": ["--seconds", "0.5"], "idle-gap": ["--gaps", "1,2"],
            "sustained": ["--seconds", "130", "--fps", "1"]}.get(mode, ["--seconds", "2"])
    # flatout never sleeps, so its time has to move on its own: 10 ms a reading.
    fake = TickingTime() if mode == "flatout" else None
    rc, out = await bench(base(client), mode, *argv, fake=fake)
    assert rc == EXIT_OK, out
    if mode == "idle-gap":
        assert "after 1s idle" in out and "after 2s idle" in out
    if mode == "sustained":
        assert "min 1" in out and "min 3" in out


async def test_unreachable_clock_stops_after_the_first_push(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    rc, out = await bench(f"http://127.0.0.1:{port}", "rates", tmp_path=tmp_path)
    assert rc == EXIT_CLOCK
    assert "stopped: the first push failed" in out
    rec = json.loads((tmp_path / "pushbench-2026-11-18.jsonl").read_text())
    assert rec["aborted"] and rec["rows"][0]["failures"] == 1


def test_result_json_is_serialisable():
    from arthur.pushbench import Result

    r = Result("rates", "http://x", "litter", {"seconds": 1})
    r.row("first push")
    assert json.loads(json.dumps(result_json(r, WALL)))["rows"][0]["sent"] == 0


# --- the feeder guard --------------------------------------------------------------------


def test_feeder_guard_refuses_without_force(capsys):
    rc = main(["rates", "--clock", "http://127.0.0.1:9"], is_feeder_running=lambda: True)
    assert rc == EXIT_FEEDER
    err = capsys.readouterr().err
    assert "feeder" in err and "--force" in err


def test_feeder_guard_force_and_no_feeder():
    opts = build_parser().parse_args(["rates", "--clock", "x"])
    forced = build_parser().parse_args(["rates", "--clock", "x", "--force"])
    assert check_feeder(opts, lambda: True, io.StringIO()) == EXIT_FEEDER
    assert check_feeder(forced, lambda: True) is None
    assert check_feeder(opts, lambda: False) is None


def test_force_goes_on_to_the_clock(capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    rc = main(["rates", "--clock", f"127.0.0.1:{port}", "--force", "--no-log"],
              is_feeder_running=lambda: True)
    assert rc == EXIT_CLOCK  # it got past the guard and tried the clock
    assert "first push failed" in capsys.readouterr().out
