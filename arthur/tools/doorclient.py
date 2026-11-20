"""The Mac's side of the clock's update door (``arthur.onclock.door``). CPython only.

One :class:`DoorClient` is one connection: it reads the door's challenge, signs each request
with it, checks the tag on each answer and takes the next challenge from it. The protocol is
in ``docs/reference/onclock.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

from arthur.onclock import door as d

CHUNK = 24576  # bytes of a file per request (under the door's MAX_BODY)


class DoorRefused(Exception):
    """The door would not talk to us (wrong key, or a request it judged too large)."""


class DoorFailed(Exception):
    """The door answered with ok: false."""


class DoorClient:
    def __init__(self, host: str, port: int, key: bytes, timeout: float = 20.0) -> None:
        self.key = key
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.rf = self.sock.makefile("rb")
        line = self.rf.readline(64)
        if not line.startswith(d.GREETING) or not line.endswith(b"\n"):
            self.close()
            raise DoorRefused("not an Arthur door (or it is busy)")
        self.nonce = bytes.fromhex(line[len(d.GREETING):-1].decode())

    def close(self) -> None:
        try:
            self.rf.close()
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _exact(self, n: int) -> bytes:
        data = self.rf.read(n)
        if data is None or len(data) != n:
            raise DoorRefused("the door closed the connection")
        return data

    def call(self, head: dict[str, Any], body: bytes = b"", check: bool = True) -> dict:
        """Send one request; return the answer. Raises DoorRefused or, with ``check``,
        DoorFailed for ok: false."""
        h = json.dumps(head, separators=(",", ":")).encode()
        hdr = struct.pack(">HI", len(h), len(body))
        tag = hmac.new(self.key, d.REQ_TAG + self.nonce + hdr + h + body, hashlib.sha256)
        self.sock.sendall(hdr + h + body + tag.digest())
        first = self.rf.read(4)
        if not first or len(first) < 4:
            raise DoorRefused("the door closed the connection")
        if first == d.REFUSAL[:4]:
            raise DoorRefused("the door refused the request (wrong key?)")
        (n,) = struct.unpack(">I", first)
        if n > d.MAX_RESPONSE:
            raise DoorRefused("answer too large")
        out = self._exact(n)
        rtag = self._exact(32)
        want = hmac.new(self.key, d.RES_TAG + self.nonce + first + out, hashlib.sha256).digest()
        if not hmac.compare_digest(rtag, want):
            raise DoorRefused("the answer's tag is wrong: not our clock?")
        res = json.loads(out)
        self.nonce = bytes.fromhex(res.pop("nonce"))
        if check and not res.get("ok"):
            raise DoorFailed(res.get("error", "failed"))
        return res

    # -- the verbs ---------------------------------------------------------------------------

    def status(self) -> dict:
        return self.call({"verb": "status"})

    def config_put(self, data: bytes) -> dict:
        return self.call({"verb": "config-put"}, data)

    def bundle(self, folder: Path, manifest: bytes, config: bytes | None = None,
               progress: Callable[[str], None] | None = None) -> dict:
        """Send a slot: only the files the inactive slot lacks, then commit (with a new
        config.json when ``config`` is given, installed in the same step)."""
        begin = self.call({"verb": "bundle-begin"}, manifest)
        sid = begin["session"]
        sent = 0
        try:
            for path in begin["need"]:
                data = (folder / path).read_bytes()
                offset = 0
                while True:
                    chunk = data[offset:offset + CHUNK]
                    self.call({"verb": "bundle-file", "session": sid, "path": path,
                               "offset": offset}, chunk)
                    offset += len(chunk)
                    if offset >= len(data):
                        break
                sent += len(data)
                if progress:
                    progress(f"  sent {path} ({len(data):,} bytes)")
            res = self.call({"verb": "bundle-commit", "session": sid}, config or b"")
        except DoorFailed:
            self.call({"verb": "bundle-abort"}, check=False)
            raise
        res["files_sent"] = len(begin["need"])
        res["bytes_sent"] = sent
        return res

    def logs(self, after: int, most: int = d.MAX_LOG_BYTES) -> dict:
        return self.call({"verb": "logs", "after": after, "max": most})

    def pause(self) -> dict:
        return self.call({"verb": "pause"})

    def resume(self) -> dict:
        return self.call({"verb": "resume"})

    def restart(self) -> dict:
        return self.call({"verb": "restart"})
