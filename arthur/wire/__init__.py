"""Our own read-only client for Whisker's Litter-Robot 4 cloud, for CPython and MicroPython.

pylitterbot cannot run on the clock (D-0029), so this package speaks the same wire protocol
itself, with the standard library only (and only the parts MicroPython 1.29's unix port has).
What goes over the wire, and where in pylitterbot and pycognito it was read, is in
``docs/reference/whisker-wire.md``.

Modules:

- :mod:`arthur.wire.const`: endpoints, ids, timings, our User-Agent.
- :mod:`arthur.wire.util`: base64, JWT claims, HMAC-SHA256, UTC time without ``datetime``.
- :mod:`arthur.wire.net`: TLS contexts (always verified) and streams.
- :mod:`arthur.wire.http`: a small HTTP/1.1 client.
- :mod:`arthur.wire.ws`: a small WebSocket client.
- :mod:`arthur.wire.cognito`: token renewal and the SRP password login.
- :mod:`arthur.wire.lr4`: the Litter-Robot 4 queries, the subscription and the history.
- :mod:`arthur.wire.record`: the record ``arthur.spikelog.build_record`` makes, from raw data.
- :mod:`arthur.wire.client`: :class:`~arthur.wire.client.WhiskerClient`, the calls.
- :mod:`arthur.wire.monitor`: :class:`~arthur.wire.monitor.Monitor`, the connection policy.

Nothing here sends a command to the robot.
"""
