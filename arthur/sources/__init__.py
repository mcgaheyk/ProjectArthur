"""Sources: where the facts on the display come from.

- ``whisker``: the live robot through Whisker's cloud, for the feeder. With
  ``arthur.spikelog`` it is the only code that imports pylitterbot. It writes the spike's
  ``logs/spike-*.jsonl``.
- ``logfile``: reads those logs back as robot snapshots, for the studio. Read only.
- ``tracker``: what the feeder observed over time (when a status began, when the cat left),
  shared by both.
- ``weather``: the idle show's weather from Open-Meteo, a decoration the feeder runs beside
  the Whisker source (D-0019).
"""
