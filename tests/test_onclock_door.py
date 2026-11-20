"""The update door (arthur/onclock/door.py) against the stub program, on CPython, and the
same door under the ``micropython`` binary (as on the clock)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from onclockhelp import KEY, REPO, WRONG_KEY, DoorThread, free_port, make_root, manifest_of

from arthur.onclock import door as d
from arthur.tools.doorclient import DoorClient, DoorFailed, DoorRefused

pytestmark = pytest.mark.usefixtures("loopback_only")

MPY = shutil.which("micropython")


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


@pytest.fixture
def door(root):
    t = DoorThread(root)
    yield t
    t.stop()


def client(t: DoorThread, key: bytes = KEY) -> DoorClient:
    return DoorClient("127.0.0.1", t.port, key, timeout=10)


def raw_connect(port: int) -> tuple[socket.socket, bytes]:
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    f = s.makefile("rb")
    line = f.readline()
    return s, line


def signed(nonce: bytes, head: dict, body: bytes = b"", key: bytes = KEY) -> bytes:
    h = json.dumps(head).encode()
    hdr = struct.pack(">HI", len(h), len(body))
    tag = hmac.new(key, d.REQ_TAG + nonce + hdr + h + body, hashlib.sha256).digest()
    return hdr + h + body + tag


def read_all(s: socket.socket) -> bytes:
    out = b""
    while True:
        try:
            chunk = s.recv(4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


def state(root: Path) -> dict:
    return json.loads((root / "state.json").read_text())


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# -- authentication --------------------------------------------------------------------------


def test_status_is_signed_and_reports_the_slots(door, root):
    with client(door) as c:
        st = c.status()
    assert st["ok"] and st["door"] == 1
    assert st["running"] == "a" and st["active"] == "a"
    assert st["slots"]["a"] == d.slot_id(str(root), "a") and st["slots"]["b"] is None
    assert st["app"]["paused"] is False
    assert len(st["config"]) == 16


def test_wrong_key_gets_only_the_fixed_refusal(door):
    s, line = raw_connect(door.port)
    nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
    s.sendall(signed(nonce, {"verb": "status"}, key=WRONG_KEY))
    assert read_all(s) == d.REFUSAL
    s.close()
    with pytest.raises(DoorRefused):
        client(door, WRONG_KEY).status()
    assert door.app.paused is False


def test_a_recorded_request_cannot_be_replayed(door):
    s, line = raw_connect(door.port)
    nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
    req = signed(nonce, {"verb": "pause"})
    s.sendall(req)
    s.settimeout(5)
    first = s.recv(4)
    assert first != d.REFUSAL[:4]  # the real one worked
    assert wait_for(lambda: door.app.paused)
    door.app.resume()
    # Again on a new connection: a new challenge, so the old tag is wrong.
    s2, _ = raw_connect(door.port)
    s2.sendall(req)
    assert read_all(s2) == d.REFUSAL
    s2.close()
    # Again on the same connection: the challenge moved on with the answer.
    read = b""
    s.settimeout(1)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            read += chunk
    except OSError:
        pass
    s.settimeout(5)
    s.sendall(req)
    assert read_all(s).endswith(d.REFUSAL)
    s.close()
    assert door.app.paused is False


def test_a_tag_for_an_old_challenge_is_refused(door):
    """There is no timestamp to go stale: a request made for an earlier challenge (the
    equivalent of a stale one) is refused, and the clock's own time plays no part."""
    with client(door) as c:
        old = c.nonce
        c.status()
        c.nonce = old
        with pytest.raises(DoorRefused):
            c.status()


def test_the_clocks_time_does_not_matter(door, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 0.0)  # 1970 on both sides
    with client(door) as c:
        assert c.status()["ok"]


def test_constant_time_compare():
    assert d.ct_equal(b"abc", b"abc")
    assert not d.ct_equal(b"abc", b"abd")
    assert not d.ct_equal(b"abc", b"ab")


