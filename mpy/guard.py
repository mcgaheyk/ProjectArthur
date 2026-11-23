"""The on-clock guard's runner: the shared code decides and draws, case by case (D-0029).

Runs unchanged on CPython and MicroPython::

    micropython -X heapsize=2M mpy/guard.py JOB.jsonl OUT.txt
    python mpy/guard.py JOB.jsonl OUT.txt

``tests/test_micropython.py`` writes the job on CPython (from the fixtures, the scenarios, the
goldens and the real robot's log), runs it here under both Pythons, and compares the outputs
line by line. The job's first line is the exported config (``arthur.tools.export_config``);
every other line is one case. Each output line is ``<case id> <result>``; a result is a list
of short frame digests, or text (``|`` for a newline), the same on both Pythons when the
shared code behaves the same.

Case kinds:

- ``frames``: a DisplayState mapped here (from the snapshot, health, time, weather and cue)
  and drawn at many times in one variant; the result is the mapped state, then a digest per
  frame.
- ``ascii``: frames drawn as text with the preview's legend, to compare with the goldens.
- ``scenario``: a scenario's steps, mapped and drawn at each sample time (the mapping asks
  the schedule, so the idle show, the night and the waking are decided here too).
- ``replay``: the real robot's log lines fed through the tracker, mapped and drawn.
- ``tracker``: a made-up run of records with a gap in watching and seeding.
- ``rng``, ``zone``, ``decimal``, ``config``: the generator, the time zone, the schedule's
  decimal shares and the config, as text.
"""

import sys

IS_MPY = sys.implementation.name == "micropython"


def _here():
    f = __file__
    i = f.rfind("/")
    return f[:i] if i > 0 else "."


HERE = _here()
if "/" in HERE:
    ROOT = HERE[: HERE.rfind("/")] or "/"
else:
    ROOT = ".." if HERE == "." else "."
if IS_MPY:
    sys.path.insert(0, HERE + "/lib")  # the stdlib stand-ins: MicroPython only
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import json
import struct
from datetime import UTC, datetime, timedelta

from arthur.compat import field_names, replace

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ONE_US = timedelta(microseconds=1)


# =========================================================================================
# The codec: values to JSON and back, and to text that is the same on both Pythons
# =========================================================================================


def _bits(x):
    return "".join(f"{b:02x}" for b in struct.pack(">d", x))


def _unbits(h):
    return struct.unpack(">d", bytes([int(h[i:i + 2], 16) for i in range(0, 16, 2)]))[0]


def us_of(dt):
    return (dt - EPOCH) // ONE_US


def dt_of(us):
    return EPOCH + timedelta(microseconds=us)


def _fields(v):
    try:
        return field_names(type(v))
    except (TypeError, AttributeError):
        return None


