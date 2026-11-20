"""Helpers for the tests of the door, boot.py and the Mac's clock tool: a temporary install
root, small slots with manifests, a door running in a thread, and the stub program."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import socket
import threading
from pathlib import Path

from clockstub import StubApp

from arthur.onclock import door as d
from arthur.tools.onclock_files import manifest_bytes

REPO = Path(__file__).resolve().parents[1]
STUB = Path(__file__).with_name("clockstub.py")
DEPLOY = REPO / "deploy" / "clock"
KEY = bytes(range(32))  # made up, for tests only
WRONG_KEY = bytes(range(1, 33))

#: What a slot needs for the stub program and the real door to run.
SLOT_FILES = {
    "arthur/__init__.py": REPO / "arthur" / "__init__.py",
    "arthur/onclock/__init__.py": REPO / "arthur" / "onclock" / "__init__.py",
    "arthur/onclock/door.py": REPO / "arthur" / "onclock" / "door.py",
    "arthur/onclock/main.py": STUB,
    "arthur/wire/__init__.py": REPO / "arthur" / "wire" / "__init__.py",
    "arthur/wire/util.py": REPO / "arthur" / "wire" / "util.py",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_root(tmp: Path, key: bytes = KEY) -> Path:
    """An install root as ``clock.py install`` leaves it, with slot a filled for the stub."""
    root = tmp / "arthur"
    (root / "slots" / "b").mkdir(parents=True)
    (root / "secrets.json").write_text(json.dumps({"whisker_username": "nobody@example.com",
                                                    "whisker_password": "not-a-password",
                                                    "door_key": key.hex()}))
    (root / "state.json").write_text(json.dumps({"v": 1, "active": "a",
                                                 "tries": {"a": 0, "b": 0}, "errors": {}}))
    (root / "config.json").write_text(json.dumps({"idle": {"page_pct": 30}}))
    fill_slot(root / "slots" / "a")
    return root


def fill_slot(folder: Path, extra: dict[str, bytes] | None = None) -> bytes:
    """Copy the stub program's files into ``folder`` (plus ``extra``) and write the manifest."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for rel, src in SLOT_FILES.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, folder / rel)
    for rel, data in (extra or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(data)
    m = manifest_bytes(folder)
    (folder / "manifest.json").write_bytes(m)
    return m


def manifest_of(files: dict[str, bytes]) -> bytes:
    return json.dumps({"v": 1, "files": {p: [hashlib.sha256(b).hexdigest(), len(b)]
                                         for p, b in files.items()}},
                      sort_keys=True, separators=(",", ":")).encode()


class DoorThread:
    """The real door, with the stub app, on its own event loop in a thread (so the tests'
    blocking client can talk to it)."""

    def __init__(self, root: Path, key: bytes = KEY, reserve: int = 0,
                 validate=lambda data: None) -> None:
        self.root = root
        self.app = StubApp(str(root))
        self.loop = asyncio.new_event_loop()
        self.door = d.Door(self.app, str(root), key, validate_config=validate, reserve=reserve)
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.door.start("127.0.0.1", 0))
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(10)
        self.port = self.door.server.sockets[0].getsockname()[1]

    def stop(self) -> None:
        async def shut() -> None:
            self.door.close()
            await self.door.wait_closed()

        asyncio.run_coroutine_threadsafe(shut(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