def test_a_response_with_a_bad_tag_is_rejected_by_the_mac(door):
    with client(door) as c:
        c.key = WRONG_KEY  # the request is now refused by the door
        with pytest.raises(DoorRefused):
            c.status()


# -- limits and garbage ----------------------------------------------------------------------


@pytest.mark.parametrize("hlen,blen", [(0, 0), (d.MAX_HEAD + 1, 0), (10, d.MAX_BODY + 1),
                                       (65535, 0xFFFFFFFF)])
def test_oversize_announcements_are_refused_before_reading(door, hlen, blen):
    s, _ = raw_connect(door.port)
    t0 = time.monotonic()
    s.sendall(struct.pack(">HI", hlen, blen))
    assert read_all(s) == d.REFUSAL
    assert time.monotonic() - t0 < 2
    s.close()


def test_an_oversize_head_with_a_good_tag_is_still_refused(door):
    s, line = raw_connect(door.port)
    nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
    s.sendall(signed(nonce, {"verb": "status", "pad": "x" * d.MAX_HEAD}))
    assert read_all(s) == d.REFUSAL
    s.close()


def test_garbage_is_refused(door):
    s, _ = raw_connect(door.port)
    s.sendall(b"\x00\x10\x00\x00\x00\x00" + os.urandom(16) + b"\x00" * 32)
    assert read_all(s) == d.REFUSAL
    s.close()
    s, _ = raw_connect(door.port)
    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert read_all(s) in (d.REFUSAL, b"")
    s.close()


def test_authenticated_nonsense_gets_an_error_not_a_crash(door):
    with client(door) as c:
        assert c.call({"verb": "nope"}, check=False)["error"] == "unknown verb"
        assert c.call({"verb": "logs", "after": -1}, check=False)["ok"] is False
        assert c.call({"verb": "logs", "after": True}, check=False)["ok"] is False
        assert c.status()["ok"]  # the connection still works


def test_more_connections_than_allowed_are_closed(door):
    held = [raw_connect(door.port) for _ in range(d.MAX_CONNECTIONS)]
    s = socket.create_connection(("127.0.0.1", door.port), timeout=5)
    assert read_all(s) == b""
    s.close()
    for h, _ in held:
        h.close()
    assert wait_for(lambda: door.door.conns == 0)
    with client(door) as c:
        assert c.status()["ok"]


# -- config ----------------------------------------------------------------------------------


def test_config_put_replaces_the_file_and_reloads(door, root):
    before = door.app.config_loads
    new = json.dumps({"idle": {"page_pct": 10}}).encode()
    with client(door) as c:
        res = c.config_put(new)
        assert res["config"] == hashlib.sha256(new).hexdigest()[:16]
        assert c.status()["config"] == res["config"]
    assert (root / "config.json").read_bytes() == new
    assert door.app.config_loads == before + 1
    assert not (root / "config.json.new").exists()


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_config_put_refuses_what_is_not_a_config(door, root, body):
    old = (root / "config.json").read_bytes()
    before = door.app.config_loads
    with client(door) as c, pytest.raises(DoorFailed):
        c.config_put(body)
    assert (root / "config.json").read_bytes() == old
    assert door.app.config_loads == before


def test_config_put_checks_with_the_real_config_code(root):
    t = DoorThread(root, validate=None)  # the default: arthur.config.config_from_json
    try:
        from arthur.clocktool import config_bytes

        good = config_bytes()
        with client(t) as c:
            c.config_put(good)
            with pytest.raises(DoorFailed, match="config refused"):
                c.config_put(json.dumps({"colours": {"nope": 1}}).encode())
        assert (root / "config.json").read_bytes() == good
    finally:
        t.stop()


# -- bundles ---------------------------------------------------------------------------------


