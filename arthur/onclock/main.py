"""What the boot script on the clock calls: build the program and run it.

    import asyncio
    from arthur.onclock.main import main
    code = asyncio.run(main("/data/awtrix-ng/state/arthur", source_factory))

In order: the log ring (kept on the flash too, in ``<root>/log``, when this program runs
from a code slot: :mod:`arthur.onclock.logstore`, D-0035); the config (below); the shared TLS context for the clock's CA
bundle; the robot source, from ``source_factory(cfg=, log=, tls=, root=)``
(``deploy/clock/boot.py`` hands :func:`arthur.onclock.source.make_source` through); the
:class:`~arthur.onclock.app.ClockApp`, reporting the slot's bundle id as its version; the
update door (:func:`arthur.onclock.door.start`), and without a valid key in
``secrets.json``, or if its port is taken, the program carries on without it and says so;
then the app's loop until it asks to stop or be restarted.

**The config.** Both code slots read the one ``<root>/config.json``, so a file this code
rejects must not stop the program, or a bad file pushed with new code would spend both slots
(the new code fails, boot.py falls back, the old code rejects the file) and only a USB cable
would reach the clock. So the program keeps, per slot, the exact bytes of the last
``config.json`` the code from that slot loaded (``<root>/config.good.a.json``,
``config.good.b.json``; written whole, and only when they changed). At start:
``config.json`` if it loads (and it becomes this slot's kept copy); else this slot's kept
copy, then the other slot's, the first that loads, with one error line in the log saying
why ``config.json`` was refused and which kept file is in use, and ``config_error`` in the
app's status until a good ``config.json`` is reloaded (``config-put``) or the program
restarts on one. Outside a slot (the tests that run the repository's ``arthur``) no copy is
kept or read. (:func:`arthur.onclock.app.start_config`, ``ClockApp.keep_config``.)

Returns the exit code: 0 to stop for good, 42 to be restarted, 1 if the program failed (the
traceback is printed; boot.py counts it as a failed start), as when no config loads at all.
A failure also leaves one error line in the log, with the exception's type name only, and
whatever the log holds unwritten goes to the flash before ``main`` returns.

:func:`healthy` is what boot.py asks, besides the door's greeting, before it calls a start
good: a frame reached AWTRIX within the last :data:`HEALTHY_PUSH_S` seconds.

**Tests only:** the environment variable ``ARTHUR_ONCLOCK_TEST`` (a JSON object) points the
program at fakes: ``awtrix`` (base URL), ``ca_file``, ``server_name`` (the name the test
certificates carry), ``weather_url``, ``lr4_url``, ``cognito_url``, ``door_port`` and
``door_host``, ``log_store`` (true: keep the log on the flash outside a slot too), and
``client`` (keyword arguments for the Whisker client, such as ``stale_s``). On the clock the
daemon starts the launcher with a clean environment, so none of this applies there.
"""

import sys

from arthur.onclock.app import ClockApp, trace_text
from arthur.onclock.clocklog import DEFAULT_MAX_BYTES, ClockLog
from arthur.wire import util
from arthur.wire.const import CLOCK_CA_FILE
from arthur.wire.net import Tls

EXIT_FAILED = 1
#: The folder under the root that holds the log on the flash.
LOG_DIR = "log"
TEST_ENV = "ARTHUR_ONCLOCK_TEST"
#: Healthy means a frame reached AWTRIX at most this long ago.
HEALTHY_PUSH_S = 60.0

#: The running app, for :func:`healthy`.
_current = {}


def healthy():
    """For boot.py's health probe: the program is drawing (not paused, a frame pushed to
    AWTRIX within :data:`HEALTHY_PUSH_S`)."""
    app = _current.get("app")
    if app is None:
        return False
    try:
        return bool(app.drawing(HEALTHY_PUSH_S))
    except Exception:  # noqa: BLE001 - a probe never raises
        return False


def test_settings():
    """The ``ARTHUR_ONCLOCK_TEST`` object, or ``{}``."""
    import json
    import os

    try:
        text = os.getenv(TEST_ENV)
    except Exception:  # noqa: BLE001
        text = None
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def slot_dir():
    """The code slot this program was imported from (``.../slots/a``), or None when it was
    not (the tests run the repository's ``arthur``)."""
    import arthur

    path = getattr(arthur, "__file__", None) or ""
    cut = path.rfind("/arthur/")
    if cut < 0:
        return None
    return path[:cut]


