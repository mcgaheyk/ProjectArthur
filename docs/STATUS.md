# STATUS.md — project memory

_Last updated: 2026-11-15._ Current state only. History belongs in git and DECISIONS.md.

## Where things stand
- **Hardware:** nothing flashed or deployed. Host will be a Raspberry Pi on the LAN (D-0006).
  The display is **not chosen yet**: Ulanzi TC001 (32x8, about $50) or TC002 (52x16, about
  $100). Every screen is designed at both sizes so the choice can be made from mock-ups (D-0001).
- **Firmware target:** AWTRIX NG pinned to 1.2.3 (D-0002). The feeder draws every pixel and
  pushes a bitmap (D-0003).
- **Built:** project skeleton, docs, skills. Core pipeline, simulator and spike are in progress
  (first build, 2026-11-15).

## Next
1. Kyle: copy `.env.example` to `.env`, add the Whisker login, and leave the spike running for a
   few days so real visits get recorded.
2. Kyle: review the screens in the studio and choose a display size.
3. Feed spike findings back into `docs/states.md` and `config/display.toml` (phase durations,
   cat-detected latency, what the raw fields add).

## Later
- The feeder service itself: `arthur/sources/whisker.py` and the run loop with heartbeat.
- Pi deployment: systemd unit, reconnect with backoff, log rotation, and a memory check
  (pylitterbot pulls in boto3/botocore).
- Flash AWTRIX NG and compare the simulator against the clock's own screen read-back.
- Optional: build `awtrix-linux` (NG's host build) as a conformance check for the transport.
- On-device watchdog script for a louder stale screen. Review NG's Berry agent skill first.
- Alert sounds, button or knob acknowledgement, scheduled night dimming (needed on the TC002).
- Health nudges: no visit in N hours, unusually many visits.
- Experiment: the no-cloud ESPHome firmware for the robot itself.

## Known unknowns
- How fast Whisker pushes "cat detected". Nobody has published a measurement.
- How long each phase really lasts, and whether the raw fields reveal cycle phases.
- How colours and dim shades read on real LEDs.
- TC002: whether AWTRIX NG support is out of beta, whether it has a light sensor, its physical
  size. Check before buying one.
- AWTRIX NG: whether re-pushing an app clears the stale mark (inferred, not confirmed), and
  what an empty rotation shows after a clock reboot.
