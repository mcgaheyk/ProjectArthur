"""The update door: how changes arrive on the clock from the Mac over Wi-Fi (D-0029).

A small asyncio server inside the on-clock program, on one fixed TCP port. The same source
runs on CPython (the tests) and MicroPython 1.29 (the clock). It runs as root on a device that
holds the Whisker login, so it is kept small and strict:

- Every request carries an HMAC-SHA256 under the door key, over a fresh random challenge
  the door issued on that connection (so a recorded request can never be replayed, and the
  clock's time does not matter). The tag is compared in constant time. A request whose tag
  fails gets one fixed refusal line and the connection is closed. Responses carry a tag too,
  so the Mac knows it talks to its clock.
- Hard size limits on every part of a request; one that breaks them is refused and closed.
- **Nothing unauthenticated costs memory.** A request is read into one buffer made when the
  door starts (:data:`MAX_HEAD` + :data:`MAX_BODY` bytes), one request at a time across all
  connections, and its tag is computed over that buffer as it stands; only after the tag has
  passed are the head and the body copied out. Before that a request allocates nothing
  larger than a few dozen bytes (its 6-byte header, small objects of the event loop), so
  nobody on the Wi-Fi can fragment the program's heap, which MicroPython cannot compact.
  The whole request must arrive within :data:`REQUEST_DEADLINE_S` once its header is in.
- No verb runs a command or evaluates text. Writes go only to the inactive code slot
  (``<root>/slots/a|b/``), ``<root>/config.json`` and ``<root>/state.json``, through checked
  relative paths that never follow a symbolic link.

The protocol, the on-clock layout and the threat list: ``docs/reference/onclock.md``.

The door talks to the program only through the ``ClockApp`` contract: ``status()``,
``pause()``, ``resume()``, ``reload_config()``, ``request_restart()`` and ``log`` (``append``,
``read``). Start it from the program with :func:`start`.
"""

import asyncio
import binascii
import hashlib
import json
import os
import struct
import sys
import time

from arthur.wire.util import hmac_sha256, utc_iso_ms

IS_MPY = sys.implementation.name == "micropython"

#: The door's TCP port on the clock. ``deploy/clock/boot.py`` has its own copy (it may import
#: nothing from a slot); ``tests/test_onclock_boot.py`` checks the two agree.
DOOR_PORT = 7531
VERSION = 1
GREETING = b"ARTHUR-DOOR/1 "
#: The one answer anything unauthenticated or oversized gets, before the connection closes.
REFUSAL = b"ARTHUR-DOOR/1 NO\n"
REQ_TAG = b"ARTHUR-DOOR/1 req"
RES_TAG = b"ARTHUR-DOOR/1 res"

# -- limits ----------------------------------------------------------------------------------
MAX_HEAD = 2048  # the JSON head of a request
MAX_BODY = 32768  # one request's body: a config, a manifest or one chunk of a file
MAX_RESPONSE = 49152
MAX_LOG_BYTES = 24576  # the most log text one `logs` answer carries
MAX_FILES = 256  # files in a slot
MAX_FILE = 262144  # bytes in one file
MAX_SLOT = 2097152  # bytes in a whole slot
MAX_PATH = 120  # characters in a file's path inside a slot
MAX_DEPTH = 6  # folders deep
MAX_CONNECTIONS = 3  # at once; more are closed at once
MAX_REQUESTS = 2000  # on one connection
IO_TIMEOUT_S = 20  # for any one read
REQUEST_DEADLINE_S = 30  # for the rest of a request once its 6-byte header has arrived
READ_CHUNK = 512  # CPython reads a request in pieces this big (MicroPython reads in place)
BAD_TAG_DELAY_S = 1.0  # after a bad tag, before the connection closes
SESSION_IDLE_S = 120  # an unfinished transfer can be taken over after this
FLASH_RESERVE = 2097152  # bytes a transfer must leave free on the clock's flash