def _norm(path):
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/") if len(path) > 1 else path


def slot_name(root):
    """``"a"`` or ``"b"`` when this program was imported from that slot of ``root``
    (:func:`slot_dir` is ``<root>/slots/a``), else None."""
    folder = slot_dir()
    if not folder:
        return None
    folder = _norm(folder)
    for s in ("a", "b"):
        if folder == _norm(root + "/slots/" + s):
            return s
    return None


def bundle_version(folder):
    """The bundle id of the slot at ``folder`` (the first 16 hex digits of its manifest's
    SHA-256, as ``scripts/clock.py`` and the door name it), or None."""
    if not folder:
        return None
    try:
        with open(folder + "/manifest.json", "rb") as f:
            data = f.read()
    except OSError:
        return None
    from arthur.onclock.door import bundle_id

    return bundle_id(data)


async def main(root, source_factory, *, ca_file=None, base_url=None,
               log_bytes=DEFAULT_MAX_BYTES, door_port=None, settings=None):
    from arthur.onclock import door as doormod
    from arthur.onclock.app import start_config

    _current.clear()
    door = None
    log = None
    try:
        test = test_settings() if settings is None else settings
        root = root.rstrip("/") if len(root) > 1 else root
        ca_file = ca_file or test.get("ca_file") or CLOCK_CA_FILE
        base_url = base_url or test.get("awtrix") or "http://127.0.0.1"
        if door_port is None:
            door_port = test.get("door_port") or doormod.DOOR_PORT
        slot = slot_name(root)
        # In a slot the log is kept on the flash too, under <root>/log (D-0035); the tests
        # that run the repository's arthur keep it in RAM only, as before.
        store = None
        if slot is not None or test.get("log_store"):
            from arthur.onclock.logstore import LogStore

            store = LogStore(root + "/" + LOG_DIR)
        log = ClockLog(log_bytes, store=store)
        # config.json, else a kept copy (an error, said once below); raises if none loads.
        cfg, cfg_data, cfg_error = start_config(root, slot)
        tls = Tls(ca_file=ca_file, server_name=test.get("server_name"))
        wire = {}
        client = dict(test.get("client") or {})
        for k in ("lr4_url", "cognito_url"):
            if test.get(k):
                client[k] = test[k]
        if client:
            wire["wire_kw"] = {"client_kw": client}
        source = source_factory(cfg=cfg, log=log, tls=tls, root=root, **wire)
        app = ClockApp(source, root=root, base_url=base_url, ca_file=ca_file, log=log,
                       version=bundle_version(slot_dir()), cfg=cfg, tls=tls,
                       weather_url=test.get("weather_url"), slot=slot,
                       config_data=cfg_data, config_error=cfg_error)
        _current["app"] = app
        app.settle_config()
        cfg_data = None  # the app has kept what it needs; not held for the whole run
        try:
            door = await doormod.start(app, root, host=test.get("door_host") or "0.0.0.0",
                                       port=door_port)
        except doormod.DoorError as exc:
            app.console("warning", "the update door stays closed: " + str(exc)
                        + "; the program runs without it")
        except OSError as exc:
            app.console("error", "the update door could not open on port " + str(door_port)
                        + ": " + type(exc).__name__ + " " + str(exc)
                        + "; the program runs without it")
        return await app.run()
    except Exception as exc:  # noqa: BLE001 - the boot script counts a non-zero exit
        print("arthur on the clock failed:", file=sys.stderr)
        print(trace_text(exc), file=sys.stderr)
        if log is not None:
            # The type's name only: an exception's text can carry what it was handed.
            try:
                log.append({"kind": "console", "ts": util.utc_iso_ms(util.now()),
                            "level": "error", "name": "arthur.clock",
                            "msg": "the program stopped on an error: " + type(exc).__name__})
            except Exception:  # noqa: BLE001, S110 - the exit code is what counts
                pass
        return EXIT_FAILED
    finally:
        if door is not None:
            door.close()
        if log is not None:
            # What this run logged reaches the flash, a start that failed included.
            try:
                log.flush()
            except Exception:  # noqa: BLE001, S110 - never changes the exit code
                pass
        _current.clear()
