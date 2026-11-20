"""The robot source on the clock (``arthur/onclock/source.py``): the login from
``secrets.json``, the token file, the clock's time, the log ring, and a second run after a
pause. Against the fake Whisker on 127.0.0.1 (plain HTTP; the TLS path is the end-to-end
test's), in real time."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from wirefake import FakeWhisker, activity_entry

from arthur.config import default_config
from arthur.onclock.clocklog import ClockLog
from arthur.onclock.source import SANE_EPOCH, ClockSource, TokenStore, make_source
from arthur.wire.net import Tls

pytestmark = pytest.mark.usefixtures("loopback_only")

CFG = default_config()
PASSWORD = "hunter2-secret"


def make_root(tmp_path: Path, fake: FakeWhisker | None, *, secrets: object = None) -> str:
    root = tmp_path / "arthur"
    root.mkdir()
    if secrets is None and fake is not None:
        secrets = {"whisker_username": fake.username, "whisker_password": PASSWORD,
                   "door_key": "00" * 32}
    if secrets is not None:
        text = secrets if isinstance(secrets, str) else json.dumps(secrets)
        (root / "secrets.json").write_text(text)
    return str(root)


def source(fake: FakeWhisker | None, root: str, **kw) -> ClockSource:
    client = {"ws_backoff": (0.05, 0.2), "timeout": 5.0}
    if fake is not None:
        client.update(lr4_url=fake.url + "/graphql", cognito_url=fake.url + "/")
    wire_kw = dict(kw.pop("wire_kw", {}), client_kw=client)
    return make_source(cfg=CFG, log=ClockLog(), tls=Tls(allow_plain=True), root=root,
                       wire_kw=wire_kw, echo=False, **kw)


def console(src: ClockSource) -> list[str]:
    lines, _ = src.log.read(0, 10**7)
    return [ln["msg"] for _, ln in lines if ln.get("kind") == "console"]


async def until(cond, timeout: float = 10.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


class Running:
    def __init__(self, src: ClockSource) -> None:
        self.src = src
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(src.run(self.stop))

    async def end(self) -> None:
        self.stop.set()
        await asyncio.wait_for(self.task, 10)


@pytest.fixture
async def fakew():
    f = FakeWhisker(ka_s=0.2, password=PASSWORD)
    await f.start()
    yield f
    await f.stop()


def tokens_file(root: str) -> Path:
    return Path(root) / "tokens.json"


async def test_first_start_logs_in_and_saves_the_tokens_once(tmp_path, fakew):
    root = make_root(tmp_path, fakew)
    src = source(fakew, root)
    run = Running(src)
    await until(lambda: src.view(src.inner._wall())[0] is not None if src.inner else False)
    await run.end()
    assert fakew.counts["login"] == 1
    saved = json.loads(tokens_file(root).read_text())
    assert set(saved) == {"access_token", "id_token", "refresh_token"}
    assert saved["refresh_token"] in fakew.refresh_tokens
    assert src.tokens.writes == 1
    assert "Whisker connected" in console(src)


async def test_a_renewal_does_not_write_the_flash(tmp_path, fakew):
    root = make_root(tmp_path, fakew)
    fakew.token_ttl = 31.5  # valid for 1.5 s past the client's 30 s margin
    TokenStore(root).save(fakew.issue_tokens())
    stamp = os.stat(tokens_file(root)).st_mtime_ns
    src = source(fakew, root)
    run = Running(src)
    await until(lambda: src.inner is not None and src.inner.last_contact_at is not None)
    await asyncio.sleep(1.7)
    await fakew.drop_sockets()  # the live connection comes back with a renewed token
    await until(lambda: fakew.counts["refresh"] >= 1)
    await until(lambda: fakew.counts["ws"] >= 2)
    await run.end()
    assert fakew.counts["login"] == 0
    assert src.tokens.writes == 0
    assert os.stat(tokens_file(root)).st_mtime_ns == stamp


async def test_a_rejected_renewal_logs_in_again_and_saves_the_new_tokens(tmp_path, fakew):
    root = make_root(tmp_path, fakew)
    fakew.token_ttl = 31.5
    TokenStore(root).save(fakew.issue_tokens())
    src = source(fakew, root)
    run = Running(src)
    await until(lambda: src.inner is not None and src.inner.last_contact_at is not None)
    fakew.reject_refresh = True
    await asyncio.sleep(1.7)
    await fakew.drop_sockets()
    await until(lambda: fakew.counts["login"] >= 1)
    await until(lambda: src.tokens.writes == 1)
    await run.end()
    assert json.loads(tokens_file(root).read_text())["refresh_token"] in fakew.refresh_tokens


@pytest.mark.parametrize("secrets", ["not json", {"door_key": "00"}, {"whisker_username": "a",
                                                                     "whisker_password": ""}])
async def test_without_a_login_no_data_auth_and_slow_looks(tmp_path, secrets):
    root = make_root(tmp_path, None, secrets=secrets)
    waits: list[float] = []

    async def sleep(stop, s):
        waits.append(s)
        return len(waits) >= 3

    src = source(None, root, sleep=sleep)
    await asyncio.wait_for(src.run(asyncio.Event()), 5)
    snap, health = src.view(None)
    assert snap is None and health.error == "auth"
    assert waits == [600.0, 1200.0, 2400.0]
    assert src.inner is None
    text = json.dumps(src.log.read(0, 10**7)[0])
    assert "door_key" not in text and "not json" not in text


async def test_a_missing_secrets_file_is_looked_for_again(tmp_path, fakew):
    root = make_root(tmp_path, None)
    waits: list[float] = []

    async def sleep(stop, s):
        waits.append(s)
        (Path(root) / "secrets.json").write_text(json.dumps(
            {"whisker_username": fakew.username, "whisker_password": PASSWORD}))
        return False

    src = source(fakew, root, sleep=sleep)
    run = Running(src)
    await until(lambda: src.inner is not None and src.inner.last_contact_at is not None)
    await run.end()
    assert waits == [600.0]
    assert src.view(src.inner._wall())[1].error is None


async def test_a_wrong_password_is_retried_slowly(tmp_path, fakew):
    root = make_root(tmp_path, fakew, secrets={"whisker_username": fakew.username,
                                               "whisker_password": "wrong"})
    waits: list[float] = []

    async def sleep(stop, s):
        waits.append(s)
        return len(waits) >= 3

    src = source(fakew, root, wire_kw={"sleep": sleep})
    await asyncio.wait_for(src.run(asyncio.Event()), 30)
    assert waits == [600.0, 1200.0, 2400.0]
    assert fakew.counts["login"] == 3  # one attempt per wait, never a tight loop
    assert src.view(src.inner._wall())[1].error == "auth"
    assert "wrong" not in json.dumps(src.log.read(0, 10**7)[0])


async def test_waits_for_the_clocks_time_before_any_login(tmp_path, fakew):
    root = make_root(tmp_path, fakew)
    t = {"now": 1000.0}
    waits: list[float] = []

    async def sleep(stop, s):
        waits.append(s)
        if len(waits) == 3:
            t["now"] = None  # the time arrives
        await asyncio.sleep(0)
        return False

    def epoch():
        import time

        return time.time() if t["now"] is None else t["now"]

    src = source(fakew, root, epoch_fn=epoch, sleep=sleep)
    run = Running(src)
    await until(lambda: src.inner is not None and src.inner.last_contact_at is not None)
    await run.end()
    assert waits[:3] == [2.0, 2.0, 2.0]
    assert fakew.counts["login"] == 1 and len(fakew.requests) > 0
    msgs = console(src)
    assert any("time is not set" in m for m in msgs)
    assert any("time is set" in m for m in msgs)
    assert SANE_EPOCH > 1000


async def test_time_not_set_means_no_request_at_all(tmp_path, fakew):
    root = make_root(tmp_path, fakew)
    stop = asyncio.Event()
    src = source(fakew, root, epoch_fn=lambda: 5.0)
    task = asyncio.create_task(src.run(stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert fakew.requests == [] and src.inner is None


async def test_a_second_run_reads_the_history_as_after_a_gap(tmp_path, fakew):
    """A pause from the Mac stops the source; on resume nothing old is shown, the first
    contact ends a gap, and the history (where the Mac's watching found a visit) is read."""
    root = make_root(tmp_path, fakew)
    src = source(fakew, root)
    run = Running(src)
    await until(lambda: src.inner is not None and src.inner.last_contact_at is not None)
    await run.end()
    reads = sum(1 for r in fakew.requests
                if r["json"] and "getLitterRobot4Activity" in str(r["json"].get("query")))
    import time

    fakew.activities.append(activity_entry(time.time() - 5, "robotStatus", "CAT_DETECTED"))
    assert src.view(src.inner._wall())[0] is not None
    run = Running(src)
    await asyncio.sleep(0)
    assert src.view(src.inner._wall())[0] is None  # nothing old while reconnecting
    await until(lambda: src.inner.last_contact_at is not None)
    await until(lambda: any("Not watching from" in m for m in console(src)))
    await until(lambda: sum(1 for r in fakew.requests if r["json"] and
                            "getLitterRobot4Activity" in str(r["json"].get("query"))) > reads)
    await run.end()
    assert src.runs == 2


async def test_log_lines_have_the_shapes_logs_pull_sorts(tmp_path, fakew):
    import time

    from arthur.clocktool import ACTIVITY_KINDS, SPIKE_KINDS

    fakew.activities.append(activity_entry(time.time() - 3600, "robotStatus", "CAT_DETECTED"))
    root = make_root(tmp_path, fakew)
    src = source(fakew, root)
    run = Running(src)
    await until(lambda: any(ln.get("kind") == "activity"
                            for _, ln in src.log.read(0, 10**7)[0]))
    await run.end()
    kinds = {ln["kind"] for _, ln in src.log.read(0, 10**7)[0]}
    assert kinds <= set(SPIKE_KINDS) | set(ACTIVITY_KINDS) | {"console"}
    assert {"state", "info", "activity", "console"} <= kinds
    for _, ln in src.log.read(0, 10**7)[0]:
        if ln["kind"] == "console":
            assert ln["name"] == "arthur.whisker" and ln["ts"].endswith("Z")
