"""The program that runs on the clock itself, under MicroPython 1.29 (D-0029).

- :mod:`arthur.onclock.door`: the update door, how changes arrive from the Mac over Wi-Fi.
  Protocol and threat list: ``docs/reference/onclock.md``.

Started by ``deploy/clock/boot.py`` (stage 0, never replaced over Wi-Fi), which calls
``arthur.onclock.main.main(root, source_factory)``.
"""
