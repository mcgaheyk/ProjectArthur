"""How long the program's long jobs hold the event loop, under MicroPython on this Mac
(``tests/onclock_blockbench.py``): a password login, a renewal, the robot, 100 history
entries, the live connection, a weather fetch, a config reload and the door's work for one
request. The numbers are printed (``-s``); the clock is estimated at 25 to 30 times slower.

The assertion is loose on purpose: on this Mac no job may hold the loop for more than 40 ms
(a fifth of what it would take, at 25 to 30 times slower, to cost a beat or two on the
clock is beyond that; the figures say which job comes closest). Skips, saying so, without
``micropython``."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from onclock_e2e import PASSWORD, USERNAME, Fakes

from arthur.clocktool import config_bytes

pytestmark = pytest.mark.usefixtures("loopback_only")
MPY = shutil.which("micropython")
HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(MPY is None, reason="micropython is not on PATH: how long the program's "
                    "jobs block its loop was NOT measured (brew install micropython)")
async def test_how_long_each_job_holds_the_loop(tmp_path, tls_files):
    fakes = Fakes(tls_files)
    now = time.time()
    for i in range(100):
        fakes.add_history(now - 3600 * 24 + i * 600, "robotStatusCatDetect")
    await fakes.start()
    try:
        (tmp_path / "config.json").write_bytes(config_bytes(HERE.parent / "config" /
                                                            "display.toml"))
        job = {"ca": str(tls_files["ca"]), "user": USERNAME, "password": PASSWORD,
               "whisker": fakes.whisker.url, "weather": fakes.weather_url,
               "root": str(tmp_path), "since": now - 3600 * 25}
        (tmp_path / "job.json").write_text(json.dumps(job))
        proc = await asyncio.create_subprocess_exec(
            MPY, "-X", "heapsize=3M", str(HERE / "onclock_blockbench.py"),
            str(tmp_path / "job.json"), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=HERE.parent)
        out, err = await asyncio.wait_for(proc.communicate(), 120)
    finally:
        await fakes.close()
    assert proc.returncode == 0, (out.decode()[-2000:], err.decode()[-3000:])
    res = json.loads(out.decode().strip().splitlines()[-1])
    print("\nloop blocked, MicroPython on this Mac (the clock: x25 to x30):")
    for name, v in res.items():
        print(f"  {name}: {v['blocked_ms']} ms longest stretch, {v['total_ms']} ms in all; "
              f"clock est. {v['blocked_ms'] * 25:.0f} to {v['blocked_ms'] * 30:.0f} ms")
    assert len(res) == 11
    for name, v in res.items():
        assert v["blocked_ms"] < 40, (name, v)
