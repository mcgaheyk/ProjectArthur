"""The clock's robot source under MicroPython (``tests/onclock_source_script.py``): a first
login, a stop that must close the live connection (MicroPython 1.29 can raise again at an
await in a cancelled task's ``finally``, which left the socket open before ``close_now``),
and a second run that shows nothing old and reads the history as after a gap. Skips, saying
so, without ``micropython``."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from wirefake import FakeWhisker, activity_entry

from arthur.clocktool import config_bytes

pytestmark = pytest.mark.usefixtures("loopback_only")
MPY = shutil.which("micropython")
HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(MPY is None, reason="micropython is not on PATH: the clock's source was "
                    "NOT run as the clock runs it (brew install micropython)")
async def test_the_clock_source_under_micropython(tmp_path):
    fake = FakeWhisker(ka_s=0.5, password="pw-not-real")
    await fake.start()
    root = tmp_path / "arthur"
    root.mkdir()
    (root / "config.json").write_bytes(config_bytes(HERE.parent / "config" / "display.toml"))
    (root / "secrets.json").write_text(json.dumps({"whisker_username": fake.username,
                                                   "whisker_password": "pw-not-real"}))
    proc = await asyncio.create_subprocess_exec(
        MPY, "-X", "heapsize=3M", str(HERE / "onclock_source_script.py"),
        fake.url.rsplit(":", 1)[1], str(root), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    events: list[dict] = []

    async def until(cond, what):
        end = time.monotonic() + 20
        while not cond():
            if time.monotonic() > end:
                raise AssertionError("timed out: " + what)
            await asyncio.sleep(0.05)

    async def read():
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            try:
                events.append(json.loads(raw))
            except ValueError:
                events.append({"ev": "text", "text": raw.decode(errors="replace")})

    reader = asyncio.create_task(read())
    try:
        await until(lambda: any(e.get("ev") == "connected" for e in events), "connected")
        assert len(fake.subscribers) == 1
        fake.step = 1
        await until(lambda: any(e.get("ev") == "stopped" for e in events), "stopped")
        await until(lambda: not fake.subscribers, "the live connection closed on stop")
        fake.activities.append(activity_entry(time.time() - 30, "robotStatusCatDetect"))
        fake.step = 2
        await until(lambda: sum(e.get("ev") == "stopped" for e in events) == 2, "stopped 2")
        await until(lambda: not fake.subscribers, "closed again")
        fake.step = 3
        await asyncio.wait_for(proc.wait(), 20)
        await reader
    finally:
        if proc.returncode is None:
            proc.kill()
        await fake.stop()
    err = (await proc.stderr.read()).decode()
    assert proc.returncode == 0, (events[-5:], err[-2000:])
    end = next(e for e in events if e["ev"] == "end")
    second = next(e for e in events if e["ev"] == "second run begins")
    assert second["snapshot"] is False  # nothing old while the second run connects
    assert end["runs"] == 2 and end["tokens_writes"] == 1
    assert fake.counts["login"] == 1 and fake.counts["ws"] == 2
    assert any(m.startswith("Not watching from") for m in end["console"]), end["console"]
    assert any(m.startswith(("Last visit set to", "Last visit moved"))
               for m in end["console"]), end["console"]
