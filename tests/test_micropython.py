"""The on-clock guard: the shared code decides and draws the same under MicroPython (D-0029).

The clock is to run ``arthur``'s model, mapping, schedule, tracker and drawing code under
MicroPython 1.29, and MicroPython differs from CPython in ways that change pixels without
raising an error (``docs/reference/micropython.md``). So every golden, every fixture over its
animation, every scenario, the scenes in both endings, the weather pages, the night and the
real robot's log are run through the shared code by ``mpy/guard.py`` under the
``micropython`` binary and under CPython, and must give the same frames and the same mapped
states. The goldens are also compared with their files directly.

The MicroPython run's heap is capped at :data:`HEAP`. Without ``micropython`` on PATH these
tests are skipped with a reason saying the guard did not run.
"""

from __future__ import annotations

import importlib.util
import random
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW, SCENARIOS
from arthur.model import Cue, Weather
from arthur.render import idle_show as show
from arthur.render import list_variants
from arthur.render.preview import legend_for
from arthur.timebase import SHOW_FPS, frames_since
from arthur.tools.export_config import export

MICROPYTHON = shutil.which("micropython")
#: The MicroPython heap for the guard's run (``-X heapsize``); the smallest that runs every
#: case is reported in docs/reference/micropython.md.
HEAP = "2M"
#: Longest the MicroPython run may take, seconds.
TIMEOUT_S = 240

pytestmark = pytest.mark.skipif(
    MICROPYTHON is None,
    reason="micropython is not on PATH: the on-clock guard did NOT run, so nothing checks "
    "that the shared code draws the same on the clock (brew install micropython)",
)

ROOT = Path(__file__).resolve().parent.parent
GUARD_PATH = ROOT / "mpy" / "guard.py"
GOLDEN = Path(__file__).parent / "golden"

_spec = importlib.util.spec_from_file_location("arthur_mpy_guard", GUARD_PATH)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

CFG = default_config()
FRAME_US = 1_000_000 // SHOW_FPS
US = 1_000_000


def _us(dt: datetime) -> int:
    return guard.us_of(dt)


def _segments(us_list: list[int]) -> list[list[int]]:
    """``[[start, step, count], ...]`` covering ``us_list`` in order."""
    out: list[list[int]] = []
    i = 0
    while i < len(us_list):
        if i + 1 < len(us_list):
            step = us_list[i + 1] - us_list[i]
            j = i + 1
            while j + 1 < len(us_list) and us_list[j + 1] - us_list[j] == step:
                j += 1
            out.append([us_list[i], step, j - i + 1])
            i = j + 1
        else:
            out.append([us_list[i], 1, 1])
            i += 1
    return out


def _map_inputs(fx) -> dict:
    return {"snapshot": guard.enc(fx.snapshot), "health": guard.enc(fx.health),
            "now": _us(fx.now), "weather": guard.enc(fx.weather), "cue": guard.enc(fx.cue)}


def _legend() -> list[list[object]]:
    return [[r, g, b, ch] for (r, g, b), ch in legend_for(CFG).items()]


READY = FIXTURES["ready"].display_state(CFG)


def _scene_cue(name: str | None, n: int, length: float = 36.0) -> Cue:
    return Cue(kind="scene", at=NOW, length_s=float(length), n=n, name=name)


def _ending_rounds(name: str, pick) -> list[int]:
    """Rounds 0-3 and the first round with the other ending."""
    rounds = [0, 1, 2, 3]
    seen = {pick(show.SceneContext(name, CFG, n, 270)) for n in rounds}
    for n in range(4, 200):
        if pick(show.SceneContext(name, CFG, n, 270)) not in seen:
            rounds.append(n)
            break
    return rounds


# =========================================================================================
# The cases
# =========================================================================================


def golden_cases() -> list[dict]:
    out = []
    for name, fx in FIXTURES.items():
        for v in list_variants(fx.kind, (52, 16)):
            out.append({"id": f"golden:{name}:{v}", "kind": "ascii", "map": _map_inputs(fx),
                        "variant": v, "legend": _legend(), "times": [[_us(fx.now), 1, 1]],
                        "file": f"{name}__52x16__{v}.txt"})
    for name in sorted(show.SCENES):
        cue = _scene_cue(name, 0)
        ks = list(range(0, show.slot_frames(cue), SHOW_FPS))
        out.append({"id": f"strip:{name}", "kind": "ascii", "header": "strip",
                    "state": guard.enc(replace(READY, cue=cue)), "variant": "arthur",
                    "legend": _legend(), "frames": ks,
                    "times": _segments([_us(cue.at) + k * FRAME_US for k in ks]),
                    "file": f"strips/scene_{name}__round0__36s.txt"})
    return out