MANIFEST = "manifest.json"
PART = ".part"
SLOTS = ("a", "b")
_NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
_HEX = "0123456789abcdef"


class DoorError(Exception):
    """A request the door turns down (the message goes back to the Mac)."""


# -- small helpers both Pythons can run ------------------------------------------------------


def errors_kind(exc):
    """None for the ways a connection normally ends (a timeout, a cut, garbage), else the
    class name: those are logged."""
    if isinstance(exc, (OSError, EOFError, ValueError)):
        return None
    name = type(exc).__name__
    if name in ("TimeoutError", "CancelledError", "IncompleteReadError"):
        return None
    return name


def hexstr(data):
    return binascii.hexlify(data).decode()


def ct_equal(a, b):
    """Compare two byte strings in time that does not depend on where they differ."""
    if len(a) != len(b):
        return False
    diff = 0
    for i in range(len(a)):
        diff |= a[i] ^ b[i]
    return diff == 0


def is_int(v):
    return type(v) is int  # bool is not an int here (on MicroPython it never is)


def is_sha(v):
    if type(v) is not str or len(v) != 64:
        return False
    for c in v:
        if c not in _HEX:
            return False
    return True


def safe_parts(path):
    """A slot-relative path as its parts, or None if it is not allowed: only
    ``[A-Za-z0-9_.-]`` names, no empty, ``.``, ``..`` or hidden names, no absolute path, at most
    :data:`MAX_DEPTH` deep and :data:`MAX_PATH` long, not the manifest or a part file."""
    if type(path) is not str or not path or len(path) > MAX_PATH:
        return None
    parts = path.split("/")
    if len(parts) > MAX_DEPTH:
        return None
    for part in parts:
        if not part or part[0] == ".":
            return None
        for c in part:
            if c not in _NAME_CHARS:
                return None
    if path == MANIFEST or path.endswith(PART):
        return None
    return parts


def kind(dirpath, name):
    """What ``dirpath/name`` is, without following a symbolic link: ``"d"`` folder, ``"f"``
    regular file, ``"l"`` link, ``"o"`` anything else, None if absent."""
    if IS_MPY:
        try:
            entries = os.ilistdir(dirpath)
        except OSError:
            return None
        for e in entries:
            if e[0] == name:
                t = e[1]
                if t == 0x4000:
                    return "d"
                if t == 0x8000:
                    return "f"
                if t == 0xA000:
                    return "l"
                return "o"  # includes 0, "unknown": refused rather than guessed
        return None
    try:
        st = os.lstat(dirpath + "/" + name)
    except OSError:
        return None
    m = st.st_mode & 0o170000
    return {0o040000: "d", 0o100000: "f", 0o120000: "l"}.get(m, "o")


def entries(dirpath):
    """``(name, kind)`` for each entry of a folder, links not followed."""
    if IS_MPY:
        out = []
        for e in os.ilistdir(dirpath):
            t = e[1]
            k = "d" if t == 0x4000 else "f" if t == 0x8000 else "l" if t == 0xA000 else "o"
            out.append((e[0], k))
        return out
    return [(n, kind(dirpath, n)) for n in os.listdir(dirpath)]


def remove_tree(path):
    """Delete a real folder and everything in it. Links inside are removed, never followed."""
    for name, k in entries(path):
        p = path + "/" + name
        if k == "d":
            remove_tree(p)
        else:
            os.remove(p)
    os.rmdir(path)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            h.update(chunk)
    return hexstr(h.digest())


def write_atomic(path, data):
    """Write ``path`` whole or not at all (a temporary file, then a rename)."""
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.rename(tmp, path)


def free_bytes(path):
    st = os.statvfs(path)
    return st[4] * (st[1] or st[0])


def other(slot):
    return "b" if slot == "a" else "a"


# -- state.json: shared with boot.py, which keeps its own copy of this format ----------------


