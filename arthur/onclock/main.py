"""What the boot script on the clock calls: build the :class:`~arthur.onclock.app.ClockApp`
and run it.

    import asyncio
    from arthur.onclock.main import main
    code = asyncio.run(main("/data/awtrix-ng/state/arthur", make_source))

``source_factory(cfg=..., log=..., tls=...)`` builds the robot source: ``cfg`` is the
:class:`~arthur.config.Config` read from ``<root>/config.json``, ``log`` the
:class:`~arthur.onclock.clocklog.ClockLog` it writes the robot's lines to, ``tls`` the
:class:`~arthur.wire.net.Tls` for the clock's CA bundle, shared with the weather so the
bundle is loaded once. Nothing here reads secrets or starts the update door.

Returns the exit code: 0 to stop for good, 42 to be restarted, 1 if the program failed (the
traceback is printed).
"""

import sys

from arthur.onclock.app import ClockApp, tls_for, trace_text
from arthur.onclock.clocklog import DEFAULT_MAX_BYTES, ClockLog
from arthur.wire.const import CLOCK_CA_FILE

EXIT_FAILED = 1


async def main(root, source_factory, *, ca_file=CLOCK_CA_FILE, base_url="http://127.0.0.1",
               log_bytes=DEFAULT_MAX_BYTES):
    from arthur.config import load_config_json

    try:
        log = ClockLog(log_bytes)
        cfg = load_config_json(root.rstrip("/") + "/config.json")
        source = source_factory(cfg=cfg, log=log, tls=tls_for(ca_file))
        app = ClockApp(source, root=root, base_url=base_url, ca_file=ca_file, log=log)
        return await app.run()
    except Exception as exc:  # noqa: BLE001 - the boot script restarts us on a non-zero exit
        print("arthur on the clock failed:", file=sys.stderr)
        print(trace_text(exc), file=sys.stderr)
        return EXIT_FAILED