def files_v2() -> dict[str, bytes]:
    return {"arthur/__init__.py": b"# v2\n", "arthur/onclock/main.py": b"x = 2\n",
            "lib/typing.py": b"# stand-in\n", "arthur/render/fonts/f.bdf": b"F" * 70000}


def send(c: DoorClient, files: dict[str, bytes], tmp: Path, config: bytes | None = None):
    folder = tmp / "send"
    shutil.rmtree(folder, ignore_errors=True)
    for p, b in files.items():
        (folder / p).parent.mkdir(parents=True, exist_ok=True)
        (folder / p).write_bytes(b)
    return c.bundle(folder, manifest_of(files), config=config)


def test_a_bundle_goes_to_the_inactive_slot_and_asks_for_a_restart(door, root, tmp_path):
    (root / "slots" / "b" / "stale.py").write_text("old")
    (root / "slots" / "b" / "old_dir").mkdir()
    (root / "slots" / "b" / "old_dir" / "x").write_text("old")
    a_before = {p: p.read_bytes() for p in (root / "slots" / "a").rglob("*") if p.is_file()}
    with client(door) as c:
        res = send(c, files_v2(), tmp_path)
        assert res["slot"] == "b" and res["files_sent"] == 4
        st = c.status()
    assert st["active"] == "b" and st["running"] == "a" and st["restart_pending"]
    assert st["slots"]["b"] == res["bundle"]
    assert wait_for(lambda: door.app._code == 42)
    b = root / "slots" / "b"
    assert sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file()) == sorted(
        [*files_v2(), "manifest.json"])
    for p, data in files_v2().items():
        assert (b / p).read_bytes() == data
    assert state(root)["active"] == "b" and state(root)["tries"]["b"] == 0
    assert {p: p.read_bytes() for p in (root / "slots" / "a").rglob("*") if p.is_file()} \
        == a_before
    # A second bundle waits for the restart.
    with client(door) as c, pytest.raises(DoorFailed, match="waiting for the restart"):
        send(c, files_v2(), tmp_path)


def test_only_missing_files_are_sent(door, root, tmp_path):
    files = files_v2()
    b = root / "slots" / "b"
    for p in ("lib/typing.py", "arthur/render/fonts/f.bdf"):
        (b / p).parent.mkdir(parents=True, exist_ok=True)
        (b / p).write_bytes(files[p])
    with client(door) as c:
        res = send(c, files, tmp_path)
    assert res["files_sent"] == 2


def test_a_bundle_can_carry_the_config(door, root, tmp_path):
    cfg = json.dumps({"new": True}).encode()
    with client(door) as c:
        send(c, files_v2(), tmp_path, config=cfg)
    assert (root / "config.json").read_bytes() == cfg


def test_a_wrong_hash_never_becomes_active(door, root, tmp_path):
    files = files_v2()
    manifest = manifest_of(files)
    with client(door) as c:
        begin = c.call({"verb": "bundle-begin"}, manifest)
        path = "arthur/onclock/main.py"
        with pytest.raises(DoorFailed, match="hash mismatch"):
            c.call({"verb": "bundle-file", "session": begin["session"], "path": path,
                    "offset": 0}, b"y = 3\n")
        with pytest.raises(DoorFailed, match="missing"):
            c.call({"verb": "bundle-commit", "session": begin["session"]})
    assert state(root)["active"] == "a"
    assert not (root / "slots" / "b" / path).exists()
    assert not (root / "slots" / "b" / (path + ".part")).exists()
    assert not (root / "slots" / "b" / "manifest.json").exists()


def test_a_file_longer_than_its_manifest_is_refused(door, tmp_path):
    files = files_v2()
    with client(door) as c:
        begin = c.call({"verb": "bundle-begin"}, manifest_of(files))
        with pytest.raises(DoorFailed, match="longer"):
            c.call({"verb": "bundle-file", "session": begin["session"],
                    "path": "lib/typing.py", "offset": 0}, b"x" * 100)
        with pytest.raises(DoorFailed, match="offset"):
            c.call({"verb": "bundle-file", "session": begin["session"],
                    "path": "lib/typing.py", "offset": 5}, b"x")