def read_state(root):
    """``<root>/state.json``: ``{"v": 1, "active": "a"|"b", "tries": {"a": n, "b": n}, ...}``.
    Anything unreadable reads as the default (slot a, no tries)."""
    st = None
    try:
        with open(root + "/state.json") as f:
            st = json.loads(f.read())
    except (OSError, ValueError):
        pass
    if not isinstance(st, dict):
        st = {}
    if st.get("active") not in SLOTS:
        st["active"] = "a"
    tries = st.get("tries")
    if not isinstance(tries, dict):
        tries = {}
    for s in SLOTS:
        if not is_int(tries.get(s)):
            tries[s] = 0
    st["tries"] = tries
    st["v"] = 1
    return st


def write_state(root, st):
    write_atomic(root + "/state.json", json.dumps(st).encode())


def bundle_id(manifest_bytes):
    """A slot's identity: the first 16 hex digits of its manifest's SHA-256."""
    return hexstr(hashlib.sha256(manifest_bytes).digest())[:16]


def slot_id(root, slot):
    try:
        with open(root + "/slots/" + slot + "/" + MANIFEST, "rb") as f:
            return bundle_id(f.read())
    except OSError:
        return None


def load_key(root):
    """The door key from ``<root>/secrets.json`` (``door_key``, 64 hex digits). The other
    values in that file are never read here."""
    try:
        with open(root + "/secrets.json") as f:
            text = f.read()
        k = json.loads(text).get("door_key")
    except (OSError, ValueError, AttributeError):
        raise DoorError("no door key in secrets.json") from None
    if not is_sha(k):
        raise DoorError("the door key in secrets.json is not 64 hex digits")
    return binascii.unhexlify(k)


def parse_manifest(data):
    """Check a manifest's bytes: ``{"v": 1, "files": {path: [sha256, size], ...}}``.
    Returns ``{path: (sha256, size)}``; raises DoorError."""
    try:
        m = json.loads(data)
    except ValueError:
        raise DoorError("manifest is not JSON") from None
    if not isinstance(m, dict) or m.get("v") != 1 or not isinstance(m.get("files"), dict):
        raise DoorError("manifest has the wrong shape")
    files = m["files"]
    if not files or len(files) > MAX_FILES:
        raise DoorError("manifest has too many or no files")
    out = {}
    total = 0
    dirs = {}
    for path in files:
        entry = files[path]
        parts = safe_parts(path)
        if parts is None:
            raise DoorError("path not allowed: " + str(path)[:MAX_PATH])
        if not isinstance(entry, list) or len(entry) != 2 or not is_sha(entry[0]) \
                or not is_int(entry[1]) or not 0 <= entry[1] <= MAX_FILE:
            raise DoorError("bad entry for " + path)
        for i in range(1, len(parts)):
            dirs["/".join(parts[:i])] = True
        total += entry[1]
        out[path] = (entry[0], entry[1])
    if total > MAX_SLOT:
        raise DoorError("bundle too large")
    for path in out:
        if path in dirs:
            raise DoorError("a path is both a file and a folder: " + path)
    return out


class _Session:
    """One bundle transfer into the inactive slot."""

    def __init__(self, sid, slot, files, manifest_bytes, need):
        self.id = sid
        self.slot = slot
        self.files = files
        self.manifest_bytes = manifest_bytes
        self.mid = bundle_id(manifest_bytes)
        self.need = need  # path -> bytes received so far
        self.touched = time.time()


