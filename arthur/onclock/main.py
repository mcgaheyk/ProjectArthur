"""What the boot script on the clock calls: build the program and run it.

    import asyncio
    from arthur.onclock.main import main
    code = asyncio.run(main("/data/awtrix-ng/state/arthur", source_factory))

In order: the log ring; ``<root>/config.json``; the shared TLS context for the clock's CA
bundle; the robot source, from ``source_factory(cfg=, log=, tls=, root=)``
(``deploy/clock/boot.py`` hands :func:`arthur.onclock.source.make_source` through); the
:class:`~arthur.onclock.app.ClockApp`, reporting the slot's bundle id as its version; the
update door (:func:`arthur.onclock.door.start`), and without a valid key in
``secrets.json``, or if its port is taken, the program carries on without it and says so;
then the app's loop until it asks to stop or be restarted.

Returns the exit code: 0 to stop for good, 42 to be restarted, 1 if the program failed (the
traceback is printed; boot.py counts it as a failed start).

:func:`healthy` is what boot.py asks, besides the door's greeting, before it calls a start
good: a frame reached AWTRIX within the last :data:`HEALTHY_PUSH_S` seconds.

**Tests only:** the environment variable ``ARTHUR_ONCLOCK_TEST`` (a JSON object) points the
program at fakes: ``awtrix`` (base URL), ``ca_file``, ``server_name`` (the name the test
certificates carry), ``weather_url``, ``lr4_url``, ``cognito_url``, ``door_port`` and
``door_host``, and ``client`` (keyword arguments for the Whisker client, such as ``stale_s``). On the clock the
daemon starts the launcher with a clean environment, so none of this applies there.
"""

import sys

from arthur.onclock.app import ClockApp, trace_text
from arthur.onclock.clocklog import DEFAULT_MAX_BYTES, ClockLog
from arthur.wire.const import CLOCK_CA_FILE
from arthur.wire.net import Tls

EXIT_FAILED = 1
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
    from arthur.config import load_config_json
    from arthur.onclock import door as doormod

    _current.clear()
    door = None
    try:
        test = test_settings() if settings is None else settings
        root = root.rstrip("/") if len(root) > 1 else root
        ca_file = ca_file or test.get("ca_file") or CLOCK_CA_FILE
        base_url = base_url or test.get("awtrix") or "http://127.0.0.1"
        if door_port is None:
            door_port = test.get("door_port") or doormod.DOOR_PORT
        log = ClockLog(log_bytes)
        cfg = load_config_json(root + "/config.json")
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
                       weather_url=test.get("weather_url"))
        _current["app"] = app
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
        return EXIT_FAILED
    finally:
        if door is not None:
            door.close()
        _current.clear()