def test_a_transfer_cut_off_half_way_leaves_the_active_slot_alone(door, root, tmp_path):
    files = files_v2()
    manifest = manifest_of(files)
    a_id = d.slot_id(str(root), "a")
    c = client(door)
    begin = c.call({"verb": "bundle-begin"}, manifest)
    c.call({"verb": "bundle-file", "session": begin["session"],
            "path": "arthur/render/fonts/f.bdf", "offset": 0}, files["arthur/render/fonts/f.bdf"][:24576])
    c.close()  # the cable is pulled, so to speak
    st = state(root)
    assert st["active"] == "a" and d.slot_id(str(root), "a") == a_id
    assert d.slot_id(str(root), "b") is None  # the half-done slot is not usable
    with client(door) as c2:
        assert c2.status()["active"] == "a"
        res = send(c2, files, tmp_path)  # the same bundle again takes over at once
    assert res["slot"] == "b"
    assert (root / "slots" / "b" / "arthur/render/fonts/f.bdf").read_bytes() \
        == files["arthur/render/fonts/f.bdf"]


def test_two_transfers_at_once(door, tmp_path):
    one = files_v2()
    two = dict(files_v2(), **{"arthur/onclock/main.py": b"x = 3\n"})
    with client(door) as c1, client(door) as c2:
        c1.call({"verb": "bundle-begin"}, manifest_of(one))
        with pytest.raises(DoorFailed, match="busy"):
            c2.call({"verb": "bundle-begin"}, manifest_of(two))
        with pytest.raises(DoorFailed, match="no such transfer"):
            c2.call({"verb": "bundle-commit", "session": "0000"})


def test_a_stale_transfer_can_be_taken_over(door, monkeypatch, tmp_path):
    with client(door) as c1:
        c1.call({"verb": "bundle-begin"}, manifest_of(files_v2()))
    door.door.session.touched -= d.SESSION_IDLE_S + 1
    two = dict(files_v2(), **{"arthur/onclock/main.py": b"x = 3\n"})
    with client(door) as c2:
        assert send(c2, two, tmp_path)["slot"] == "b"


BAD_PATHS = ["../evil.py", "/etc/passwd", "arthur/../../evil.py", "arthur//x.py", "./x.py",
             ".hidden", "arthur/.x", "manifest.json", "x.py.part", "a\\b.py", "a b.py",
             "x" * 121, "/".join("d" * 7) + ".py", "", "arthur/é.py", "a/./b.py"]


@pytest.mark.parametrize("path", BAD_PATHS)
def test_path_tricks_are_refused(door, root, path):
    manifest = json.dumps({"v": 1, "files": {path: ["0" * 64, 1]}}).encode()
    with client(door) as c, pytest.raises(DoorFailed, match="not allowed"):
        c.call({"verb": "bundle-begin"}, manifest)


def test_a_file_cannot_also_be_a_folder(door):
    manifest = manifest_of({"a/b": b"1", "a/b/c": b"2"})
    with client(door) as c, pytest.raises(DoorFailed, match="both a file and a folder"):
        c.call({"verb": "bundle-begin"}, manifest)


@pytest.mark.parametrize("manifest", [b"nope", b"[]", b'{"v": 2, "files": {}}',
                                      b'{"v": 1, "files": {}}',
                                      b'{"v": 1, "files": {"a.py": ["zz", 1]}}',
                                      b'{"v": 1, "files": {"a.py": ["' + b"0" * 64 + b'", -1]}}',
                                      b'{"v": 1, "files": {"a.py": ["' + b"0" * 64
                                      + b'", 999999999}}'])