def fixture_cases() -> list[dict]:
    """Every fixture in every variant: the idle show's over their whole loop at 8 a second,
    the rest a minute at two frames a second (their animations run on whole seconds), and
    idle over five minutes, which brings round the rare yawn."""
    out = []
    for name, fx in FIXTURES.items():
        for v in list_variants(fx.kind, (52, 16)):
            if fx.fps and v == "arthur":
                ts = [fx.time_at(k / fx.fps) for k in range(round(fx.loop_s * fx.fps))]
            else:
                ts = [fx.time_at(k / 2) for k in range(120)]
            if name == "ready" and v == "arthur":
                ts = [fx.time_at(k) for k in range(300)]
            out.append({"id": f"fixture:{name}:{v}", "kind": "frames", "map": _map_inputs(fx),
                        "variant": v, "times": _segments([_us(t) for t in ts])})
    return out


def scene_cases() -> list[dict]:
    """The three scenes, rounds 0-3 and a round of the other ending, and two other slot
    lengths, every frame of the slot; and the bag's pick for rounds 0-5."""
    from arthur.render.scenes import bird, rat, stretch

    picks = {"bird": bird.perched, "rat": rat.turned, "stretch": stretch.kneads}
    out = []
    plays: list[tuple[str | None, int, float]] = []
    for name in sorted(picks):
        plays += [(name, n, 36.0) for n in _ending_rounds(name, picks[name])]
        plays += [(name, 7, 24.0), (name, 7, 54.0)]
    plays += [(None, n, 36.0) for n in range(6)]
    for name, n, length in plays:
        cue = _scene_cue(name, n, length)
        k_all = range(-4, show.slot_frames(cue) + 4)  # a little home either side
        out.append({"id": f"scene:{name}:{n}:{length:g}s", "kind": "frames",
                    "state": guard.enc(replace(READY, cue=cue)), "variant": "arthur",
                    "times": _segments([_us(cue.at) + k * FRAME_US for k in k_all])})
    return out


def page_cases() -> list[dict]:
    """Both weather layouts with every glyph, by day and night, hot, cold and missing
    readings, and no reading at all: every frame of the dissolves and a few seconds of the
    page."""
    out = []
    codes = [0, 1, 2, 3, 45, 51, 61, 71, 85, 95, 99, 7]
    temps = [(54.4, 61.0, 48.0), (-12.5, -3.0, -20.0), (101.5, 104.0, 88.0), (0.49, None, 5.0)]
    rng = random.Random(1)
    for page in ("weather", "weather_big"):
        for i, code in enumerate(codes + [None]):
            cue = Cue(kind="page", at=NOW, length_s=54.0, n=3, name=page)
            w = None
            if code is not None:
                t, hi, lo = temps[i % len(temps)]
                w = Weather(temp_f=t, code=code, is_day=i % 3 != 1, high_f=hi, low_f=lo,
                            fetched_at=NOW - timedelta(minutes=10))
            state = replace(READY, cue=cue, weather=w)
            ks = list(range(8)) + list(range(424, 432)) + sorted(rng.sample(range(8, 424), 24))
            out.append({"id": f"page:{page}:{code}", "kind": "frames",
                        "state": guard.enc(state), "variant": "arthur",
                        "times": _segments([_us(cue.at) + k * FRAME_US for k in ks])})
    return out