def enc(v):
    """A JSON-ready form of ``v``: floats by their bits, datetimes in microseconds, records
    by class name and fields."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return {"f": _bits(v)}
    if isinstance(v, datetime):
        return {"t": us_of(v)}
    if isinstance(v, (list, tuple)):
        return {"l": [enc(x) for x in v]}
    names = _fields(v)
    if names is None:
        raise TypeError("cannot encode " + repr(v))
    return {"r": type(v).__name__, "v": {n: enc(getattr(v, n)) for n in names}}


def _records():
    from arthur.model import Cue, DisplayState, FeederHealth, RobotSnapshot, Weather

    return {c.__name__: c for c in (Cue, DisplayState, FeederHealth, RobotSnapshot, Weather)}


_RECORDS = None


def dec(v):
    global _RECORDS
    if not isinstance(v, dict):
        return v
    if "f" in v:
        return _unbits(v["f"])
    if "t" in v:
        return dt_of(v["t"])
    if "l" in v:
        return [dec(x) for x in v["l"]]
    if _RECORDS is None:
        _RECORDS = _records()
    cls = _RECORDS[v["r"]]
    kw = {}
    for k, x in v["v"].items():
        kw[k] = dec(x)
    return cls(**kw)


def text(v):
    """``v`` as text that is the same on both Pythons for the same value."""
    if v is None:
        return "N"
    if isinstance(v, bool):
        return "T" if v else "F"
    if isinstance(v, int):
        return "i" + str(v)
    if isinstance(v, float):
        return "f" + _bits(v)
    if isinstance(v, str):
        return "s" + str(len(v)) + ":" + v
    if isinstance(v, datetime):
        return "t" + str(us_of(v))
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(text(x) for x in v) + "]"
    if isinstance(v, dict) or hasattr(v, "items"):
        keys = sorted(v.keys())
        return "{" + ",".join(text(k) + "=" + text(v[k]) for k in keys) + "}"
    names = _fields(v)
    if names is not None:
        return type(v).__name__ + "(" + ",".join(n + "=" + text(getattr(v, n))
                                                 for n in names) + ")"
    key = getattr(v, "key", None)  # a time zone: ZoneInfo on CPython, TableZone here
    if isinstance(key, str):
        return "z" + key
    raise TypeError("no text for " + repr(v))


def times(segments):
    """Microsecond times from ``[[start_us, step_us, count], ...]``."""
    for start, step, count in segments:
        for i in range(count):
            yield start + i * step


def _digest(data):
    import hashlib

    return "".join(f"{b:02x}" for b in hashlib.sha256(data).digest()[:8])


def frame_digest(frame):
    return _digest(frame.to_rgb888())


def text_digest(s):
    return _digest(s.encode())


# =========================================================================================
# The cases
# =========================================================================================


def load_all():
    """Import every shared module and load the fonts, before anything else, as the program
    on the clock will. Compiling the modules from source and reading the large font (75 KB,
    731 glyphs) need the largest contiguous blocks of the run; on a heap already fragmented
    by other work they fail with MemoryError long before the heap is full."""
    import arthur.mapping
    import arthur.render
    import arthur.sources.tracker  # noqa: F401
    from arthur.render.font import condensed_small_font, large_font

    condensed_small_font()  # the small font with it
    large_font()


class Ctx:
    def __init__(self, cfg_data):
        from arthur.config import config_from_json

        self.cfg = config_from_json(cfg_data)


def _map(case, cfg):
    from arthur.mapping import to_display_state

    m = case["map"]
    return to_display_state(dec(m["snapshot"]), dec(m["health"]), dt_of(m["now"]), cfg,
                            weather=dec(m.get("weather")), cue=dec(m.get("cue")))


def case_frames(case, ctx, out):
    from arthur.render import render

    cfg = ctx.cfg
    state = _map(case, cfg) if "map" in case else dec(case["state"])
    variant = case.get("variant")
    out.write(text_digest(text(state)))
    for us in times(case["times"]):
        out.write(" " + frame_digest(render(state, dt_of(us), cfg, variant=variant)))


def case_ascii(case, ctx, out):
    """Frames as the goldens print them: an optional header per frame, then the rows."""
    from arthur.render import render
    from arthur.render.idle_show import part_of

    cfg = ctx.cfg
    legend = {}
    for r, g, b, ch in case["legend"]:
        legend[(r, g, b)] = ch
    state = _map(case, cfg) if "map" in case else dec(case["state"])
    variant = case.get("variant")
    head = case.get("header")  # "strip": the scene strips' "frame k (+s) part" lines
    parts = []
    for k, us in enumerate(times(case["times"])):
        f = render(state, dt_of(us), cfg, variant=variant)
        if head == "strip":
            kk = case["frames"][k]
            parts.append(f"frame {kk} (+{kk / 8:g}s) {part_of(state.cue, kk)}")
        parts.append(f.to_ascii(legend))
    out.write("|".join("|".join(p.split("\n")) for p in parts))


def case_scenario(case, ctx, out):
    from arthur.mapping import to_display_state
    from arthur.model import FeederHealth
    from arthur.render import render

    cfg = ctx.cfg
    start = case["start"]
    steps = [(s["at_us"], dec(s["snapshot"]), dec(s["health"])) for s in case["steps"]]
    first = True
    for us in times(case["times"]):
        t = us - start
        cur = steps[0]
        for s in steps:
            if s[0] <= t:
                cur = s
            else:
                break
        now = dt_of(us)
        health = cur[2] or FeederHealth(last_contact_at=now, ws_connected=True)
        state = to_display_state(cur[1], health, now, cfg)
        if not first:
            out.write(" ")
        first = False
        out.write(text_digest(text(state)) + ":" + frame_digest(render(state, now, cfg)))


def case_replay(case, ctx, out):
    from arthur.mapping import to_display_state
    from arthur.model import FeederHealth
    from arthur.render import render
    from arthur.sources.tracker import TrackerState

    cfg = ctx.cfg
    visit_min_s = float(cfg.timings.visit_min_s)
    lines = []
    st = TrackerState()
    for ln in case["lines"]:
        st = st.feed(ln["record"], dt_of(ln["ts"]), visit_min_s)
        lines.append((ln["ts"], dec(ln["snapshot"]), st))
    visit_starts = case["activity_visits"]
    first = True
    for us in times(case["times"]):
        i = -1
        for j in range(len(lines)):
            if lines[j][0] <= us:
                i = j
            else:
                break
        now = dt_of(us)
        snap = None
        if i >= 0:
            obs = lines[i][2].observed(now, visit_min_s)
            cands = [d for d in (obs.last_visit_at,) if d is not None]
            act = [v for v in visit_starts if v <= us]
            if act:
                cands.append(dt_of(act[-1]))
            last = None
            for d in cands:
                if last is None or d > last:
                    last = d
            snap = replace(lines[i][1], status_since=obs.status_since,
                           previous_status=obs.previous_status, last_visit_at=last,
                           visit_started_at=obs.visit_started_at, cat_left_at=obs.cat_left_at,
                           cycled_s=obs.cycled_s)
        health = FeederHealth(last_contact_at=now, ws_connected=True)
        state = to_display_state(snap, health, now, cfg)
        if not first:
            out.write(" ")
        first = False
        out.write(text_digest(text(state)) + ":" + frame_digest(render(state, now, cfg)))


def case_tracker(case, ctx, out):
    from arthur.sources.tracker import Tracker

    tr = Tracker(case["visit_min_s"])
    res = []
    for op in case["ops"]:
        what = op[0]
        if what == "feed":
            tr.feed(op[1], dt_of(op[2]))
        elif what == "gap":
            tr.gap(dt_of(op[1]))
        elif what == "seed":
            kw = {}
            for k, v in op[1].items():
                kw[k] = dt_of(v) if v is not None else None
            tr.seed(**kw)
        res.append(text(tr.state) + "/" + text(tr.observed(dt_of(op[-1]))))
    out.write("|".join(res))


def case_rng(case, ctx, out):
    import rngcheck

    from arthur.compat import Random

    out.write(" ".join(text_digest(rngcheck.as_text(rngcheck.exercise(Random, s)))
                       for s in case["seeds"]))


def case_zone(case, ctx, out):
    from datetime import date

    cfg = ctx.cfg
    for us in case.get("instants", ()):
        loc = cfg.local(dt_of(us))
        out.write(f"{loc.year}-{loc.month}-{loc.day}T{loc.hour}:{loc.minute}:{loc.second}"
                  f".{loc.microsecond}/f{loc.fold}/{loc.date().toordinal()}|")
    for y, m, d, h in case.get("local_hours", ()):
        out.write(str(us_of(cfg.at_local_hour(date(y, m, d), h))) + "/")
        # The other reading of a repeated hour, which the shared code does not use today.
        later = datetime(y, m, d, h, 30, tzinfo=cfg.tz, fold=1).astimezone(UTC)
        out.write(str(us_of(later)) + "|")


def case_registry(case, ctx, out):
    """What the registries hold, in their order: layouts and variants (the first is the
    default), scenes, pages and the weather glyphs."""
    from arthur.model import KINDS
    from arthur.render import idle_show, list_variants, pages

    parts = [k + "=" + ",".join(list_variants(k, (52, 16))) for k in KINDS]
    parts.append("scenes=" + ",".join(idle_show.scene_modules()))
    parts.append("registered=" + ",".join(sorted(idle_show.SCENES)))
    parts.append("pages=" + ",".join(sorted(idle_show.PAGES)))
    parts.append("glyphs=" + ",".join(pages.GLYPHS))
    out.write("|".join(parts))


def case_decimal(case, ctx, out):
    from arthur.compat import decimal_ratio
    from arthur.schedule import _share

    res = []
    for v, length in case["values"]:
        x = dec(v)
        n, d = decimal_ratio(x)
        res.append(f"{n}/{d}:{_share(length, x)}")
    out.write(" ".join(res))


def case_config(case, ctx, out):
    out.write(text(ctx.cfg))


CASES = {
    "frames": case_frames,
    "ascii": case_ascii,
    "scenario": case_scenario,
    "replay": case_replay,
    "tracker": case_tracker,
    "rng": case_rng,
    "zone": case_zone,
    "decimal": case_decimal,
    "config": case_config,
    "registry": case_registry,
}


def run(job_path, out_path):
    import gc

    load_all()
    gc.collect()
    with open(job_path) as job, open(out_path, "w") as out:
        ctx = Ctx(json.loads(job.readline()))
        for line in job:
            if not line.strip():
                continue
            case = json.loads(line)
            out.write(case["id"] + " ")
            try:
                CASES[case["kind"]](case, ctx, out)
            except Exception as e:  # noqa: BLE001 (reported as the case's result)
                out.write("ERROR " + type(e).__name__ + ": " + str(e).replace("\n", " "))
                if IS_MPY:
                    sys.print_exception(e)
                else:
                    import traceback

                    traceback.print_exc()
            out.write("\n")
            case = None
            gc.collect()
        if IS_MPY:
            out.write("#heap " + str(gc.mem_alloc() + gc.mem_free()) + "\n")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
