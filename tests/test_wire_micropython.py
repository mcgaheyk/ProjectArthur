"""The same wire client under the ``micropython`` binary (1.29, as on the clock) and under
CPython, against the fake Whisker: renew a token, load the robot, poll, receive the real
visits over the subscription, read the history. Both runs must hand on the same records and
history, equal to what the spike logged. One run is over TLS with verification on, and two
must fail verification.

Skips, saying why, when ``micropython`` is not installed. Never passes silently.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from wirefake import FakeWhisker, activity_entry
from wirehelp import real_lines, real_raws, server_ssl

pytestmark = pytest.mark.usefixtures("loopback_only")

SCRIPT = Path(__file__).with_name("wire_script.py").resolve()
MPY = shutil.which("micropython")
needs_mpy = pytest.mark.skipif(MPY is None, reason="micropython is not installed "
                               "(brew install micropython); the on-clock path is untested")


async def run_script(argv: list[str], fake: FakeWhisker, *, play=True, timeout=60.0):
    """Run the script; when it says ``subscribed``, push the real visits. Returns its lines."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[1])})
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
            if line["ev"] == "subscribed" and play:
                play = False
                for r in real_raws():
                    await fake.push(r)
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
    err = (await proc.stderr.read()).decode()
    return lines, proc.returncode, err


T0 = float(int(time.time()))  # one history for every run, so runs can be compared


async def start_fake(tls_files=None) -> FakeWhisker:
    fake = FakeWhisker(ka_s=0.5)
    fake.robots = [real_raws()[0]]
    t0 = T0
    for i in range(40):  # 80 entries: four pages of 25
        fake.activities.append(activity_entry(t0 - 1800 * i, "robotStatusCatDetect"))
        weight = round(4.6 + i / 100, 2)
        fake.activities.append(activity_entry(t0 - 1800 * i + 61, "catWeight", weight))
    await fake.start(ssl_context=server_ssl(tls_files) if tls_files else None)
    return fake


def tokens_file(fake: FakeWhisker, tmp_path: Path) -> str:
    path = tmp_path / f"tokens-{len(list(tmp_path.iterdir()))}.json"
    path.write_text(json.dumps(fake.issue_tokens(ttl=-60)))  # expired: the run renews it
    return str(path)


async def one_run(exe: list[str], tmp_path, tls_files=None, extra=()):
    fake = await start_fake(tls_files)
    try:
        argv = [*exe, str(SCRIPT), fake.url, tokens_file(fake, tmp_path),
                str(len(real_lines())), "--since", str(T0 - 30 * 3600), *extra]
        if tls_files is None:
            argv.append("--plain")
        lines, code, err = await run_script(argv, fake)
    finally:
        await fake.stop()
    assert code == 0, (lines[-3:], err[-2000:])
    return lines


def summary(lines):
    records = [(x["src"], x["record"]) for x in lines if x["ev"] == "record"]
    history = next(x["acts"] for x in lines if x["ev"] == "history")
    return records, history


def check(lines):
    records, history = summary(lines)
    assert [s for s, _ in records[:2]] == ["start", "poll"]
    ws = [r for s, r in records if s == "ws"]
    assert ws == [x["record"] for x in real_lines()]
    assert len(history) == 80  # every entry, through several pages
    done = next(x for x in lines if x["ev"] == "done")
    assert done["counts"]["refresh"] == 1


@needs_mpy
async def test_micropython_and_cpython_hand_on_the_same(tmp_path):
    mine = await one_run([MPY, "-X", "heapsize=2M"], tmp_path)
    theirs = await one_run([sys.executable], tmp_path)
    check(mine)
    check(theirs)
    assert summary(mine)[0] == summary(theirs)[0]
    a, b = summary(mine)[1], summary(theirs)[1]
    assert a == b, next(((i, x, y) for i, (x, y) in enumerate(zip(a, b, strict=False))
                         if x != y), (len(a), len(b)))


@needs_mpy
async def test_micropython_over_verified_tls(tmp_path, tls_files):
    lines = await one_run([MPY, "-X", "heapsize=2M"], tmp_path, tls_files,
                          ["--ca", str(tls_files["ca"]), "--server-name", "localhost"])
    check(lines)


@needs_mpy
@pytest.mark.parametrize("case", ["wrong_ca", "wrong_name", "no_ca"])
async def test_micropython_tls_refuses(tmp_path, tls_files, case):
    fake = await start_fake(tls_files)
    extra = {"wrong_ca": ["--ca", str(tls_files["wrong_ca"]), "--server-name", "localhost"],
             "wrong_name": ["--ca", str(tls_files["ca"]), "--server-name", "lr4.iothings.site"],
             "no_ca": []}[case]
    try:
        argv = [MPY, "-X", "heapsize=2M", str(SCRIPT), fake.url, tokens_file(fake, tmp_path),
                "1", *extra]
        lines, code, err = await run_script(argv, fake, play=False, timeout=30)
    finally:
        await fake.stop()
    assert code == 3, (lines, err[-2000:])
    assert lines[-1]["ev"] == "error" and lines[-1]["kind"] == "api"
    assert fake.counts["refresh"] == 0  # nothing got through
    print(case, lines[-1]["message"])


@needs_mpy
async def test_micropython_srp_login(tmp_path):
    """The whole password login under MicroPython, against the fake's Cognito SRP."""
    fake = await start_fake()
    try:
        argv = [MPY, "-X", "heapsize=2M", str(SCRIPT), fake.url, "-", "1", "--plain",
                "--login", fake.username + ":" + fake.password]
        lines, code, err = await run_script(argv, fake, timeout=60)
    finally:
        await fake.stop()
    assert code == 0, (lines[-3:], err[-2000:])
    token = next(x for x in lines if x["ev"] == "mem" and x["phase"] == "token")
    assert token["login"] == 1 and fake.counts["login"] == 1
    print("SRP login under MicroPython took", token["seconds"], "s")


@needs_mpy
def test_micropython_prints_floats_like_cpython():
    """MicroPython parses these to the same bits as CPython but its ``repr`` differs;
    ``util.float_text`` writes them as CPython does."""
    import subprocess

    values = ["0.07", "0.56", "0.81", "8.3", "8.05", "4.67", "-41.0", "82.0", "0.0", "100.0"]
    code = (f"import sys, json; sys.path.insert(0, {str(SCRIPT.parents[1])!r}); from arthur.wire import util; "
            "vs = json.loads(sys.argv[1]); print(json.dumps([[repr(v), util.float_text(v)] "
            "for v in vs]))")
    done = subprocess.run([MPY, "-c", code, "[" + ",".join(values) + "]"],
                          capture_output=True, text=True, check=True)
    got = json.loads(done.stdout)
    assert [t for _, t in got] == [repr(float(v)) for v in values]
    assert [r for r, _ in got][:3] != [repr(float(v)) for v in values][:3]  # repr really differs