class Door:
    """The door's server. ``app`` is the running ``ClockApp``; ``root`` the install folder
    (``/data/awtrix-ng/state/arthur`` on the clock)."""

    def __init__(self, app, root, key, validate_config=None, reserve=FLASH_RESERVE):
        if len(key) != 32:
            raise DoorError("the door key must be 32 bytes")
        self.app = app
        self.root = root
        self._key = key
        # HMAC-SHA256's two padded keys, computed once (the tag is then computed in place).
        k = bytes(key) + bytes(32)
        self._ipad = bytes(b ^ 0x36 for b in k)
        self._opad = bytes(b ^ 0x5C for b in k)
        # The one request buffer, the tag's and the header's, made once: nothing a request
        # sends before its tag has passed is held anywhere else.
        self._buf = bytearray(MAX_HEAD + MAX_BODY)
        self._mv = memoryview(self._buf)
        self._tagbuf = bytearray(32)
        self._tagmv = memoryview(self._tagbuf)
        self._lock = asyncio.Lock()
        self.bad_tag_delay = BAD_TAG_DELAY_S
        self._validate = validate_config
        self.reserve = reserve
        self.run_id = hexstr(os.urandom(8))
        self.running = read_state(root)["active"]  # the slot this program came from
        self.restart_pending = False
        self.session = None
        self.server = None
        self.conns = 0
        self.refused = 0

    # -- lifecycle ---------------------------------------------------------------------------

    async def start(self, host="0.0.0.0", port=DOOR_PORT):
        self.server = await asyncio.start_server(self._serve, host, port)
        self._log("INFO", "door open on port " + str(port))
        return self

    def close(self):
        if self.server is not None:
            self.server.close()

    async def wait_closed(self):
        if self.server is not None:
            await self.server.wait_closed()

    def _log(self, level, msg):
        try:
            self.app.log.append({"kind": "console", "ts": utc_iso_ms(time.time()),
                                 "level": level, "name": "arthur.door", "msg": msg})
        except Exception:  # noqa: BLE001, S110 - logging must never break the door
            pass

    # -- the connection ----------------------------------------------------------------------

    async def _read(self, reader, n):
        return await asyncio.wait_for(reader.readexactly(n), IO_TIMEOUT_S)

    async def _fill(self, reader, mv, n):
        """Read exactly ``n`` bytes into ``mv[:n]`` (a view of a fixed buffer). MicroPython
        reads straight into the buffer; CPython (the tests) reads small pieces and copies."""
        got = 0
        while got < n:
            if IS_MPY:
                k = await asyncio.wait_for(reader.readinto(mv[got:n]), IO_TIMEOUT_S)
            else:
                piece = await asyncio.wait_for(reader.read(min(n - got, READ_CHUNK)),
                                               IO_TIMEOUT_S)
                k = len(piece)
                mv[got:got + k] = piece
                piece = None
            if not k:
                raise EOFError("connection closed in a request")
            got += k

    async def _request(self, reader, nonce, hdr, hlen, blen):
        """The rest of one request (head, body, tag) into the fixed buffers; True if its tag
        is right. Holds the door's lock (one request at a time)."""
        n = hlen + blen
        await self._fill(reader, self._mv, n)
        await self._fill(reader, self._tagmv, 32)
        inner = hashlib.sha256(self._ipad)
        inner.update(REQ_TAG)
        inner.update(nonce)
        inner.update(hdr)
        inner.update(self._mv[:n])
        outer = hashlib.sha256(self._opad)
        outer.update(inner.digest())
        return ct_equal(self._tagbuf, outer.digest())

    async def _serve(self, reader, writer):
        if self.conns >= MAX_CONNECTIONS:
            await self._close(writer)
            return
        self.conns += 1
        try:
            nonce = os.urandom(16)
            writer.write(GREETING + hexstr(nonce).encode() + b"\n")
            await writer.drain()
            for _ in range(MAX_REQUESTS):
                try:
                    hdr = await self._read(reader, 6)
                except EOFError:
                    break  # the client is done
                hlen, blen = struct.unpack(">HI", hdr)
                if hlen == 0 or hlen > MAX_HEAD or blen > MAX_BODY:
                    await self._refuse(writer, False)
                    break
                async with self._lock:
                    ok = await asyncio.wait_for(
                        self._request(reader, nonce, hdr, hlen, blen), REQUEST_DEADLINE_S)
                    if ok:  # authenticated: copy out before the next request reuses it
                        head = bytes(self._mv[:hlen])
                        body = bytes(self._mv[hlen:hlen + blen]) if blen else b""
                if not ok:
                    await self._refuse(writer, True)
                    break
                # Authenticated from here on.
                after = None
                try:
                    req = json.loads(head)
                    if not isinstance(req, dict):
                        raise DoorError("request is not an object")
                    res, after = self._dispatch(req, body)
                except DoorError as exc:
                    res = {"ok": False, "error": str(exc)}
                except ValueError:
                    res = {"ok": False, "error": "request is not JSON"}
                except Exception as exc:  # noqa: BLE001 - report, never crash the program
                    res = {"ok": False, "error": "internal error: " + type(exc).__name__}
                    self._log("ERROR", "door: " + type(exc).__name__ + " " + str(exc)[:200])
                head = body = None
                new = os.urandom(16)
                res["nonce"] = hexstr(new)
                out = json.dumps(res).encode()
                if len(out) > MAX_RESPONSE:
                    out = json.dumps({"ok": False, "error": "response too large",
                                      "nonce": hexstr(new)}).encode()
                rhdr = struct.pack(">I", len(out))
                writer.write(rhdr + out + hmac_sha256(self._key, RES_TAG + nonce + rhdr + out))
                await writer.drain()
                nonce = new
                if after is not None:
                    after()
        except Exception as exc:  # noqa: BLE001 - a timeout, a cut, garbage: close quietly
            if errors_kind(exc) is not None:
                self._log("ERROR", "door connection: " + type(exc).__name__)
        finally:
            self.conns -= 1
            await self._close(writer)

    async def _refuse(self, writer, bad_tag):
        self.refused += 1
        try:
            writer.write(REFUSAL)
            await writer.drain()
            if bad_tag and self.bad_tag_delay:
                await asyncio.sleep(self.bad_tag_delay)  # slows guessing; the key is 256 bits
        except OSError:
            pass

    async def _close(self, writer):
        try:
            writer.close()
            await writer.wait_closed()
        except (TimeoutError, OSError, EOFError):
            pass

    # -- verbs -------------------------------------------------------------------------------

    def _dispatch(self, req, body):
        verb = req.get("verb")
        if verb == "status":
            return self._status(), None
        if verb == "config-put":
            return self._config_put(body), None
        if verb == "bundle-begin":
            return self._bundle_begin(body), None
        if verb == "bundle-file":
            return self._bundle_file(req, body), None
        if verb == "bundle-commit":
            return self._bundle_commit(req, body)
        if verb == "bundle-abort":
            self.session = None
            return {"ok": True}, None
        if verb == "logs":
            return self._logs(req), None
        if verb == "pause":
            self.app.pause()
            self._log("INFO", "paused from the Mac")
            return {"ok": True, "paused": True}, None
        if verb == "resume":
            self.app.resume()
            self._log("INFO", "resumed from the Mac")
            return {"ok": True, "paused": False}, None
        if verb == "restart":
            st = read_state(self.root)
            st["tries"][st["active"]] = 0
            write_state(self.root, st)
            self._log("INFO", "restart asked from the Mac")
            return {"ok": True}, self.app.request_restart
        raise DoorError("unknown verb")

    def _status(self):
        st = read_state(self.root)
        try:
            with open(self.root + "/config.json", "rb") as f:
                cfg = hexstr(hashlib.sha256(f.read()).digest())[:16]
        except OSError:
            cfg = None
        try:
            free = free_bytes(self.root)
        except OSError:
            free = None
        return {"ok": True, "door": VERSION, "run": self.run_id, "app": self.app.status(),
                "running": self.running, "active": st["active"], "tries": st["tries"],
                "halted": st.get("halted"), "errors": st.get("errors") or {},
                "restart_pending": self.restart_pending,
                "slots": {s: slot_id(self.root, s) for s in SLOTS}, "config": cfg,
                "free": free, "transfer": self.session.id if self.session else None}

    def _config_put(self, body):
        try:
            data = json.loads(body)
        except ValueError:
            raise DoorError("config is not JSON") from None
        if not isinstance(data, dict):
            raise DoorError("config is not an object")
        validate = self._validate
        if validate is None:
            from arthur.config import config_from_json as validate
        try:
            validate(data)
        except Exception as exc:  # noqa: BLE001 - any failure means "do not install it"
            raise DoorError("config refused: " + str(exc)[:200]) from None
        write_atomic(self.root + "/config.json", body)
        self.app.reload_config()
        cfg = hexstr(hashlib.sha256(body).digest())[:16]
        self._log("INFO", "config " + cfg + " installed from the Mac")
        return {"ok": True, "config": cfg}

    def _slot_dir(self, slot):
        """``<root>/slots/<slot>``, made if missing; refused if any part is not a real folder."""
        for d, name in ((self.root, "slots"), (self.root + "/slots", slot)):
            k = kind(d, name)
            if k is None:
                os.mkdir(d + "/" + name)
            elif k != "d":
                raise DoorError("slot folder is not a real folder")
        return self.root + "/slots/" + slot

    def _bundle_begin(self, body):
        if self.restart_pending:
            raise DoorError("a new bundle is waiting for the restart")
        s = self.session
        mid = bundle_id(body)
        if s is not None and s.mid != mid and time.time() - s.touched < SESSION_IDLE_S:
            raise DoorError("busy: another transfer is under way")
        files = parse_manifest(body)
        slot = other(self.running)
        base = self._slot_dir(slot)
        # The slot is unusable from here until its manifest is written at commit.
        if kind(base, MANIFEST) is not None:
            os.remove(base + "/" + MANIFEST)
        need = {}
        need_bytes = 0
        for path in files:
            sha, size = files[path]
            if self._have(base, path, sha):
                continue
            need[path] = 0
            need_bytes += size
        if need_bytes and free_bytes(self.root) - need_bytes < self.reserve:
            raise DoorError("not enough free flash for this bundle")
        self.session = _Session(hexstr(os.urandom(8)), slot, files, body, need)
        self._log("INFO", "transfer " + self.session.id + " to slot " + slot + ": "
                  + str(len(need)) + " of " + str(len(files)) + " files needed")
        return {"ok": True, "session": self.session.id, "slot": slot,
                "need": sorted(need)}

    def _have(self, base, path, sha):
        """Is ``path`` in the slot already, as a regular file reached through real folders,
        with this hash?"""
        parts = path.split("/")
        d = base
        for name in parts[:-1]:
            if kind(d, name) != "d":
                return False
            d = d + "/" + name
        if kind(d, parts[-1]) != "f":
            return False
        return file_sha(d + "/" + parts[-1]) == sha

    def _session(self, req):
        s = self.session
        if s is None or req.get("session") != s.id:
            raise DoorError("no such transfer")
        s.touched = time.time()
        return s

    def _bundle_file(self, req, body):
        s = self._session(req)
        path = req.get("path")
        if path not in s.need:
            raise DoorError("file not wanted in this transfer")
        sha, size = s.files[path]
        offset = req.get("offset")
        if not is_int(offset) or offset != s.need[path]:
            raise DoorError("wrong offset")
        if offset + len(body) > size:
            raise DoorError("file longer than its manifest says")
        parts = path.split("/")
        d = self._slot_dir(s.slot)
        for name in parts[:-1]:
            k = kind(d, name)
            if k == "l":
                os.remove(d + "/" + name)  # a link where a folder belongs: the link goes
                k = None
            if k is None:
                os.mkdir(d + "/" + name)
            elif k != "d":
                raise DoorError("not a real folder in the slot: " + path)
            d = d + "/" + name
        final = d + "/" + parts[-1]
        part = final + PART
        if offset == 0:
            if kind(d, parts[-1] + PART) is not None:
                os.remove(part)  # removes a link itself, never what it points at
            mode = "wb"
        else:
            if kind(d, parts[-1] + PART) != "f":
                raise DoorError("transfer of this file was lost; start it again")
            mode = "ab"
        with open(part, mode) as f:
            f.write(body)
        s.need[path] = offset + len(body)
        if s.need[path] < size:
            return {"ok": True, "have": s.need[path]}
        if file_sha(part) != sha:
            os.remove(part)
            s.need[path] = 0
            raise DoorError("hash mismatch for " + path)
        if kind(d, parts[-1]) not in (None, "f", "l"):
            raise DoorError("not a file in the slot: " + path)
        os.rename(part, final)  # replaces a link itself, never follows it
        del s.need[path]
        return {"ok": True, "have": size, "done": True}

    def _bundle_commit(self, req, body):
        """Check every file, prune, write the manifest, and make the slot active. A body is
        a new config.json to install with it: the new code may need it, so it is not checked
        by the running (old) code, only for being a JSON object (the Mac built it with the
        new code)."""
        s = self._session(req)
        if s.need:
            raise DoorError("files still missing: " + str(len(s.need)))
        if body:
            try:
                new_config = json.loads(body)
            except ValueError:
                new_config = None
            if not isinstance(new_config, dict):
                raise DoorError("config is not a JSON object")
        base = self._slot_dir(s.slot)
        for path in s.files:
            if not self._have(base, path, s.files[path][0]):
                raise DoorError("file does not match the manifest: " + path)
        keep_dirs = {}
        for path in s.files:
            parts = path.split("/")
            for i in range(1, len(parts)):
                keep_dirs["/".join(parts[:i])] = True
        self._prune(base, "", s.files, keep_dirs)
        write_atomic(base + "/" + MANIFEST, s.manifest_bytes)
        if body:
            write_atomic(self.root + "/config.json", body)
        st = read_state(self.root)
        st["active"] = s.slot
        st["tries"][s.slot] = 0
        if "halted" in st:
            del st["halted"]
        write_state(self.root, st)
        self.session = None
        self.restart_pending = True
        self._log("INFO", "bundle " + s.mid + " installed in slot " + s.slot + "; restarting")
        return {"ok": True, "slot": s.slot, "bundle": s.mid}, self.app.request_restart

    def _prune(self, d, rel, keep_files, keep_dirs):
        """Delete what the manifest does not name. Never descends a link."""
        for name, k in entries(d):
            r = rel + name
            p = d + "/" + name
            if k == "d" and r in keep_dirs:
                self._prune(p, r + "/", keep_files, keep_dirs)
            elif k == "f" and r in keep_files:
                pass
            elif k == "d":
                remove_tree(p)
            else:
                os.remove(p)

    def _logs(self, req):
        after = req.get("after", 0)
        most = req.get("max", MAX_LOG_BYTES)
        if not is_int(after) or after < 0 or not is_int(most) or most < 1:
            raise DoorError("bad logs request")
        lines, nxt = self.app.log.read(after, min(most, MAX_LOG_BYTES))
        # "log": the id of the log on the flash, the same across restarts (D-0035); with it a
        # reader keeps its place when "run" changes. None when the log is in RAM only.
        return {"ok": True, "run": self.run_id, "log": getattr(self.app.log, "log_id", None),
                "lines": [[q, ln] for q, ln in lines], "next": nxt}


async def start(app, root, host="0.0.0.0", port=DOOR_PORT, validate_config=None):
    """Open the door for ``app``, with the key from ``<root>/secrets.json``. Raises
    DoorError when there is no valid key (the program should carry on without the door)."""
    door = Door(app, root, load_key(root), validate_config=validate_config)
    return await door.start(host, port)