def scenario_times(sc) -> list[int]:
    """Sample times: every frame (8 a second) where the idle show is in a transition or a
    scene, Arthur is falling asleep or waking, or a step is near; two a second elsewhere,
    where the screens change on whole seconds. Decided from CPython's own mapping."""
    from arthur.mapping import to_display_state

    steps = [s.at_s for s in sc.steps]
    out = []
    for k in range(round(sc.duration_s * SHOW_FPS) + 1):
        t = k / SHOW_FPS
        snap, health, now = sc.at(t)
        st = to_display_state(snap, health, now, CFG)
        dense = any(abs(t - s) <= 3 for s in steps)
        if st.cue is not None:
            part = show.part_of(st.cue, frames_since(st.cue.at, now))
            dense = dense or part not in ("page", "home")
        if st.woke_at is not None:
            dense = True
        if st.asleep_since is not None and (now - st.asleep_since).total_seconds() < 4:
            dense = True
        if dense or k % (SHOW_FPS // 2) == 0:
            out.append(_us(now))
    return out


def scenario_cases() -> list[dict]:
    out = []
    for name, sc in SCENARIOS.items():
        steps = [{"at_us": round(s.at_s * US), "snapshot": guard.enc(s.snapshot),
                  "health": guard.enc(s.health)} for s in sc.steps]
        out.append({"id": f"scenario:{name}", "kind": "scenario", "start": _us(sc.start),
                    "steps": steps, "times": _segments(scenario_times(sc))})
    return out


def replay_cases() -> list[dict]:
    """The real robot's three cycles (tests/data/real-cycles.jsonl), and a synthesised visit
    whose clean cycle is interrupted the way the real one of 2026-11-21 was (so the countdown
    carries on: ``cycled_s``), fed through the tracker and mapped, two frames a second
    through each visit's window, as the studio's Replay."""
    from logsynth import interrupted_visit_lines, write

    data = ROOT / "tests" / "data" / "real-cycles.jsonl"
    return [
        _replay_case("replay:real-cycles",
                     lambda d: shutil.copy(data, d / "spike-20261116.jsonl")),
        _replay_case("replay:interrupted",
                     lambda d: write(d / "spike-20260301.jsonl", interrupted_visit_lines(100))),
    ]


def _replay_case(case_id: str, put) -> dict:
    import tempfile

    from arthur.sources.logfile import RobotLog, snapshot_from_record

    with tempfile.TemporaryDirectory() as dname:
        d = Path(dname)
        put(d)
        rl = RobotLog(Path(d), just_cleaned_s=CFG.timings.just_cleaned_s,
                      visit_min_s=CFG.timings.visit_min_s)
        rl.refresh()
        tl = rl.timeline
        lines = []
        for ln in tl.states:
            rec = ln.record
            if not rec.get("status"):
                rec = {**rec, "status": ln.status}
            lines.append({"ts": _us(ln.ts),
                          "record": {k: rec.get(k) for k in ("status", "pet_weight")},
                          "snapshot": guard.enc(snapshot_from_record(rec))})
        ts = []
        for v in tl.visits():
            ts += [_us(v.window_start + timedelta(seconds=k / 2))
                   for k in range(int(v.window_s * 2) + 1)]
        reference = {}
        from arthur.mapping import to_display_state
        from arthur.sources.logfile import replay_view

        for v in tl.visits():
            for k in range(int(v.window_s * 2) + 1):
                view = replay_view(tl, v, k / 2)
                reference[_us(view.now)] = guard.text(
                    to_display_state(view.snapshot, view.health, view.now, CFG))
    return {"id": case_id, "kind": "replay", "lines": lines,
            "activity_visits": [_us(t) for t in tl._visit_starts],
            "times": _segments(ts), "reference": reference}


def tracker_cases() -> list[dict]:
    t0 = datetime(2026, 11, 19, 22, 30, tzinfo=UTC)

    def at(s: float) -> int:
        return _us(t0 + timedelta(seconds=s))

    def rec(status, w=4.6):
        return {"status": status, "pet_weight": w}

    ops = [
        ["feed", rec("READY"), at(0)],
        ["feed", rec("CAT_DETECTED"), at(30)],
        ["feed", rec("CAT_SENSOR_TIMING"), at(36)],
        ["feed", rec("CAT_SENSOR_TIMING", 10.6), at(95)],
        ["gap", at(100)],
        ["feed", rec("CAT_SENSOR_TIMING", 10.6), at(700)],
        ["feed", rec("CLEAN_CYCLE", 10.6), at(800)],
        ["feed", rec("READY", 10.6), at(930)],
        ["feed", rec("CAT_DETECTED", 10.6), at(2000)],
        ["gap", at(2010)],
        ["feed", rec("READY", None), at(2600)],
        ["seed", {"visit_started_at": None, "cat_left_at": None, "last_visit_at": at(2500)},
         at(2601)],
        ["feed", {"status": None, "pet_weight": True}, at(2700)],
        ["feed", rec("CAT_DETECTED", 10.6), at(3000)],
        ["seed", {"visit_started_at": at(2990), "cat_left_at": at(3050),
                  "last_visit_at": None}, at(3060)],
        ["feed", rec("CAT_SENSOR_TIMING", 11.0), at(3100)],
        ["feed", rec("READY", 11.0), at(3700)],
    ]
    # The one interruption logged live (2026-11-21), then a second in one cycle, a pause that
    # does not carry, and a gap mid-interruption: the seconds already cycled (cycled_s).
    r0 = datetime(2026, 11, 21, 14, 33, 6, 878000, tzinfo=UTC)

    def rt(s: float) -> int:
        return _us(r0 + timedelta(seconds=s))

    interrupted = [
        ["feed", rec("CAT_SENSOR_TIMING", 10.6), rt(-480)],
        ["feed", rec("CLEAN_CYCLE", 10.6), rt(0)],
        ["feed", rec("CAT_SENSOR_INTERRUPTED", 10.6), rt(40.1)],
        ["feed", rec("CLEAN_CYCLE", 10.6), rt(65.37)],
        ["feed", rec("CAT_SENSOR_INTERRUPTED", 10.6), rt(70.003)],
        ["feed", rec("CLEAN_CYCLE", 10.6), rt(81.25)],
        ["feed", rec("READY", 10.6), rt(150.5)],
        ["feed", rec("CLEAN_CYCLE", 10.6), rt(900)],
        ["feed", rec("PAUSED", 10.6), rt(933.3)],
        ["feed", rec("CLEAN_CYCLE", 10.6), rt(940)],
        ["feed", rec("CAT_SENSOR_INTERRUPTED", 10.6), rt(951.1)],
        ["gap", rt(960)],
        ["feed", rec("CLEAN_CYCLE", 10.6), rt(1700)],
    ]
    return [{"id": "tracker:gap-and-seed", "kind": "tracker", "visit_min_s": 30.0, "ops": ops},
            {"id": "tracker:interrupted", "kind": "tracker", "visit_min_s": 30.0,
             "ops": interrupted}]


def helper_cases() -> list[dict]:
    import rngcheck  # from mpy/, put on sys.path by the guard module

    from arthur.zonetable import zone_table

    zone = CFG.tz
    start, end = datetime(2025, 1, 1, tzinfo=UTC), datetime(2045, 1, 1, tzinfo=UTC)
    changes = zone_table(zone.key, start, end)["changes"]
    rng = random.Random(7)
    instants = []
    for t, _ in changes:
        instants += [(t + d) * US for d in (-3601, -1, 0, 1, 3599, 3600)]
    instants += [rng.randrange(_us(start), _us(end)) for _ in range(500)]
    hours = []
    for t, _ in changes:
        day = datetime.fromtimestamp(t, UTC).astimezone(zone).date()
        for dd in (-1, 0, 1):
            d = day + timedelta(days=dd)
            hours += [[d.year, d.month, d.day, h] for h in range(24)]
    values = []
    for v in (0, 20, 30, 33.3, 12.5, 0.1, 99.99, 45.5, 7.25, 1e-3, 50.0, 2.675):
        for length in (180, 200, 1000, 37):
            values.append([guard.enc(v), length])
    seeds = list(rngcheck.SEEDS) + rngcheck.more_seeds(40)
    return ([{"id": "config", "kind": "config"}, {"id": "registry", "kind": "registry"},
             {"id": "decimal", "kind": "decimal", "values": values}]
            + [{"id": f"rng:{i}", "kind": "rng", "seeds": seeds[i:i + 16]}
               for i in range(0, len(seeds), 16)]
            + [{"id": f"zone:utc:{i}", "kind": "zone", "instants": instants[i:i + 200]}
               for i in range(0, len(instants), 200)]
            + [{"id": f"zone:local:{i}", "kind": "zone", "local_hours": hours[i:i + 400]}
               for i in range(0, len(hours), 400)])


# =========================================================================================
# Running both
# =========================================================================================


def _read(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            out[line.split(" ", 1)[0]] = line.split(" ", 1)[1]
            continue
        key, _, rest = line.partition(" ")
        out[key] = rest
    return out


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Both runs: ``(cases by id, MicroPython's results, CPython's results, seconds)``."""
    import json

    d = tmp_path_factory.mktemp("mpy")
    cases = (helper_cases() + golden_cases() + fixture_cases() + scene_cases()
             + page_cases() + scenario_cases() + replay_cases() + tracker_cases())
    job = d / "job.jsonl"
    with job.open("w") as fh:
        fh.write(json.dumps(export()) + "\n")
        for c in cases:
            fh.write(json.dumps({k: v for k, v in c.items()
                                 if k not in ("file", "reference")}) + "\n")
    mpy_out, cpy_out = d / "micropython.txt", d / "cpython.txt"
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [MICROPYTHON, "-X", f"heapsize={HEAP}", str(GUARD_PATH), str(job), str(mpy_out)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    guard.run(str(job), str(cpy_out))
    try:
        log, _ = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail(f"the MicroPython run took over {TIMEOUT_S} s")
    seconds = time.monotonic() - t0
    assert proc.returncode == 0, f"micropython exited {proc.returncode}:\n{log[-3000:]}"
    by_id = {c["id"]: c for c in cases}
    print(f"\non-clock guard: {len(cases)} cases, MicroPython {seconds:.1f} s, heap {HEAP}")
    return by_id, _read(mpy_out), _read(cpy_out), seconds


def _diff(case: dict, mpy: str, cpy: str) -> str:
    if mpy.startswith("ERROR"):
        return f"{case['id']}: MicroPython raised {mpy}"
    if cpy.startswith("ERROR"):
        return f"{case['id']}: CPython raised {cpy}"
    a, b = mpy.split(" "), cpy.split(" ")
    if len(a) != len(b):
        return f"{case['id']}: {len(a)} results on MicroPython, {len(b)} on CPython"
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if x != y:
            what = "the mapped state" if i == 0 and case["kind"] == "frames" else f"result {i}"
            return f"{case['id']}: {what} differs ({sum(p != q for p, q in zip(a, b))} differ)"
    return f"{case['id']}: differs"


def _check(runs, prefix: str) -> int:
    by_id, mpy, cpy, _ = runs
    ids = [i for i in by_id if i.startswith(prefix)]
    assert ids, prefix
    bad = []
    for i in ids:
        assert i in cpy and not cpy[i].startswith("ERROR"), f"{i}: CPython: {cpy.get(i)}"
        if mpy.get(i) != cpy[i]:
            bad.append(_diff(by_id[i], mpy.get(i, "ERROR missing"), cpy[i]))
    assert not bad, f"{len(bad)} of {len(ids)} differ under MicroPython:\n" + "\n".join(bad)
    return len(ids)


def test_config_registries_decimal_shares_rng_and_zone(runs):
    for prefix in ("config", "registry", "decimal", "rng", "zone"):
        _check(runs, prefix)


def test_goldens_match_their_files_under_micropython(runs):
    by_id, mpy, _, _ = runs
    bad = []
    for i, case in by_id.items():
        if case["kind"] != "ascii":
            continue
        lines = (GOLDEN / case["file"]).read_text().split("\n")[1:]  # after the legend
        want = "|".join(lines).rstrip("|")
        if mpy.get(i) != want:
            bad.append(i)
    assert not bad, f"goldens drawn differently under MicroPython: {bad}"
    _check(runs, "golden:")
    _check(runs, "strip:")


def test_fixtures_animate_the_same(runs):
    assert _check(runs, "fixture:") >= len(FIXTURES)


def test_scenes_in_both_endings(runs):
    _check(runs, "scene:")


def test_weather_pages(runs):
    _check(runs, "page:")


def test_scenarios_including_the_idle_hour_and_the_night(runs):
    assert _check(runs, "scenario:") == len(SCENARIOS)


@pytest.mark.parametrize("case_id", ["replay:real-cycles", "replay:interrupted"])
def test_real_cycles_through_tracker_and_mapping(runs, case_id):
    by_id, _, cpy, _ = runs
    _check(runs, case_id)
    # The guard's replay on CPython is the studio's Replay: the same states.
    case = by_id[case_id]
    ref = case["reference"]
    times = list(guard.times(case["times"]))
    digests = [r.split(":")[0] for r in cpy[case_id].split(" ")]
    assert len(times) == len(digests)
    for us, dg in zip(times, digests, strict=True):
        assert guard.text_digest(ref[us]) == dg, us
    if case_id == "replay:interrupted":
        # The resumed cleaning carries on: some state ends its cycle 155.27 s after the
        # first clean start (130 s estimate plus 25.27 s standing still).
        clean = case["lines"][3]["ts"]
        assert case["lines"][3]["record"]["status"] == "CLEAN_CYCLE"
        want = "cycle_end=t" + str(clean + 155_270_000)
        assert any(want in r for r in ref.values()), want


def test_tracker_gap_and_seed(runs):
    _check(runs, "tracker:")


def test_the_run_is_quick(runs):
    assert runs[3] < TIMEOUT_S
    assert "#heap" in runs[1]  # the MicroPython run reached the end