def test_bad_manifests_are_refused(door, manifest):
    with client(door) as c, pytest.raises(DoorFailed):
        c.call({"verb": "bundle-begin"}, manifest)


def test_links_in_the_slot_are_replaced_never_followed(door, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.py").write_text("precious")
    b = root / "slots" / "b"
    (b / "arthur").symlink_to(outside)  # a folder that is a link
    (b / "lib").mkdir()
    (b / "lib" / "typing.py").symlink_to(outside / "target.py")  # a file that is a link
    (b / "lib" / "typing.py.part").symlink_to(outside / "part")  # a part file that is a link
    (b / "elsewhere").symlink_to(outside)  # a link the manifest does not name
    with client(door) as c:
        send(c, files_v2(), tmp_path)
    assert (outside / "target.py").read_text() == "precious"
    assert sorted(p.name for p in outside.iterdir()) == ["target.py"]
    assert not (b / "arthur").is_symlink() and (b / "arthur").is_dir()
    assert not (b / "lib" / "typing.py").is_symlink()
    assert not (b / "elsewhere").exists() and not (b / "elsewhere").is_symlink()


def test_a_slot_folder_that_is_a_link_is_refused(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(root / "slots" / "b")
    (root / "slots" / "b").symlink_to(outside)
    t = DoorThread(root)
    try:
        with client(t) as c, pytest.raises(DoorFailed, match="real folder"):
            c.call({"verb": "bundle-begin"}, manifest_of(files_v2()))
    finally:
        t.stop()
    assert list(outside.iterdir()) == []


def test_not_enough_flash_is_refused(root, tmp_path):
    t = DoorThread(root, reserve=1 << 60)
    try:
        with client(t) as c, pytest.raises(DoorFailed, match="free flash"):
            send(c, files_v2(), tmp_path)
    finally:
        t.stop()
    assert state(root)["active"] == "a"


# -- the rest of the verbs ---------------------------------------------------------------------


def test_pause_resume_and_restart(door, root):
    st = state(root)
    st["tries"]["a"] = 2
    (root / "state.json").write_text(json.dumps(st))
    with client(door) as c:
        assert c.pause()["paused"] is True
        assert door.app.paused
        assert c.status()["app"]["paused"] is True
        assert c.resume()["paused"] is False
        assert not door.app.paused
        c.restart()
    assert wait_for(lambda: door.app._code == 42)
    assert state(root)["tries"]["a"] == 0


def test_logs_page_through_the_memory_log(door):
    for i in range(300):
        door.app.log.append({"kind": "console", "msg": f"line {i}"})
    seen = []
    after = 0
    with client(door) as c:
        while True:
            res = c.logs(after, most=2000)
            if not res["lines"]:
                break
            seen.extend(line["msg"] for _, line in res["lines"] if "msg" in line)
            after = res["next"]
    assert [m for m in seen if m.startswith("line")] == [f"line {i}" for i in range(300)]


def test_the_key_comes_from_secrets_json(root):
    assert d.load_key(str(root)) == KEY
    (root / "secrets.json").write_text(json.dumps({"door_key": "abc"}))
    with pytest.raises(d.DoorError):
        d.load_key(str(root))
    (root / "secrets.json").unlink()
    with pytest.raises(d.DoorError):
        d.load_key(str(root))


# -- the same door under MicroPython ----------------------------------------------------------

DRIVER = """
import sys
sys.path.insert(0, {repo!r} + "/mpy/lib")
sys.path.insert(0, {repo!r})
sys.path.insert(0, {tests!r})
import asyncio
import clockstub
sys.exit(asyncio.run(clockstub.main({root!r}, None)))
"""


@pytest.mark.skipif(MPY is None, reason="micropython is not installed (brew install "
                    "micropython): the door was NOT tested as the clock runs it")
def test_the_door_under_micropython(tmp_path):
    root = make_root(tmp_path)
    port = free_port()
    (root / "test-port").write_text(str(port))
    drv = tmp_path / "driver.py"
    drv.write_text(DRIVER.format(repo=str(REPO), tests=str(REPO / "tests"), root=str(root)))
    proc = subprocess.Popen([MPY, "-X", "heapsize=2M", str(drv)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=tmp_path)
    try:
        c = None
        end = time.monotonic() + 20
        while c is None and time.monotonic() < end:
            try:
                c = DoorClient("127.0.0.1", port, KEY, timeout=20)
            except OSError:
                time.sleep(0.1)
        assert c is not None, "the door under MicroPython never answered"
        with c:
            st = c.status()
            assert st["running"] == "a" and st["app"]["config_loads"] == 1
            cfg = b'{"from": "micropython test"}'
            c.config_put(cfg)
            assert c.status()["app"]["config_loads"] == 2
            assert c.pause()["paused"] is True
            with pytest.raises(DoorFailed, match="not allowed"):
                c.call({"verb": "bundle-begin"},
                       json.dumps({"v": 1, "files": {"../x": ["0" * 64, 1]}}).encode())
            res = send(c, files_v2(), tmp_path)
            assert res["slot"] == "b"
        assert (root / "config.json").read_bytes() == cfg
        with pytest.raises(DoorRefused):
            DoorClient("127.0.0.1", port, WRONG_KEY, timeout=20).status()
        assert proc.wait(20) == 42  # the bundle asked for a restart
    finally:
        if proc.poll() is None:
            proc.kill()
        out = proc.stdout.read().decode(errors="replace")
    assert "Traceback" not in out, out
    assert state(root)["active"] == "b"
    for p, data in files_v2().items():
        assert (root / "slots" / "b" / p).read_bytes() == data
    assert sys.implementation.name == "cpython"


# -- the door's memory: nothing unauthenticated allocates more than a few bytes -------------


def test_a_bad_tag_on_a_full_body_gets_the_refusal(door):
    s, line = raw_connect(door.port)
    nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
    s.sendall(signed(nonce, {"verb": "status"}, os.urandom(d.MAX_BODY), key=WRONG_KEY))
    assert read_all(s) == d.REFUSAL
    s.close()
    with client(door) as c:
        assert c.status()["ok"]


def test_a_request_in_tiny_pieces_still_works(door):
    s, line = raw_connect(door.port)
    nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
    data = signed(nonce, {"verb": "config-put"}, b'{"idle": {"page_pct": 5}}')
    for i in range(len(data)):
        s.sendall(data[i:i + 1])
    f = s.makefile("rb")
    (n,) = struct.unpack(">I", f.read(4))
    assert json.loads(f.read(n))["ok"] is True
    s.close()


def test_one_request_at_a_time_and_a_slow_one_is_cut_off(door, monkeypatch):
    """A request whose header has come in holds the one buffer until it is whole; one that
    dawdles is cut off after the request deadline, and the others carry on."""
    monkeypatch.setattr(d, "REQUEST_DEADLINE_S", 1.0)
    slow, _ = raw_connect(door.port)
    slow.sendall(struct.pack(">HI", 20, 1000) + b"x" * 10)  # then nothing
    time.sleep(0.2)
    t0 = time.monotonic()
    with client(door) as c:
        assert c.status()["ok"]
    waited = time.monotonic() - t0
    assert 0.5 < waited < 5, waited  # it waited for the slow one's deadline, no longer
    assert read_all(slow) in (b"", d.REFUSAL)
    slow.close()
    assert wait_for(lambda: door.door.conns == 0)


def test_the_request_buffer_is_made_once(root):
    t = DoorThread(root)
    try:
        buf = t.door._buf
        assert len(buf) == d.MAX_HEAD + d.MAX_BODY
        with client(t) as c:
            for _ in range(3):
                c.status()
        s, line = raw_connect(t.port)
        nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
        s.sendall(signed(nonce, {"verb": "status"}, b"y" * 1000, key=WRONG_KEY))
        assert read_all(s) == d.REFUSAL
        s.close()
        assert t.door._buf is buf
    finally:
        t.stop()


HEAP_DRIVER = """
import sys
sys.path.insert(0, {repo!r})
sys.path.insert(0, {tests!r})
import asyncio, gc
import clockstub
from arthur.onclock import door as d


class App(clockstub.StubApp):
    def status(self):
        gc.collect()
        return {{"heap": gc.mem_alloc(), "free": gc.mem_free()}}


async def run():
    app = App({root!r})
    door = await d.start(app, {root!r}, "127.0.0.1", {port}, validate_config=lambda x: None)
    door.bad_tag_delay = 0
    print("door up", gc.mem_alloc())
    return await app.run()

sys.exit(asyncio.run(run()))
"""


def _barrage(port: int, kind: int) -> None:
    """One unauthenticated request of one of four kinds, then the connection is closed."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
    except OSError:
        return
    try:
        f = s.makefile("rb")
        line = f.readline()
        if not line.startswith(d.GREETING):
            return  # over the connection limit: closed with no greeting
        nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())
        if kind == 0:  # a full-size body under the wrong key
            s.sendall(signed(nonce, {"verb": "status"}, os.urandom(d.MAX_BODY), key=WRONG_KEY))
        elif kind == 1:  # announced larger than allowed
            s.sendall(struct.pack(">HI", 65535, 0xFFFFFFFF))
        elif kind == 2:  # garbage
            s.sendall(os.urandom(200))
        else:  # cut off half way through a big body
            s.sendall(struct.pack(">HI", 20, d.MAX_BODY) + os.urandom(d.MAX_BODY // 2))
        if kind in (2, 3):
            s.shutdown(socket.SHUT_WR)  # the sender goes away mid-request
        read_all(s)
    except OSError:
        pass
    finally:
        s.close()


@pytest.mark.skipif(MPY is None, reason="micropython is not installed (brew install "
                    "micropython): the door's memory was NOT tested as the clock runs it")
def test_bad_requests_leave_the_heap_where_it_was_under_micropython(tmp_path):
    """Four hundred unauthenticated requests (full-size bodies under the wrong key, oversize
    announcements, garbage, bodies cut off half way), three connections at a time, against
    the door under MicroPython in a 256 KiB heap: every one is refused, the door still
    answers, and the heap after a collection is where it was before."""
    from concurrent.futures import ThreadPoolExecutor

    root = make_root(tmp_path)
    port = free_port()
    drv = tmp_path / "heap_driver.py"
    drv.write_text(HEAP_DRIVER.format(repo=str(REPO), tests=str(REPO / "tests"),
                                      root=str(root), port=port))
    proc = subprocess.Popen([MPY, "-X", "heapsize=256K", str(drv)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=tmp_path)
    try:
        c = None
        end = time.monotonic() + 20
        while c is None and time.monotonic() < end:
            try:
                c = DoorClient("127.0.0.1", port, KEY, timeout=20)
            except OSError:
                time.sleep(0.1)
        assert c is not None, "the door under MicroPython never answered"
        with c:
            for _ in range(3):  # settle: the first answers allocate their own small things
                c.status()
            before = c.status()["app"]["heap"]
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda i: _barrage(port, i % 4), range(400)))
        with DoorClient("127.0.0.1", port, KEY, timeout=20) as c:
            for _ in range(3):
                c.status()
            after = c.status()["app"]["heap"]
            c.restart()
        assert proc.wait(20) == 42
    finally:
        if proc.poll() is None:
            proc.kill()
        out = proc.stdout.read().decode(errors="replace")
    assert "Traceback" not in out and "MemoryError" not in out, out
    print(f"door heap after a collection: {before} bytes before, {after} after 400 bad "
          "requests")
    assert abs(after - before) <= 256, (before, after)
