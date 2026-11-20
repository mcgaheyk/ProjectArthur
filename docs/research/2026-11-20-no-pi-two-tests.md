# Two tests for running the clock with no Pi and no Mac (2026-11-20)

_Outcome: both routes passed their first test, each with one thing still unproven. The
choice between them is Kyle's and is not made in this note._

The question: the feeder runs on Kyle's Mac, so the clock shows the stale border whenever
the Mac sleeps. Two routes would end that without a Pi. Each had one unknown that could rule
it out, so each got a cheap test before anything was built.

- **Cloud relay:** the feeder on Railway, sending frames to the clock through an MQTT broker.
  Unknown: does Whisker accept a login from a data centre?
- **On the clock:** a program on the TC002 itself. An earlier proposal by another AI system
  (`ProjectArthur-TC002-Technical-Plan.md` in this folder, Kyle's file) suggested a rewrite
  in a compiled language. Tested here instead: our own Python, run by MicroPython, so one
  codebase serves the studio and the clock. Unknown: can the clock run it fast enough?

The Mac's feeder was stopped from 11:48:24 to 12:46:49 for the live parts. Only one program
was connected to the Whisker account at any time.

## Test 1: Whisker from Railway

The feeder's image (`deploy/feeder/Dockerfile`) ran in Railway's US East region for 43
minutes with Kyle's login, typed in by him and sealed. Its clock address pointed at nothing.

| Evidence wanted | Seen |
|---|---|
| Login | "Whisker connected" 4 seconds after start |
| Robot found, state read | Yes: `ready`, and the history gave the last visit |
| Polls | 8 poll times passed with no error line |
| 401, 403 or lost contact | None |
| A live event over the open connection | **Not seen: no visit happened in the 43 minutes** |
| A token renewal | **Not reached** |

**Result: the login and the reads pass. Live events from a data centre are unproven.**

Railway notes: CLI 5.31's `environment edit` and `volume add` did not work; the service was
set through Railway's API (`serviceInstanceUpdate`, `volumeCreate`). The service is set to
stop the old copy before a new one starts (`overlapSeconds` 0), because Whisker allows one
connection. Saving variables in the dashboard and pressing Deploy starts the service.

## Test 2: our Python on the clock

**A. On the Mac.** A slice of the real drawing code (fonts, art, the idle home screen, the
bird scene, most other screens) runs under MicroPython 1.29 from one source that CPython
also runs. All 1,760 frames are identical on both. About 3% of lines changed, mechanically.
Costs found: four behaviours that differ with no error (dict order, `sorted()` stability,
`isinstance(True, int)`, a folder hiding a module), no `random.Random`, no time zones.
Details: `spike/mpy/RESULTS-2A.md` on the local branch `spike/micropython-slice`.

**B. On Kyle's TC002, over USB.** `adb` works while AWTRIX NG runs, with the cable in the
clock itself, not its charging base.

| Measure | Result | Pass line |
|---|---|---|
| Memory | 33 MiB total, 13 MiB available | — |
| Storage | 7.6 MiB free of 8 on `/data` | — |
| Home screen, ms a frame | mean 15.6, worst 24.4 | worst ≤ 60: pass |
| Bird scene as written | mean 93.4, worst 106.7 | **fail** |
| Bird scene, plan built once and kept | mean 10.8, p95 21, worst 86.9 | one frame per scene over the line |
| Pixels against the Mac | identical in every run | pass |
| Our program's memory | 1.5 MiB (1 MiB heap; 640 KB is too small) | under 6 MiB: pass |
| Ten minutes of drawing and pushing at 8 a second | 1,323 pushes, 0 errors, 1 beat missed of 4,800, both read-backs exact | pass |
| AWTRIX meanwhile | 42 frames a second, 10.8 MiB free at the lowest | ≥ 35 and ≥ 6 MiB: pass |
| TLS, certificate checked | handshake 0.31 s; wrong CA, no CA and wrong name all refused | pass, after a fix |
| Autostart | came up alone after a cold boot with the cable out; removed cleanly | pass |

The clock is 25 to 30 times slower than the Mac under MicroPython.

Three findings worth keeping:

1. **The bird scene re-plans itself on every frame.** That was 82% of its cost. Building the
   plan once per scene play made it nine times faster on the clock with identical pixels.
   The one frame that builds the plan takes 87 ms: inside the 125 ms beat, over the 60 ms
   line. Building it during the frames before the scene would remove that. Not done.
2. **The clock has almost no entropy** (10 to 57 bits), and its kernel (4.9) blocks reads of
   `/dev/random` when entropy is low. MicroPython 1.29 and the mbedTLS 3.6.6 inside it both
   read `/dev/random` unless built with glibc, so creating a TLS context hung for minutes.
   The build recipe now makes both use `getrandom()`. Emulation never showed this. Any
   program doing TLS on this clock must avoid `/dev/random`.
3. **The firmware's autostart slot works as documented**: one file at
   `/data/awtrix-ng/state/autostart`, here a four-line shell script starting MicroPython.
   The test install used 537 KB of storage.

**Result: it works. The one miss is a single 87 ms frame per scene, with a known next step.**

Everything from part B is on the local branch `spike/micropython-slice` (the kit is
`spike/mpy/clock/kit.py`, the measurements `spike/mpy/clock/results/`). It is not in `main`.

## What neither test covered

| | Cloud relay | On the clock |
|---|---|---|
| Still unproven | Live events from a data centre; how scenes look when frames cross the internet | A Whisker connection in MicroPython (login, live events, weeks of uptime); memory with everything running at once |
| Still to build | An MQTT transport, a broker, the deployment | The rest of the code made to run on both Pythons, with the pixel comparison in the test suite; a Whisker connection of our own; sending changes over Wi-Fi; a home for the visit log; Boston's clock time without a time-zone library |
| Runs on | Railway, about $5 a month, the Whisker login stored there | The clock alone; the login stays at home |
| A change reaches the clock by | One deploy, a minute or two | Sending files over Wi-Fi, seconds, once that is built |
| Whisker changes its API | pylitterbot's maintainer fixes it; we redeploy | We fix our own connection |
