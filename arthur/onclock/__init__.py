"""The program that runs on the clock itself, under MicroPython 1.29 (D-0029).

It does on the TC002 what ``arthur/feeder.py``, ``pusher.py``, ``transports/awtrix_ng.py`` and
``sources/weather.py`` do on the Mac: on the beat of the wall clock it asks the robot source,
maps, draws, and pushes the frame to AWTRIX's own HTTP API on ``127.0.0.1``. Every module here
runs unchanged on CPython 3.13 too, so the normal test suite exercises it; the rules for that
are CLAUDE.md's shared-code rules (``docs/reference/micropython.md``).

Started by ``deploy/clock/boot.py`` (stage 0, never replaced over Wi-Fi), which calls
``arthur.onclock.main.main(root, source_factory)``.

- :mod:`~arthur.onclock.app`: :class:`~arthur.onclock.app.ClockApp`, the loop.
- :mod:`~arthur.onclock.main`: ``main(root, source_factory)``, what the boot script calls.
- :mod:`~arthur.onclock.clocklog`: the log ring kept in memory.
- :mod:`~arthur.onclock.awtrix`: the transport to AWTRIX on the same device.
- :mod:`~arthur.onclock.pusher`: when a frame is pushed.
- :mod:`~arthur.onclock.weather`: Open-Meteo over verified TLS.
- :mod:`~arthur.onclock.httpconn`: one kept HTTP/1.1 connection.
- :mod:`~arthur.onclock.clock`: wall and monotonic time, sleeping, waiting with a stop.
- :mod:`~arthur.onclock.door`: the update door, how changes arrive from the Mac over Wi-Fi.
  Protocol and threat list: ``docs/reference/onclock.md``.

The robot source is not here: the app takes any object with the feeder's ``Source`` protocol
(``arthur/sources/wire_source.py`` is the real one).
"""

#: The program's version, reported by ``ClockApp.status()``. The installer may rewrite it.
VERSION = "0.1-dev"
