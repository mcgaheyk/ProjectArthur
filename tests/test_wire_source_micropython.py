"""``WireSource`` under the ``micropython`` binary (1.29, as on the clock) and under CPython,
through the same scripted scenarios against the fake Whisker (``tests/wire_scenarios.py``):
every view sampled, every log line and every console line must be the same, and so must the
characters of every number the log writes (MicroPython prints some floats differently unless
they go through ``arthur.wire.util.float_text``).

The heap the source needs is measured on the hour scenario (three visits, the routine
traffic) and printed. Skips, saying why, when ``micropython`` is not installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest
from test_wire_source import PASSWORD, perform, start_fake, write_token
from wire_scenarios import SCENARIOS

from arthur.tools.export_config import export

pytestmark = [pytest.mark.usefixtures("loopback_only"),
              pytest.mark.skipif(shutil.which("micropython") is None,
                                 reason="micropython is not on PATH: WireSource did NOT run "
                                        "under MicroPython (brew install micropython)")]

MPY = shutil.which("micropython")
SCRIPT = Path(__file__).with_name("wire_source_script.py").resolve()
#: The heap the MicroPython runs get (``-X heapsize``); the smallest the hour runs in is
#: measured by ``test_the_heap_an_hour_needs``.
HEAP = "1M"
NUMBER = re.compile(r"-?\d+\.\d+(?:e[-+]?\d+)?|-?\d+e[-+]?\d+")


async def run_script(exe: list[str], name: str, tmp_path: Path, heap: str = HEAP,
                     timeout: float = 240.0, ring: int | None = None):
    """One scripted run in a subprocess. Returns ``(lines, returncode, stderr)``."""
    sc = SCENARIOS[name]()
    fake = await start_fake(sc)
    run_dir = tmp_path / f"run-{len(list(tmp_path.iterdir()))}"
    run_dir.mkdir()
    token = run_dir / "token.json"
    write_token(sc, fake, token)
    job = run_dir / "job.json"
    job.write_text(json.dumps({"config": export(), "scenario": sc, "url": fake.url,
                               "token": str(token),
                               "credentials": [fake.username, PASSWORD], "ring": ring}))
    argv = [*exe, str(SCRIPT), str(job)]
    if exe[0] == MPY:
        argv = [MPY, "-X", f"heapsize={heap}", str(SCRIPT), str(job)]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[1])}, limit=1 << 22)
    lines = []
    end = time.monotonic() + timeout
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), max(0.1, end - time.monotonic()))
            if not raw:
                break
            try:
                line = json.loads(raw)
            except ValueError:  # a traceback: MicroPython prints them on stdout
                line = {"ev": "text", "text": raw.decode(errors="replace")}
            lines.append(line)
            if line["ev"] == "need":
                await perform(fake, sc, sc["events"][line["i"]])
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
        await fake.stop()
    err = (await proc.stderr.read()).decode()
    return lines, proc.returncode, err


def summary(lines):
    probes = [x["p"] for x in lines if x["ev"] == "probe"]
    states = [x["text"] for x in lines if x["ev"] == "state"]
    acts = [x["text"] for x in lines if x["ev"] == "activity"]
    console = [x["line"] for x in lines if x["ev"] == "console"]
    done = next(x for x in lines if x["ev"] == "done")
    return probes, states, acts, console, done


def numbers(texts):
    """Every number with a fraction or exponent in the lines, as written, sorted."""
    return sorted(m for t in texts for m in NUMBER.findall(t))


async def both(name, tmp_path):
    mine, code, err = await run_script([MPY], name, tmp_path)
    assert code == 0, ([x for x in mine if x["ev"] == "text"][-5:], err[-2000:])
    theirs, code, err = await run_script([sys.executable], name, tmp_path)
    assert code == 0, err[-2000:]
    return summary(mine), summary(theirs)


@pytest.mark.parametrize("name", sorted(set(SCENARIOS) - {"an_hour"}))
async def test_micropython_and_cpython_agree(name, tmp_path):
    (probes, states, acts, console, done), (p2, s2, a2, c2, d2) = await both(name, tmp_path)
    assert done["notes"] == d2["notes"]
    assert done["notes"] == [] or name == "two_robots"  # never subscribed: pushes unseen
    if probes != p2:
        i = next(i for i, (a, b) in enumerate(zip(probes, p2, strict=False)) if a != b)
        pytest.fail(f"view {i} differs: MicroPython {probes[i]} CPython {p2[i]}\n"
                    f"console {console} / {c2}")
    assert [json.loads(x) for x in states] == [json.loads(x) for x in s2]
    assert [json.loads(x) for x in acts] == [json.loads(x) for x in a2]
    assert numbers(states + acts) == numbers(s2 + a2)
    assert console == c2
    assert done["counts"] == d2["counts"]
    assert probes, "no view was sampled"


async def test_the_heap_an_hour_needs(tmp_path):
    """The hour's run under MicroPython, equal to CPython's, and the heap it took: what
    the source holds once connected and at the end (after a collection), and the smallest
    ``-X heapsize`` (in steps of 32 KB) it completes in."""
    (probes, states, _acts, _console, done), theirs = await both("an_hour", tmp_path)
    assert probes == theirs[0]
    assert [json.loads(x) for x in states] == [json.loads(x) for x in theirs[1]]
    heap = done["heap"]
    print("\nan hour under MicroPython, bytes in use:", heap)
    lo, hi = 128, 1024  # KB: fails at lo (or below), runs at hi
    while hi - lo > 32:
        mid = (lo + hi) // 2 // 32 * 32
        lines, code, _ = await run_script([MPY], "an_hour", tmp_path, heap=f"{mid}K")
        ok = code == 0 and summary(lines)[0] == probes
        lo, hi = (lo, mid) if ok else (mid, hi)
    print(f"smallest heap that runs the hour: {hi} KB (fails at {lo} KB)")
    assert hi <= 1024
    # The same hour keeping its log in memory, as the clock's default sink does.
    lines, code, _ = await run_script([MPY], "an_hour", tmp_path, ring=100)
    assert code == 0
    _, ring_states, ring_acts, _, ring_done = summary(lines)
    assert [json.loads(x) for x in ring_states] == [json.loads(x) for x in states][-100:]
    size = sum(len(x) for x in ring_states + ring_acts)
    print(f"with the log kept in memory ({len(ring_states)} lines, {size} characters):",
          ring_done["heap"])
