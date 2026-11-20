"""Stage 0 of the program on the clock (D-0029). Never replaced over Wi-Fi.

    micropython -X heapsize=3M boot.py ROOT TMP      (run by autostart.sh)

Two code slots, ``ROOT/slots/a`` and ``ROOT/slots/b``. Each start:

1. Read ``ROOT/state.json`` (which slot is active, failed starts per slot).
2. Pick a slot: the active one, unless it has failed :data:`MAX_TRIES` starts in a row or
   its files do not match its manifest; then the other one, if it is whole. If neither is
   usable, record why in ``state.json`` and exit :data:`HALT` without a further write, so the
   daemon's back-off ends it. (The first start after a power-on gives both slots one more
   round, in case the failure was passing, such as no Wi-Fi.)
3. Count this start in ``state.json`` BEFORE importing anything from the slot.
4. Put the slot on ``sys.path`` and run ``arthur.onclock.main.main(ROOT, source_factory)``.
5. Watch for health: once the program has run :data:`HEALTHY_AFTER_S`, its update door
   answers on 127.0.0.1 with its greeting, and it says it is drawing (``healthy()`` in the
   slot's ``arthur.onclock.main``: a frame reached AWTRIX within the last minute), the count
   goes back to 0. Until then it is looked at again every :data:`PROBE_EVERY_S`. A program
   whose door answers can be fixed from the Mac, and one that draws is doing its job; one
   that does both is healthy. It is never killed for being unhealthy: it stays counted, so
   its next start (a crash, a restart, a power cycle) brings the fall-back closer.
6. The program's answer is the exit code: 0 stops for good, 42 asks ``autostart.sh`` to start
   it again at once (a new bundle, or a restart from the Mac). An import error, a crash, or
   any other exit code (``main`` returns 1 when it fails) is recorded, and after a short
   pause also ends in 42, so the daemon's own failure counter (5 in a row and it gives up)
   is not spent on them.

``state.json`` is the one file to read when something is wrong: active slot, counts, the last
error of each slot, and ``halted`` with the reason when both slots are spent. Writes to the
flash are few and small: one at each start, one when healthy, one per recorded error.
Details for the start (picks, fall-backs, errors) also go to ``/tmp/arthur/boot.txt`` (RAM).

Imports only what MicroPython has built in, so it runs before any slot is trusted. The door
writes ``state.json`` too (a new bundle, a restart from the Mac); both read it just before
changing it.
"""

import asyncio
import binascii
import hashlib
import json
import os
import sys
import time

ROOT = "/data/awtrix-ng/state/arthur"
TMP = "/tmp/arthur"
#: The update door's port: the same number as ``arthur.onclock.door.DOOR_PORT``.
DOOR_PORT = 7531
GREETING = b"ARTHUR-DOOR/1 "
#: Failed starts in a row before a slot is given up for the other.
MAX_TRIES = 3
#: Seconds the program must run, with its door answering, to count as healthy.
HEALTHY_AFTER_S = 120
PROBE_EVERY_S = 15
#: Seconds to wait after an import error or a crash before starting again.
CRASH_PAUSE_S = 5
RESTART = 42
HALT = 3
SLOTS = ("a", "b")
CONSOLE_MAX = 262144


def other(slot):
    return "b" if slot == "a" else "a"


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


