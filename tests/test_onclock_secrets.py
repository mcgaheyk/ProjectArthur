"""Nothing secret in any output of the program on the clock (CPython, in process).

The real ``arthur.onclock.main`` with the real source factory and the door, against the fake
Whisker over TLS, the repository's fake clock and a fake Open-Meteo, goes through a first
login (no tokens), a renewal, a refused renewal and a password login again. Then every
``ClockLog`` line, everything printed, ``status()``, every door answer and every file under
the root but ``secrets.json`` and ``tokens.json`` is searched for the password, the email,
the door key and every token value the fake issued. (The same search runs over the
MicroPython end-to-end run in ``tests/test_onclock_e2e.py``.)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from onclock_e2e import DOOR_KEY, PASSWORD, USERNAME, Fakes, free_port

from arthur.clocktool import config_bytes
from arthur.onclock import main as mainmod
from arthur.onclock.source import make_source
from arthur.tools.doorclient import DoorClient

pytestmark = pytest.mark.usefixtures("loopback_only")
REPO = Path(__file__).resolve().parents[1]


async def until(cond, timeout, what):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting for " + what)
        await asyncio.sleep(0.05)


async def test_no_secret_in_any_output(tmp_path, tls_files, capsys, monkeypatch):
    fakes = Fakes(tls_files)
    await fakes.start()
    root = tmp_path / "arthur"
    root.mkdir()
    (root / "config.json").write_bytes(config_bytes(REPO / "config" / "display.toml"))
    (root / "state.json").write_text(json.dumps({"v": 1, "active": "a",
                                                 "tries": {"a": 0, "b": 0}, "errors": {}}))
    (root / "secrets.json").write_text(json.dumps({"whisker_username": USERNAME,
                                                   "whisker_password": PASSWORD,
                                                   "door_key": DOOR_KEY.hex()}))
    port = free_port()
    settings = {"awtrix": f"http://127.0.0.1:{fakes.clock_port}",
                "ca_file": str(tls_files["ca"]), "server_name": "localhost",
                "weather_url": fakes.weather_url, "lr4_url": fakes.whisker.url + "/graphql",
                "cognito_url": fakes.whisker.url + "/", "door_port": port,
                "door_host": "127.0.0.1", "client": {"ws_backoff": [0.05, 0.2]}}
    answers: list[str] = []

    def door(verb: str, **kw) -> dict:
        with DoorClient("127.0.0.1", port, DOOR_KEY, timeout=10) as c:
            res = c.call({"verb": verb, **kw}) if kw else getattr(c, verb)()
        answers.append(json.dumps(res))
        return res

    w = fakes.whisker
    task = asyncio.create_task(mainmod.main(str(root), make_source, settings=settings))
    try:
        await until(lambda: w.subscribers, 30, "the first login")
        assert w.counts["login"] == 1
        await asyncio.to_thread(door, "status")
        w.revoke()  # a token refused: renewed
        await w.drop_sockets()
        await until(lambda: w.counts["refresh"] >= 1 and w.subscribers, 30, "a renewal")
        w.reject_refresh = True  # a renewal refused: the password again
        w.revoke()
        await w.drop_sockets()
        await until(lambda: w.counts["login"] >= 2 and w.subscribers, 30, "a re-login")
        await asyncio.sleep(0.5)
        app = mainmod._current["app"]
        statuses = [json.dumps(app.status())]
        await asyncio.to_thread(door, "status")
        await asyncio.to_thread(door, "logs", after=0)
        await asyncio.to_thread(door, "pause")
        await asyncio.to_thread(door, "resume")
        await until(lambda: w.subscribers, 30, "the connection after resume")
        await asyncio.to_thread(door, "logs", after=0)
        lines = [json.dumps(ln) for _, ln in app.log.read(0, 10**8)[0]]
        await asyncio.to_thread(door, "restart")
        assert await asyncio.wait_for(task, 20) == 42
    finally:
        if not task.done():
            task.cancel()
        await fakes.close()
    out = capsys.readouterr()
    secrets = [PASSWORD, USERNAME, DOOR_KEY.hex()]
    for t in fakes.issued:
        secrets += [v for v in t.values() if v]
    assert len(fakes.issued) >= 3 and w.counts["login"] == 2 and w.counts["refresh"] >= 1
    texts = {"log ring": "\n".join(lines), "stdout": out.out, "stderr": out.err,
             "status()": "\n".join(statuses), "door answers": "\n".join(answers)}
    for p in root.rglob("*"):
        if p.is_file() and p.name not in ("secrets.json", "tokens.json"):
            texts[str(p)] = p.read_text(errors="replace")
    assert len(lines) > 10 and "Whisker connected" in texts["log ring"]
    for name, text in texts.items():
        for s in secrets:
            assert s not in text, f"a secret ({s[:4]}...) in {name}"
    # And the two files that do hold them hold what they should.
    saved = json.loads((root / "tokens.json").read_text())
    assert saved["refresh_token"] == fakes.issued[-1]["refresh_token"]
