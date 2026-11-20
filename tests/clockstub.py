"""A stand-in for the on-clock program, to the contract the door and boot.py are built on.

The real ``ClockApp`` (``arthur/onclock/app.py``) and ``main`` are built on another branch;
this stub implements exactly the contract, so the door, boot.py and the Mac's tool can be
tested without them. It runs on CPython and MicroPython: boot.py's tests put it in a slot as
``arthur/onclock/main.py``, and the emulated end-to-end run does too.

``main`` opens the real door on the port in ``<root>/test-port`` (or the door's own port)
and runs until asked to restart or stop. ``<root>/test-mode-<slot>`` (the slot boot.py made
active) can make it misbehave for the boot tests: ``raise-import`` (raises while being
imported), ``die-young`` (raises after a moment), ``no-door`` (runs, never opens its door).
"""

import asyncio
import json
import sys
import time


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _mode(root):
    """The test mode for the slot boot.py made active (from state.json)."""
    try:
        with open(root + "/state.json") as f:
            active = json.loads(f.read()).get("active", "a")
    except (OSError, ValueError):
        active = "a"
    return _read(root + "/test-mode-" + str(active), "")


if len(sys.argv) > 1 and _mode(sys.argv[1]) == "raise-import":
    raise RuntimeError("this slot is broken on purpose")


class StubLog:
    """``ClockLog``: lines in memory, numbered from 1. ``read(after, max_bytes)`` gives the
    lines after ``after`` (at least one, then as many as fit) and the number to pass next."""

    def __init__(self, keep=500):
        self.lines = []
        self.seq = 0
        self.keep = keep

    def append(self, line):
        self.seq += 1
        self.lines.append((self.seq, line))
        if len(self.lines) > self.keep:
            self.lines.pop(0)
        return self.seq

    def read(self, after_seq, max_bytes):
        out = []
        size = 0
        nxt = after_seq
        for seq, line in self.lines:
            if seq <= after_seq:
                continue
            n = len(json.dumps(line))
            if out and size + n > max_bytes:
                break
            out.append((seq, line))
            size += n
            nxt = seq
        return out, nxt


class StubApp:
    def __init__(self, root):
        self.root = root
        self.log = StubLog()
        self.paused = False
        self.config_loads = 0
        self.config_loaded_at = None
        self.frames = 0
        self._code = None
        self.reload_config()

    async def run(self):
        while self._code is None:
            if not self.paused:
                self.frames += 1
            await asyncio.sleep(0.02)
        return self._code

    def status(self):
        return {"paused": self.paused, "config_loads": self.config_loads,
                "frames": self.frames}

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def reload_config(self):
        self.config_loads += 1
        self.config_loaded_at = time.time()
        self.log.append({"kind": "console", "ts": "2026-11-20T12:00:00.000Z", "level": "INFO",
                         "name": "arthur.onclock", "msg": "config loaded"})

    def request_restart(self):
        self._code = 42

    def request_stop(self):
        self._code = 0


async def main(root, source_factory):
    mode = _mode(root)
    app = StubApp(root)
    door = None
    if mode != "no-door":
        from arthur.onclock import door as door_mod

        port = int(_read(root + "/test-port", str(door_mod.DOOR_PORT)))
        # Every interface only on a (real or emulated) clock; this machine only in tests.
        host = "0.0.0.0" if root == "/data/awtrix-ng/state/arthur" else "127.0.0.1"
        door = await door_mod.start(app, root, host, port,
                                    validate_config=lambda data: None)
    if mode == "die-young":
        await asyncio.sleep(0.3)
        raise RuntimeError("died young on purpose")
    try:
        return await app.run()
    finally:
        if door is not None:
            door.close()
            await door.wait_closed()