class Boot:
    def __init__(self, root=ROOT, tmp=TMP, port=DOOR_PORT, healthy_after=HEALTHY_AFTER_S,
                 probe_every=PROBE_EVERY_S, crash_pause=CRASH_PAUSE_S):
        self.root = root
        self.tmp = tmp
        self.port = port
        self.healthy_after = healthy_after
        self.probe_every = probe_every
        self.crash_pause = crash_pause

    # -- notes and state -------------------------------------------------------------------

    def note(self, msg):
        line = "boot " + str(int(time.time())) + " " + msg
        print(line)
        try:
            with open(self.tmp + "/boot.txt", "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def read_state(self):
        st = None
        try:
            with open(self.root + "/state.json") as f:
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
            if type(tries.get(s)) is not int:
                tries[s] = 0
        st["tries"] = tries
        if not isinstance(st.get("errors"), dict):
            st["errors"] = {}
        st["v"] = 1
        return st

    def write_state(self, st):
        path = self.root + "/state.json"
        with open(path + ".new", "w") as f:
            f.write(json.dumps(st))
        os.rename(path + ".new", path)

    # -- slots -----------------------------------------------------------------------------

    def verify(self, slot):
        """Is the slot whole: a manifest, and every file in it with the right hash?
        Returns None if so, else the reason."""
        base = self.root + "/slots/" + slot
        try:
            with open(base + "/manifest.json") as f:
                files = json.loads(f.read())["files"]
        except (OSError, ValueError, KeyError, TypeError):
            return "no manifest"
        for path in files:
            if ".." in path or path[:1] == "/":
                return "bad path in manifest"
            h = hashlib.sha256()
            try:
                with open(base + "/" + path, "rb") as f:
                    while True:
                        chunk = f.read(4096)
                        if not chunk:
                            break
                        h.update(chunk)
            except OSError:
                return "missing " + path
            if binascii.hexlify(h.digest()).decode() != files[path][0]:
                return "changed " + path
        return None

    def pick(self):
        """Choose the slot to start and count the start; None when both are spent."""
        st = self.read_state()
        first = not exists(self.tmp + "/booted")
        if first:
            try:
                with open(self.tmp + "/booted", "w") as f:
                    f.write("1")
            except OSError:
                pass
        if st.get("halted"):
            if not first:
                self.note("halted: " + str(st["halted"]) + " (see state.json)")
                return None
            self.note("halted before this power-on; giving both slots another round")
            del st["halted"]
            st["tries"] = {"a": 0, "b": 0}
        active = st["active"]
        for slot in (active, other(active)):
            if st["tries"][slot] >= MAX_TRIES:
                self.note("slot " + slot + ": " + str(st["tries"][slot]) + " failed starts")
                continue
            why = self.verify(slot)
            if why is not None:
                self.note("slot " + slot + " is not whole: " + why)
                st["tries"][slot] = MAX_TRIES
                st["errors"][slot] = "not whole: " + why
                continue
            if slot != active:
                self.note("falling back from slot " + active + " to slot " + slot)
                st["active"] = slot
            st["tries"][slot] += 1
            self.write_state(st)
            self.note("starting slot " + slot + ", try " + str(st["tries"][slot]))
            return slot
        st["halted"] = "both slots failed; see errors"
        self.write_state(st)
        self.note("both slots failed; stopping (see state.json)")
        return None

    def record_error(self, slot, text):
        st = self.read_state()
        st["errors"][slot] = text[:200]
        self.write_state(st)
        self.note("slot " + slot + " failed: " + text[:200])

    def mark_healthy(self, slot):
        st = self.read_state()
        if st["tries"].get(slot) == 0 and slot not in st["errors"]:
            return
        st["tries"][slot] = 0
        if slot in st["errors"]:
            del st["errors"][slot]
        self.write_state(st)
        self.note("slot " + slot + " is healthy")

    # -- health ----------------------------------------------------------------------------

    async def probe(self):
        """Does the door answer on this machine with its greeting?"""
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.port), 5)
        except Exception:  # noqa: BLE001
            return False
        try:
            line = await asyncio.wait_for(r.readline(), 5)
            return line.startswith(GREETING)
        except Exception:  # noqa: BLE001
            return False
        finally:
            try:
                w.close()
                await w.wait_closed()
            except Exception:  # noqa: BLE001, S110
                pass

    def drawing(self):
        """Does the program say it is drawing? (``healthy()`` in the slot's
        ``arthur.onclock.main``; a program without it is judged by its door alone.)"""
        mod = sys.modules.get("arthur.onclock.main")
        check = getattr(mod, "healthy", None)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 - a probe never raises
            return False

    async def watch(self, slot):
        await asyncio.sleep(self.healthy_after)
        said = False
        while True:
            door = await self.probe()
            if door and self.drawing():
                break
            if door and not said:
                self.note("slot " + slot + ": the door answers but nothing is being drawn")
                said = True
            await asyncio.sleep(self.probe_every)
        self.mark_healthy(slot)

    async def supervise(self, coro, slot):
        task = asyncio.create_task(coro)
        watcher = asyncio.create_task(self.watch(slot))
        try:
            return await task
        finally:
            watcher.cancel()

    # -- the start -------------------------------------------------------------------------

    def trim_console(self):
        """Keep the program's console (``autostart.sh`` appends to it, in RAM) bounded."""
        path = self.tmp + "/console.log"
        try:
            if os.stat(path)[6] > CONSOLE_MAX:
                with open(path, "w") as f:
                    f.write("(console trimmed by boot)\n")
        except OSError:
            pass

    def run(self):
        try:
            os.mkdir(self.tmp)
        except OSError:
            pass
        self.trim_console()
        slot = self.pick()
        if slot is None:
            return HALT
        base = self.root + "/slots/" + slot
        sys.path.insert(0, base + "/lib")
        sys.path.insert(0, base)
        try:
            __import__("arthur.onclock.main")
            main = sys.modules["arthur.onclock.main"].main
        except Exception as exc:  # noqa: BLE001 - a broken slot must not stop the clock
            self.record_error(slot, "import: " + type(exc).__name__ + ": " + str(exc))
            time.sleep(self.crash_pause)
            return RESTART
        try:
            code = asyncio.run(self.supervise(main(self.root, source_factory), slot))
        except Exception as exc:  # noqa: BLE001
            self.record_error(slot, "run: " + type(exc).__name__ + ": " + str(exc))
            time.sleep(self.crash_pause)
            return RESTART
        if code is None:
            code = 0
        if code not in (0, RESTART):
            self.record_error(slot, "run: exited with " + str(code) + " (see console.log)")
            time.sleep(self.crash_pause)
            return RESTART
        self.note("program ended with " + str(code))
        return code


def source_factory(*args, **kwargs):
    """Build the robot source for the program: ``arthur.onclock.source.make_source`` from the
    slot, called by ``arthur.onclock.main`` as ``(cfg=, log=, tls=, root=)``. It is imported
    from the slot, so how the source is built can change over Wi-Fi; only this name is fixed
    here."""
    __import__("arthur.onclock.source")
    return sys.modules["arthur.onclock.source"].make_source(*args, **kwargs)


def test_settings():
    """Tests only: ``ARTHUR_ONCLOCK_TEST`` (a JSON object) may set ``door_port``,
    ``healthy_after``, ``probe_every`` and ``crash_pause``. The daemon starts the launcher
    with a clean environment on the clock, so there it is always empty."""
    try:
        text = os.getenv("ARTHUR_ONCLOCK_TEST")
        data = json.loads(text) if text else {}
    except Exception:  # noqa: BLE001
        data = {}
    out = {}
    if isinstance(data, dict):
        for k in ("door_port", "healthy_after", "probe_every", "crash_pause"):
            if k in data:
                out[k if k != "door_port" else "port"] = data[k]
    return out


if __name__ == "__main__":
    sys.exit(Boot(sys.argv[1] if len(sys.argv) > 1 else ROOT,
                  sys.argv[2] if len(sys.argv) > 2 else TMP, **test_settings()).run())
