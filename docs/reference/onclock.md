# The program on the clock: layout, start-up, the update door

How the on-clock program (D-0029) is installed, started, updated and removed. Written
2026-11-20 with the code: `deploy/clock/` (the build, `boot.py`, `autostart.sh`,
`clockctl.py`), `arthur/onclock/door.py`, `arthur/tools/onclock_files.py`,
`arthur/tools/doorclient.py`, `arthur/clocktool.py` (`scripts/clock.py`); joined into one
program the same day (`arthur/onclock/main.py`, `source.py`; "How the pieces join").
**None of it has run on the real clock yet**: it was proven against temporary folders, local
sockets, the Mac's MicroPython and an emulated ARMv7 "clock" in Docker, the whole program end
to end against fakes (`tests/test_onclock_e2e.py`). The clock's own facts are in
`awtrix-ng.md`, "Running our own program on the TC002"; what this note assumes about the
clock beyond them is at the end.

## Layout

Everything of ours on the flash is under one root, `ROOT=/data/awtrix-ng/state/arthur`, plus
the autostart file. All paths in the code derive from the root, so the tests use a temporary
folder.

| Path | What | Written by |
|---|---|---|
| `/data/awtrix-ng/state/autostart` | the launcher (`deploy/clock/autostart.sh`), root, mode 700, carries the marker `ARTHUR-AUTOSTART` | `install` only |
| `ROOT/micropython` | MicroPython 1.29.0, static ARMv7 (521,364 bytes) | `install` only |
| `ROOT/boot.py` | stage 0 (11 KB) | `install` only |
| `ROOT/slots/a/`, `ROOT/slots/b/` | the code slots: `arthur/` (compiled `.mpy`, the package `__init__`s too), fonts (`.bdf`), art (`.txt`), `lib/` (the stand-ins from `mpy/lib/`, as source), `manifest.json` | `install` (a), the door (the inactive one) |
| `ROOT/state.json` | active slot, failed starts, errors, halted | boot.py, the door |
| `ROOT/config.json` | the exported config (`arthur.tools.export_config`), outside the slots so a colour or caption needs no new slot | `install`, the door |
| `ROOT/secrets.json` | mode 600: `whisker_username`, `whisker_password` (D-0030), `door_key` (64 hex digits) | `install` only |
| `ROOT/tokens.json` | Whisker's tokens (`access_token`, `id_token`, `refresh_token`), written whole (temporary file and rename) and **only when the refresh token changes**, i.e. after a password login; an hourly renewal never writes the flash | the program (`arthur/onclock/source.py`) |
| `/tmp/arthur/` | RAM: `boot.txt` (boot's notes), `console.log` and `console.prev.log` (the program's stdout and stderr, from the launcher), `booted` (the power-on marker) | the launcher, boot.py |

A slot's `manifest.json` is canonical JSON, `{"v": 1, "files": {path: [sha256, size]}}`,
sorted, no spaces; the **bundle id** is the first 16 hex digits of its SHA-256. The file list
comes from `arthur/tools/onclock_files.py` (`SHARED`, `ONCLOCK`, `PACKAGES`, `DATA`,
`STANDINS`), the one place the shared-code list lives as data.

**Storage, measured with the emulated install** (sizes of the files; JFFS2 compresses, so the
flash used should be less): one slot 290,779 bytes in 99 files (compiled; 525,199 as source);
an install with one slot about 826 KB; with both slots full 1,117,315 bytes. `/data` had
7,808 kB free on Kyle's clock, and `install` refuses unless 2 MiB stays free after it; the
door refuses a transfer on the same rule.

## Starting up

**The launcher** (`autostart.sh`, shell builtins plus `mkdir` and `cp`): sets `umask 077`
(what the program writes, `tokens.json` above all, is root's alone), makes `/tmp/arthur`,
keeps the last console as `console.prev.log`, and runs
`ROOT/micropython -X heapsize=3M ROOT/boot.py ROOT /tmp/arthur` in a loop. Exit 42 starts it
again at once; any other code is the launcher's own exit code, for the daemon (0: stay off
until the next start; anything else: the daemon's back-off, and off after 5 failures in a
row). SIGTERM, SIGINT or SIGHUP to the launcher stops the program too. `install` fills in
`ROOT`, `TMP` and `HEAP` (`--heap`).

**boot.py** (stage 0, built-in modules only, never replaced over Wi-Fi), each start:

1. Reads `state.json` (anything unreadable reads as slot a, no tries).
2. Picks the active slot, unless it has failed `MAX_TRIES` = 3 starts in a row or its files
   do not match its manifest (every file is hashed at each start); then the other slot if it
   is whole (the fall-back makes it the active one). Neither: writes `halted` and exits 3.
3. Counts the start in `state.json` **before** importing anything from the slot.
4. Puts `slot/` and `slot/lib/` first on `sys.path`, imports `arthur.onclock.main` and runs
   `asyncio.run(main(ROOT, source_factory))`. `source_factory` imports
   `arthur.onclock.source` from the slot and calls its `make_source(**kwargs)`; only that
   name is fixed in boot.py.
5. **Health:** after 120 s, and then every 15 s until it is, it asks two things: does the
   door answer on 127.0.0.1 with its greeting, and does the program say it is drawing
   (`healthy()` in the slot's `arthur.onclock.main`: not paused, and a frame reached AWTRIX
   within the last 60 s). Both yes: the slot's count goes back to 0 and its error is cleared.
   A door that answers while nothing is drawn is noted once in `boot.txt`. Why both: the door
   answering means the program can be fixed from the Mac; drawing means it is doing its job.
   A program that can be fixed but cannot draw (a transport broken by new code, say) must
   not wipe its count, or a fall-back could never happen. A program without `healthy()` (an
   older slot, the tests' stub) is judged by its door alone. A program is never killed for
   being unhealthy: it stays counted, so its next start (a crash, a restart, a power cycle)
   brings the fall-back closer. Paused from the Mac counts as not drawing, so a slot is not
   called healthy while paused; the probe carries on and passes after `resume`.
6. `main` returns 0 (stop: exit 0) or 42 (exit 42: the launcher starts it again). An import
   error, an exception from `main`, **or any other exit code** (`main` itself catches what
   fails inside it, prints the traceback to the console and returns 1) is written to
   `state.json` (`errors.<slot>`, 200 characters at most: `import: ...`, `run: ...` or
   `run: exited with 1 (see console.log)`), and after 5 s boot exits 42, so the launcher
   (not the daemon) starts it again and the count decides. A crash that kills the process (a
   signal) reaches the daemon; the count still works across those starts.
7. **Both slots spent:** `halted` is written once and boot exits 3 at every start after that
   without writing again, so the daemon's back-off ends it. The first start after a power-on
   (no `/tmp/arthur/booted`) clears `halted` and gives both slots one more round, in case the
   cause has passed (no Wi-Fi, say).

**The one file to read** when the clock shows nothing: `ROOT/state.json`, e.g.
`{"v": 1, "active": "a", "tries": {"a": 3, "b": 3}, "errors": {"a": "import: ...", "b":
"run: ..."}, "halted": "both slots failed; see errors"}`. `/tmp/arthur/boot.txt` and
`console.log` have the detail (RAM). `scripts/clock.py --serial S status` and `logs` read
them over the cable.

**Writes to the flash:** one small `state.json` per start, one when healthy, one per recorded
error; none while halted. A restart from the Mac or a new bundle adds one (the door).

**Tests only:** boot.py and `main` read `ARTHUR_ONCLOCK_TEST` (a JSON object) from the
environment, which points the program at fakes and shortens boot's waits (`door_port`,
`healthy_after`, `probe_every`, `crash_pause`; for `main` also `awtrix`, `ca_file`,
`server_name`, `weather_url`, `lr4_url`, `cognito_url`, `door_host`, `client`). The daemon
starts the launcher with a clean environment (assumed; the emulated daemon does, and passes
the variable only from a test file), so on the clock it is always empty.

## How the pieces join

In the order they happen at each start:

1. **The launcher** (`autostart.sh`) runs `micropython -X heapsize=$HEAP boot.py ROOT TMP`.
2. **boot.py** picks and counts the slot, imports `arthur.onclock.main` from it and starts the
   health watch beside `main`.
3. **`main(root, source_factory)`** (`arthur/onclock/main.py`), in order: the log ring
   (`ClockLog`, 128 KB of JSON text); `ROOT/config.json` (a file that does not load ends the
   start with exit 1, a counted failure); one `Tls` for the CA bundle, shared by Whisker and
   the weather; the robot source, `source_factory(cfg=, log=, tls=, root=)` (boot hands
   `arthur.onclock.source.make_source` through); the `ClockApp`, whose version is the slot's
   **bundle id** (read from the `manifest.json` next to the `arthur` package it was imported
   from; `0.1-dev` outside a slot); the door (`door.start(app, root)`: without a valid key in
   `secrets.json` it logs "the update door stays closed" and the program runs without it; a
   port already taken is logged the same way); then `app.run()`.
4. **`ClockApp.run`**: loads the fonts, the art and the CA bundle while the heap is in one
   piece; waits for AWTRIX's API; starts the source and the weather as tasks; then the beat.
5. **The source** (`ClockSource` in `arthur/onclock/source.py`), on its first run: waits
   until the system time is past 2026-01-01 (a clock booted without Wi-Fi may think it is
   1970; TLS would refuse every certificate as not yet valid and each try would count as a
   failed login), checking every 2 s and saying so once; reads the login from
   `secrets.json` (missing, unreadable or without both values: NO DATA with the `auth`
   reason, an `error` line at `login`, and the file read again after 600 s, doubling to an
   hour); builds the `WireSource` with the token file, the log ring as its sink and the
   console lines into the ring; then the wire source's connection (token, else a password
   login in 4-bit slices; the live subscription; polls; the history at start).
6. **Pause and resume** (the door): `pause()` stops pushing and stops the source task, which
   closes the live connection (the Mac's feeder may then take the screen and the account).
   `resume()` starts the task again; a second run calls `WireSource.forget_contact()`: the
   last record is dropped (NO DATA until the first contact, never the old state as if
   current) and the first contact is the end of a gap in watching (D-0025) however short, so
   the history is read and the last visit can move. The tracker keeps the last visit. A
   source task that died of a bug and is restarted takes the same path.

**The log line shapes**, as they come out of the door and as `scripts/clock.py logs pull`
writes them on the Mac (checked by `tests/test_onclock_e2e.py`, which replays the pulled
files through the studio's reader):

| From | `kind` | Shape | On the Mac |
|---|---|---|---|
| the wire source | `state`, `info`, `error` | the spike's lines (`arthur.sources.spikeformat`) | `logs/spike-YYYYMMDD.jsonl` |
| the wire source | `activity` | `{"fetched", "kind", "timestamp", "action", "code"?}` | `logs/spike-activity-YYYYMMDD.jsonl` |
| the clock source, no login | `error` | `where: "login"`, `retry_in`, a message naming the file, never a value from it | `logs/spike-*.jsonl` |
| the app | `console` | `ts` (`...mmmZ`), `level` (lower case), `msg`, `trace`? (no `name`) | `feeder-console.log`, as `arthur.clock`, level upper-cased |
| the source | `console` | the same with `name: "arthur.whisker"` | `feeder-console.log` |
| the door | `console` | the same with `name: "arthur.door"`, level upper case | `feeder-console.log` |

Everything printed (the app's and the source's lines, boot's notes, tracebacks) also goes to
`/tmp/arthur/console.log`.

## The program's side (the contract)

```
class ClockApp:
    async def run(self) -> int          # 0 = stop for good, 42 = restart me
    def status(self) -> dict            # small, JSON-able
    def pause(self) -> None             # stop pushing and stop the source
    def resume(self) -> None
    def reload_config(self) -> None     # read <root>/config.json again
    def request_restart(self) -> None   # run() returns 42 at the next beat
    def request_stop(self) -> None
    def drawing(self, within_s) -> bool  # not paused, a frame pushed within within_s
    log: ClockLog                       # .append(line) -> seq ; .read(after_seq, max_bytes)
arthur/onclock/main.py:  async def main(root, source_factory, *, ca_file=None,
                             base_url=None, log_bytes=..., door_port=None,
                             settings=None) -> int
                         def healthy() -> bool   # for boot.py's health probe
arthur/onclock/source.py: def make_source(*, cfg, log, tls, root, **kw) -> ClockSource
```

`status()` also carries, for the Mac: `version` (the bundle id), `heap_after_gc` and
`heap_after_gc_max` (the heap in use just after the scheduled collections), `beats` and `gc`
(milliseconds of work per beat by what was drawn, mean and worst), `late_beats` (beats whose
work took over 125 ms), `woke_late` and `worst_late` (beats that woke over 20 ms after their
tick, and the eight worst as `[epoch s, ms, what was on screen]`: what blocked the loop),
`late_by_part` (the late wakes by what was on screen, each `[count, worst ms]`),
`poll_held` (`[count, longest wait s]` of Whisker polls held back from pages and scenes,
D-0033) and `live_up` (whether Whisker's live subscription is acknowledged and up, from the
monitor; `health.ws_connected` beside it is always true and tells nothing).

`main` opens the door with `door = await arthur.onclock.door.start(app, root)` (port 7531
on every interface; it raises `DoorError` without a valid key in `secrets.json`, and `main`
carries on without it, saying so) and closes it with `door.close()` when `run()` returns. The tests' stub is
`tests/clockstub.py`. What the door assumes of the rest:

- `log.read(after_seq, max_bytes)` returns the lines numbered above `after_seq` (at least
  one if there is one, then as many as fit in `max_bytes`) and the number to pass as
  `after_seq` next time. Numbers start again when the program restarts; the door's `run` id
  tells the Mac.
- **Log lines are dicts with a `kind`**, which says where the Mac puts them:
  `state`, `info`, `error` go to `logs/spike-YYYYMMDD.jsonl` and `activity`, `pet_weight` to
  `logs/spike-activity-YYYYMMDD.jsonl`, exactly as `arthur.spikelog` writes them (so the
  studio's Replay and Follow work), by the local date of their `ts` (or `fetched`);
  `console` lines (`ts` as `YYYY-MM-DDTHH:MM:SS.mmmZ`, `level`, `name`, `msg`) go to
  `logs/feeder-console.log` as the feeder's logging writes them; anything else goes there as
  JSON.
- A config the door has installed has passed `arthur.config.config_from_json` (or, with a
  bundle, came from the Mac's export with the same code). **The program does not survive a
  bad one at start**: `main` returns 1, a counted failure, and both slots read the same
  `config.json`, so a bad file would halt both. A `config-put` of a bad file is refused; a
  reload of one keeps the last good config. Only a bundle's config is not checked by the
  code that will read it (see Not done).

## The update door

`arthur/onclock/door.py`, inside the program, TCP port **7531**, one connection = a
challenge, then any number of requests.

```
door → Mac   b"ARTHUR-DOOR/1 " + 32 hex digits (16 random bytes: the challenge N) + b"\n"
Mac → door   struct ">HI" (head length 1..2048, body length 0..32768)
             head (JSON object, {"verb": ..., ...}) + body
             tag = HMAC-SHA256(key, b"ARTHUR-DOOR/1 req" + N + header + head + body)
door → Mac   struct ">I" (length) + answer (JSON, with "ok" and the next challenge "nonce")
             tag = HMAC-SHA256(key, b"ARTHUR-DOOR/1 res" + N + header + answer)
```

The tag is checked (in constant time) before the head is parsed. A bad tag, or lengths over
the limits, get exactly `b"ARTHUR-DOOR/1 NO\n"` and the connection closes (after 1 s for a
bad tag). Every read times out after 20 s, and once a request's 6-byte header is in, the
rest of it must arrive within 30 s; at most 3 connections at once (more are closed with no
greeting) and 2,000 requests each. The Mac checks the answer's tag.

**The door's buffers: nothing unauthenticated costs memory.** MicroPython's heap cannot be
compacted and the program has a few MiB, so a request is never read into memory of its own
before it is known to be the Mac's. The door makes, once, when it opens: one request buffer
of 34,816 bytes (`MAX_HEAD` + `MAX_BODY`), a 32-byte tag buffer, and the HMAC key's two
padded blocks. For each request it reads the 6-byte header (the one allocation before the
tag, beside the event loop's own small objects), checks the lengths, then takes the door's
one lock: **one request at a time across all connections**. The head, body and tag are read
straight into the fixed buffers (`readinto` on MicroPython; CPython, in the tests, reads
512-byte pieces and copies them in), and the tag is computed over the buffer as it stands
(SHA-256 updated over a view of it, inside the precomputed HMAC blocks). Only after the tag
has passed are the head and the body copied out, and the lock let go. A connection that
sends a header and then dawdles holds the lock until its 30 s deadline, then is closed; the
others wait for it, no longer. Proven under MicroPython (`tests/test_onclock_door.py`,
`test_bad_requests_leave_the_heap_where_it_was_under_micropython`): 400 unauthenticated
requests, three connections at a time, in a 256 KiB heap (full 32 KB bodies under the wrong
key, oversize announcements, garbage, bodies cut off half way) left the heap after a
collection at 86,784 bytes before and 86,848 after (one small object the first answers
create), with every request refused and the door still answering. Before this change each
request's body was read into a new 32 KB string and copied once more into the tag's input.
The cost: the program's heap is 34 KB larger from the start.

| Verb | Body | Does |
|---|---|---|
| `status` | | the app's `status()`, the running and active slot, both bundle ids, the config id, tries, each slot's last error (`errors`), halted, free flash |
| `config-put` | the config JSON | checks it with `config_from_json`, replaces `config.json` (temporary file and rename), calls `reload_config()` |
| `bundle-begin` | the manifest | checks it (paths, sizes, at most 256 files, 256 KiB a file, 2 MiB a slot, 2 MiB left free), deletes the inactive slot's manifest (the slot is unusable from here), answers which files the inactive slot lacks by hash |
| `bundle-file` | a chunk (`path`, `offset`) | appends to `<file>.part` in order; when whole and its hash matches, renames it into place |
| `bundle-commit` | optional: a config | checks every file against the manifest, deletes what the manifest does not name, writes the manifest, (the config,) makes the slot active with no tries, asks the program to restart |
| `bundle-abort` | | forgets the transfer |
| `logs` | (`after`, `max`) | lines from the memory log |
| `pause`, `resume` | | `app.pause()`, `app.resume()`: pushing and the Whisker connection stop, so the Mac's feeder can take the screen and the account |
| `restart` | | clears the active slot's count, then `request_restart()` |

Only the inactive slot is ever written (the one the program did not start from), and only
one transfer runs at a time: another `bundle-begin` is "busy" unless it is the same manifest
(which takes over at once, for a resend after a cut) or the old one has been idle for 120 s.
After a commit, no new bundle until the restart. A cut transfer leaves the active slot and
`state.json` untouched; the half-written slot has no manifest, so boot never starts it.

Paths in a manifest: only `[A-Za-z0-9_.-]` names, no empty, `.`, `..` or hidden part, not
absolute, at most 6 deep and 120 characters, not `manifest.json` or a `.part` name, and no
path both a file and a folder. Nothing on the way is followed if it is a link: folders are
checked by listing their parent (MicroPython has no `lstat`); a link where a folder belongs
is removed and replaced by a real folder; a file is written as `.part` and renamed over
whatever was there, which replaces a link rather than writing through it; a slot folder that
is itself a link is refused.

**Why a challenge, not a timestamp.** Each connection gets 128 random bits from the door and
every tag covers them, and each answer hands over the next challenge, so a recorded request
is useless on any other connection or later on the same one. It needs no clock: the clock's
time can be anything (its time was exact on the day, but after a cold boot without Wi-Fi it
may not be), and the door keeps no list of seen nonces in memory. The cost is one round
trip per connection, which the greeting already is. The random bits come from `getrandom()`
(the build's fix 1; see below).

### Threat list

Someone on the home Wi-Fi **without the key** can:
- see that port 7531 is open and read the greeting (a protocol name and a random challenge);
- tie up the door: hold the 3 connections (each read times out after 20 s, but they can
  reconnect), or send a header and dawdle so the one request buffer stays theirs for up to
  30 s at a time, so the Mac cannot get in for as long as they keep at it. The display
  itself keeps running, and the door's memory does not grow;
- send garbage: it gets the fixed refusal and a closed connection; nothing is parsed.

They cannot: run any verb, read the status, logs or anything else, change a slot or the
config, replay a request they recorded (the challenge differs), make a request for a later
connection, or learn anything about the key from the timing of the comparison. With the
traffic of one of Kyle's pushes recorded they learn the bundle's files (our code, which is
public) and the config (colours and captions); the protocol does not encrypt, and nothing
secret crosses it: the Whisker login and the key never do.

Someone **with the key** (it is on the Mac in `.clock-door-key` and on the clock in
`secrets.json`) can do everything the verbs allow: install any code they like into a slot
and restart into it. That code runs as root on the clock and can read `secrets.json`, so
**the key is as good as the Whisker login and root on the clock**. They still cannot write
outside the slots, `config.json` and `state.json` through the door itself, or run a command
through it. Anyone with the clock and a USB cable can read everything anyway (D-0030).

Not covered: a man in the middle who can change traffic can drop or delay requests (not
forge them); the challenge comes from the door, so a fake clock could get the Mac to sign a
request, but only for that fake clock's own challenge, and the Mac checks every answer's
tag, so it would notice at the first answer.

## The Mac's tool

`scripts/clock.py` (`arthur/clocktool.py`); `--help` lists everything. `build` runs the Docker
build; `bundle` lists what would go into a slot.

**Over USB** (`--serial SERIAL`, always required; `--dry-run` prints every `adb` command and
every file that would be written, with sizes, and runs nothing): `install`, `status`, `logs`,
`remove`. Rules carried from the spike: files are pushed only into `/tmp` and copied from
there (by `clockctl.py`, a helper the tool pushes to `/tmp/arthur-tool/` with a MicroPython to
run it); `install` refuses an autostart that is not ours, an existing install, or less than
2 MiB left free on `/data`; nothing outside `ROOT`, the autostart file and `/tmp/arthur*` is
touched. `remove` deletes the autostart (only when it carries our marker), restarts the
daemon's autostart, kills what is left of ours, deletes `ROOT` and `/tmp/arthur*`, and checks
each is gone.

**Secrets.** `install` reads `WHISKER_USERNAME` and `WHISKER_PASSWORD` from the environment
(`uv run --env-file .env`) and makes a new 32-byte door key. They are written to a mode-600
file in a private temporary folder on the Mac, pushed to `/tmp/arthur-secrets.json`, copied to
`ROOT/secrets.json` with mode 600 and deleted from `/tmp` in the same command; the Mac's copy
is deleted before the next step. The key is also kept in `.clock-door-key` (mode 600,
gitignored). None of the three is printed in any mode; a dry run writes no file at all.

**Over Wi-Fi** (`--clock HOST[:PORT]`): `push` builds the slot and the config, asks the door
for its status, and sends nothing when both match, only the config when only it differs, or
otherwise a bundle (only the files the inactive slot lacks), with the config in the same
commit when it changed too; then it waits for the program to come back running the new
bundle. `status`, `logs pull` and `logs follow` (into `logs/`, remembering where it got to in
`logs/clock-logs.json`), `pause`, `resume`, `restart`.

**How long, measured 2026-11-20** (Mac to the program, both on this Mac or the emulated
clock): a config change 0.26 to 0.28 s from the command to `reload_config()` done (the door
under QEMU); a code change of one file 0.92 s and of a whole slot (98 files, 280 KB) 1.10 s
from the command to the program answering from the new slot (stub program, emulated ARMv7).
On the real clock the restart will take longer: the real program imports the shared code and
loads the fonts, about 1.5 s under emulation and an estimated 1 to 2 s on the clock.

**With the real program** (`tests/test_onclock_e2e.py`, 2026-11-20; the Mac's MicroPython,
then the emulated ARMv7 clock installed by `install --emulate`): `push` of a config change
0.48 to 0.53 s in all, 0.36 to 0.43 s to the reload, no restart (the door's `run` id is the
same) and the new colours on the next frame; `push` of a code change 4.2 s on the Mac and
6.3 s emulated from the command to the program answering from the new slot (most of it the
Mac building and compiling the slot), and from the old program's last beat to the new one's
first frame 0.28 s on the Mac and 1.16 s emulated (the clock keeps showing the last frame
meanwhile); a bundle broken at import fell back after 3.9 s (Mac) and 5.6 s (emulated), one
that dies 3 s after starting after 13.2 s and 14.7 s, the display back by itself 0.2 to 0.8 s
later. On the clock `crash_pause` is 5 s, not the test's 1 s: about 15 s more for three
failed starts. `logs pull` 0.13 to 0.22 s.

## The build

`deploy/clock/micropython/`: `Dockerfile` (from the spike; static, musl, Cortex-A7, hard
float, ffi and btree off, stripped; exec on), `build.sh`, and `check.sh`, which `build`
runs on every result. **The two entropy fixes** make MicroPython's `mp_hal_get_random` and
mbedTLS 3.6.6's `entropy_poll.c` use `getrandom()`; without them both read `/dev/random`,
which on the clock blocks for minutes. `check.sh` replaces `/dev/random` and `/dev/urandom`
in the emulated container with named pipes that block on open, proves the trap with a
probe that reads `/dev/random` (it must hang), then makes three TLS contexts, calls
`os.urandom` and seeds `random`, which must finish. Checked both ways on 2026-11-20: the
build passes; a build without fix 1 fails at `os.urandom`, and one without fix 2 passes
`os.urandom` and hangs at `ssl.SSLContext`.

**Compiled `.mpy`, not source, in the slots.** Measured with `importbench.py` (imports the
shared code, the wire client and the door, loads the idle show and both fonts):

| | source | `.mpy` |
|---|---|---|
| slot size | 525,199 bytes | 288,852 bytes |
| imports, Mac (MicroPython 1.29, 1 MiB heap) | 48 to 81 ms | 14 to 32 ms |
| imports, ARMv7 under QEMU | 983 to 1,720 ms | 410 to 484 ms |
| heap after loading, ARMv7 (32-bit) | 323 KB | 331 KB |
| smallest heap that loads, ARMv7 | 640 KB | 512 KB |

Fonts take the same time either way (about 1 s under QEMU). The clock was 25 to 30 times
slower than the Mac in the spike, which puts the imports near 1.5 to 2 s from source and
0.5 s compiled. Compiling also skips the parser's largest blocks of memory (see
`micropython.md`, "Memory"). The cost: `mpy-cross` from the same release (Homebrew's
`micropython` installs it; the tool checks it emits mpy v6.3), and `.mpy` comments are gone
(a comment-only change gives the same bundle id, so `push` sends nothing). `--source` ships
source instead.

## The whole program, measured (2026-11-20)

`tests/test_onclock_e2e.py` with `ARTHUR_E2E_DAY=1` runs one program, never restarted,
through a compressed "day": the first password login, two real visits, a token refused and
renewed, a renewal refused and a password login again, the live connection silent, an
outage with a visit found in the history, a config push, a pause with the Mac on the screen
and a resume, AWTRIX restarted, a whole idle round (the weather page and a scene with its
plan built ahead: the bird on the Mac, the rat and the stretch emulated) and a log pull: about 6.5 minutes of real time, 3,000 to 4,000 beats.
`ARTHUR_E2E_HEAP` sets the launcher's heap; `ARTHUR_E2E_EMULATED=1` runs it on the static
ARMv7 build in the emulated container.

**Memory**, the heap in use just after the program's scheduled collections
(`heap_after_gc` in `status`):

| | after start-up | largest over the day | smallest heap tried that ran the day |
|---|---|---|---|
| Mac, MicroPython 1.29, 64-bit | 878 KB | 1,107 KB | 1.5 MB; at 1.25 MB drawing "just cleaned" failed for want of 6.6 KB in one piece |
| ARMv7 build (the clock's), 32-bit, emulated | 583 KB | 784 KB | 1.5 MB; at 1 MB the program kept drawing but a config push failed inside the door (MemoryError, answered as an internal error) |

The 32-bit build needs about two thirds of the 64-bit heap, as the drawing alone did. The
largest pieces at start: the fonts and art (loaded first), the door's request buffer
(34 KB), the log ring (up to 128 KB of text); the CA bundle is not in the heap (below).

**The launcher's heap stays 3M** (`HEAP=` in `autostart.sh`, `install --heap`). The margin:
twice the smallest heap that ran the day on the clock's build (1.5 MB), almost four times the
largest collected heap (784 KB), against what a six-minute day cannot show: fragmentation
over weeks (MicroPython cannot compact its heap), real Whisker answers, and screens not yet
written. It costs nothing measurable: the emulated collections took 7.5 ms at 3 MB and 8.1 ms
at 1.5 MB. The process needs more than its heap: mbedTLS keeps the CA bundle outside it
(loading a 333 KB bundle took 1.8 KB of the 32-bit heap), and presumably each connection's
buffers too (not measured), so about 4 MiB of the clock's 13 MiB available, leaving AWTRIX the 4 MiB it wants
twice over. (The spike's "435 KB of heap" for the bundle was not the MicroPython heap.)

**Time**, MicroPython on this Mac, the one-program day at 1.5 MB (milliseconds of work per
beat, from the start of the beat to the frame drawn; the push is apart):

| | beats | mean | worst |
|---|---|---|---|
| home | 1,641 | 3.2 | 26.6 |
| weather page | 331 | 4.2 | 7.7 |
| bird scene | 270 | 2.9 | 5.2 |
| scene wipe | 18 | 4.1 | 7.1 |
| in use, waiting, cleaning, just cleaned | 813 | 2.8 to 3.3 | 6.1 |
| the scene's plan, built ahead on a home beat | 1 | 13.2 | |
| a push (HTTP to the fake clock, mostly waiting) | 447 | 17.9 | 98.1 |
| a scheduled collection | 813 | 2.3 | 4.6 |

No beat's work took over 125 ms; one beat woke more than 20 ms after its tick (31 ms). Under
emulation the worst late wakes came right after the outage ended (155 and 97 ms: the TLS
handshakes and the history read of the reconnect) and at the no-weight fallback's history
read (61 ms).

**How long each long job holds the loop** (`tests/test_onclock_blocking.py`: a ticker task
records the longest gap while each job runs; MicroPython on this Mac):

| Job | longest hold | in all | the clock, est. x25 to x30 |
|---|---|---|---|
| password login (SRP, 4-bit slices) | 3.9 ms | 576 ms | slices of 100 to 120 ms, 14 to 17 s in all |
| renew the token (TLS + JSON) | 4.6 ms | 21 ms | 115 to 140 ms |
| open the live connection (TLS + upgrade) | 4.6 ms | 20 ms | 115 to 140 ms |
| one weather fetch (TLS) | 5.3 ms | 23 ms | 130 to 160 ms |
| read 100 history entries (4 pages) | 5.1 ms | 113 ms | 130 to 155 ms |
| load the robot | 2.8 ms | 18 ms | 70 to 85 ms |
| reload the config | 2.7 ms | 2.5 ms | 70 to 80 ms |
| door: a 24 KB log page | 3.9 ms | 5.9 ms | 100 to 120 ms |
| door: write and hash a 24 KB chunk | 0.8 ms | 1.5 ms | 20 to 25 ms |
| door: the tag of a full request | 0.8 ms | 0.3 ms | 20 to 25 ms |

**What that means on the clock (an estimate: the Mac's figures times 25 to 30).**
*Measured since, on 2026-11-21: the estimate was about four times too slow for drawing and
five times too fast for a scene's plan; the real figures are under "The first install on the
real clock" and replace this paragraph's numbers.* Every
long job above costs about one beat on the clock. A drawn beat costs 70 to 125 ms by the
same estimate (home 80 to 95, the bird scene 70 to 85, the page 105 to 125), and a
collection 60 to 70 ms on top when one falls due, before the push (the slowest push measured
on the clock was 64 ms, `awtrix-ng.md`). **So by this estimate one process will not hold 8
frames a second during a scene or a page while anything else runs**: a reconnect (two or
three TLS handshakes) drops a beat or two each time, a history read another, and a password
login (14 to 17 s of slices) runs the display at about half rate while it lasts. On the home
page, which changes about once a second, none of this shows. Against the estimate: the spike
measured the real clock drawing the home screen at 15.6 ms a frame and the bird scene at
10.8 ms with its plan kept, which is nearer 20 times the Mac's drawing alone; the rest of a
beat (the source's view, the mapping, the clock's datetime arithmetic) has not been timed on
the clock. **Measure it there before splitting anything** (`status` now carries the per-beat
figures and the late wakes). If the clock confirms the estimate, in this order: let the
Whisker work wait for a quiet moment as the weather already does (no reconnect, history
read or login slice during a page, a scene or their transitions; they are rare and can wait
a minute); keep the mapped state between record changes; and only then split the Whisker
client (connection, tracker, log) into a second MicroPython process that hands the record and
the observed facts to the display process over a local socket, each with its own heap.

## The first install on the real clock (run on 2026-11-21)

`S` is the clock's adb serial (`adb devices -l`; the cable goes into the clock itself, not
its charging base). `ADDRESS` is the clock's address on the Wi-Fi.

Checked on 2026-11-20, before any install: `scripts/clock.py build` gives the same file,
byte for byte, as the MicroPython that ran on the clock in the spike (521,364 bytes), and
`--serial S status` ran on the real clock from its `/tmp` and reported no autostart, no
root, no process of ours and 8.0 MB free on `/data` (the install writes about 0.88 MB).

1. **Stop the Mac's feeder first** (`pkill -f arthur.feeder`, then `pgrep -fl arthur.feeder`
   prints nothing). The program logs in to Whisker as soon as it starts, and two programs
   pushing to the clock fight over the screen. The screen shows the stale border from 90 s
   after the last push until the program draws.
2. `uv run --env-file .env python scripts/clock.py --serial S --dry-run install` and read
   what it would write.
3. **Kyle runs** `uv run --env-file .env python scripts/clock.py --serial S install`. It
   writes the files, starts the autostart and returns.
4. Expect NO DATA first: the first start has no tokens, so it makes a password login, an
   estimated 14 to 20 s of arithmetic on the clock, and then reads the robot. This is also
   the first password login our client has made against the real service.
5. After about two minutes: `scripts/clock.py --serial S logs`. The boot notes should say
   `starting slot a, try 1` and, 120 s after the start, `slot a is healthy`; the console
   `Whisker connected` and `first frame pushed to AWTRIX`.
6. Over Wi-Fi: `scripts/clock.py --clock ADDRESS status`. Read `beats` (mean and worst
   milliseconds for each kind of screen), `woke_late`, `worst_late`, `gc` and
   `heap_after_gc_max`; read them again after a whole idle round with a scene (three
   minutes). These are the first measurements of the whole program's speed on the clock.
7. `scripts/clock.py --clock ADDRESS logs follow` in a Terminal window for as long as the
   program is to be kept: the clock keeps its log in memory only.

**The way back.** With the cable: `scripts/clock.py --serial S remove`, then
`open -a Terminal scripts/start_feeder.command`. Without it: `--clock ADDRESS pause` stops
the program pushing and closes its Whisker connection, and the Mac's feeder can be started;
but a pause does not outlast a restart of the program or the clock, so it is for an hour, not
for a night.

**What happened on 2026-11-21.** The Mac's feeder was stopped at 02:06:57. A password login
from the Mac with our client (`scripts/wire_check.py --login`) took 1.42 s and the robot read
READY: the first real password login the client made. The dry run printed no secret.
`install` ran from 09:04:53 to 09:05:00 and reported "installed and running". Then, from the
program's console and `status`:

| When (local) | What |
|---|---|
| 09:04:57.1 | the program starts (slot a, try 1); the door opens on port 7531 |
| 09:04:59.9 | "loaded in 2739 ms, heap in use 467648"; NO DATA |
| 09:05:02.3 | first frame pushed to AWTRIX (that first push took 2,440 ms) |
| 09:05:15.8 | "Whisker connected" (its own password login and the robot read), NO DATA to ready |
| 09:05:16.8 | live updates subscribed; the history seeded the last visit (08:16) at 09:05:19 |
| 09:06:57 | boot: "slot a is healthy" |

**Measured there, 398 s after the start** (about 3,200 beats, two idle rounds with the
weather page twice, the rat scene and the stretch scene; `status`, milliseconds):

| What | Count | Mean | Worst |
|---|---|---|---|
| a beat's mapping and drawing: home | 1,767 | 21.6 | 25.4 |
| the same: NO DATA | 104 | 17.4 | 19.0 |
| the same: the weather page | 858 | 25.7 | 45.7 |
| the same: a wipe into or out of a scene | 30 | 29.1 | 38.0 |
| the same: the rat scene | 269 | 16.5 | 22.2 |
| the same: the stretch scene | 76 | 17.2 | 18.7 |
| a scene's plan, built 0.875 s ahead: rat | 1 | 798.6 | |
| the same: stretch | 1 | 408.9 | |
| a push to AWTRIX (308 after the first) | 308 | about 38 | |
| a scheduled collection | 213 | 23.3 | 26.2 |

14 beats took longer than 125 ms; the eight latest wakes were 228, 225, 212, 192, 175, 174,
69 and 56 ms late. No push failed. The heap in use after a collection was 0.61 to 0.65 MB of
3 MB. The clock had 10.3 MiB of memory available (14.3 before the install) and the process
4,216 kB of address space; `/data` had 7.44 MB free.

**What the figures say.** Drawing on the clock costs about six times the Mac's MicroPython,
not 25 to 30: a beat with a push and a collection is about 85 ms of its 125. A scene's plan
is the opposite: 140 times the Mac's 5.8 ms for the rat. The plans draw their random numbers
from `arthur/mtrandom.py`, whose 32-bit arithmetic fits MicroPython's small integers on the
64-bit Mac and not on the 32-bit clock, where each value is a heap-allocated big integer;
that was the guess that morning, confirmed and measured in the next section. The plan fits
its 0.875 s lead by 77 ms for the rat in this first reading (later rat plans took up to
920 ms).
The bird's plan has not been timed there in the whole program (87 ms in the spike's slice).

Also seen: the clock's screen read back from the Mac showed the weather page the program
drew (the sun, 45°, `NOV` over `21`), no stale mark, pixels changing; `logs pull` brought 3
robot lines, 64 history lines and 8 console lines into `logs/`; `push` answered "nothing to
do (code bd2ef8a4853d02cc, config 470834297cb28a26 already there)". On the flash,
`secrets.json`, `tokens.json` and `state.json` are mode 600 and the autostart 700; `ROOT`
and `slots/` are mode 777, because adb's shell has umask 000 when `clockctl.py place` makes
them. Only root exists on the clock, so nothing can use that today; make them 700.

## The first two days alone (2026-11-21 and 22)

From the program's console and robot log as the Mac pulled them, and its `status`. The Mac
was asleep from about 12:15 on the 21st to 13:57 on the 22nd, so nothing was watching then.

**Visits.** Three were logged, each drawn through in use, waiting, cleaning, just cleaned
and back to ready:

| Visit | What was new | The countdown against the real clean start |
|---|---|---|
| 21st, 09:23 | the weight came over the live connection 109 s after detection; **the first interruption logged live**, 25 s, 40 s into the clean (`docs/states.md`) | reached zero 1 s before it |
| 22nd, 07:42 | | 5 s after it |
| 22nd, 13:19 | a second weight reading at 13:25 (he moved or came back) started the countdown again from 8:00, as it should | 12 s before it |

(The visits between 12:05 on the 21st and the cold boot are not in the Mac's log; the
robot's own history, read at each start, has them.)

**A cold boot, by itself, at 00:26 on the 22nd.** The clock lost power some time after
12:15 on the 21st: it had been on the Mac's USB port, the Mac went to sleep, and by the
afternoon of the 22nd the cable was out (when the power went and came back is in no log).
At 00:26:04 the program started from the autostart. The system time was still 1970 and was
set 23 s later, at 00:26:27; by 00:26:33 the program had read the robot and its history
(last visit 19:57 the evening before). Six seconds is the saved token's doing: a password
login takes about 14 s there. The first weather fetch failed and the retry a minute later
worked (00:27:19). It then ran 14 h 23 min, over 35,000 pushes, none failed.

**Whisker closed the live connection five times that night** (01:20, 01:29, 03:47, 04:29,
08:06, "closed by the server"); each time the program was subscribed again within 2 s.
Contact never lapsed across the hourly token expiry on either day, so the renewals work
(they are not logged). Open-Meteo answered 503 once (07:30:05); the reading was kept and
the retry at 07:31:41 worked.

**Memory.** The heap in use after a collection was 0.73 MB after 14 h, 0.84 MB at most
(0.61 to 0.65 MB in the first minutes; the program's own log ring, 128 KB when full, is part
of the difference).

**What was lost.** The clock's log from 12:05 on the 21st to the cold boot (it is kept in
memory), and the first 30 lines after the boot: by the time the Mac woke, 13 and a half
hours of lines had pushed the start-up lines out of the 128 KB ring (a robot line is about
1.5 KB).

**The first real change over Wi-Fi, 14:49 on the 22nd** (`push`, the code only; the clock on
its charger, no cable):

| When | What |
|---|---|
| 14:49:14 | `push` starts: the Mac builds the slot; the door takes 111 files, 327 KB, into slot b in 8.8 s (every file: slot b was empty) |
| about 14:49:23 | the old program stops; its last frame stays on the screen |
| 14:49:24.4 | the new program starts from slot b and opens the door |
| 14:49:26.5 | the Mac's `push` returns: "the program is back", 12.5 s after it began |
| 14:49:27.1 | "loaded in 2684 ms"; NO DATA |
| 14:49:29.6 | first frame pushed (2.5 s: the weather's first handshake ran in that push) |
| 14:49:30.4 | "Whisker connected" from the saved token; the home page |

So a code change costs the display about 7 s on the last frame and half a second of NO
DATA. The follower on the Mac lost the door for 10 s and carried on with the new run.

## What holds the loop on the clock (measured 2026-11-21)

After 2 h 34 min (74,000 beats, 10,927 pushes, none failed) the program's `status` counted
558 beats longer than 125 ms and 92 late wakes. Two things cause nearly all of them. Both
were then timed on the clock itself, with small read-only scripts run by the installed
MicroPython over USB beside the program (the clock has two cores, so the display was not
disturbed; the scripts lived in `/tmp` and were removed).

**1. The weather fetch: 2.2 s, every 15 minutes.** The eight latest wakes (1,987 to
2,169 ms) were 15 minutes apart: 09:20:05, 09:35:07, 09:50:10, 10:35:17, 10:50:20, 11:06:02,
11:21:04 and 11:36:07. Opening a verified TLS connection, by host:

| Host | DNS | TCP connect | TLS handshake | Suite |
|---|---|---|---|---|
| `api.open-meteo.com` | 44 to 108 ms | 24 to 28 ms | **2,238 and 2,249 ms** | ECDHE-ECDSA, AES-256-GCM-SHA384 |
| `lr4.iothings.site` (Whisker) | 51 to 111 ms | 13 to 22 ms | 273 and 283 ms | ECDHE-RSA, AES-128-GCM-SHA256 |
| `cognito-idp.us-east-1.amazonaws.com` | 42 ms | 25 ms | 319 ms | ECDHE-RSA, AES-128-GCM-SHA256 |

Loading the CA bundle into a new context took 255 ms (once per program). The handshake is
arithmetic inside mbedTLS and nothing else runs meanwhile; Open-Meteo's certificate chain is
elliptic-curve, which this build of mbedTLS checks slowly, and Whisker's is RSA. The DNS
lookup also holds the loop (MicroPython's `open_connection` calls `getaddrinfo` directly),
for a tenth of a second at most here. The program already kept the fetch out of pages and
scenes; it did not keep it out of a visit.

**2. A scene's plan: 0.4 to 0.9 s, twenty times an hour.** In the program, over 46 plans:
rat mean 691 ms and worst 920, stretch 413 and 432, bird 376 and 400, against the 0.875 s
lead. Alone on the clock, with an empty heap:

| | rat | stretch | bird |
|---|---|---|---|
| the plan, four rounds | 245 to 560 ms | 193 to 203 ms | 135 to 177 ms |
| generators it seeds | 3 to 6 | 4 | 2 |
| of which seeding | 126 to 255 ms | 168 to 173 ms | 84 to 86 ms |

So the cause guessed above is confirmed. The clock's MicroPython is 32-bit, a small integer
there holds 31 bits, and `arthur/mtrandom.py` kept its 624 words as 32-bit integers: every
word of 2^30 or more was a heap-allocated big integer and every operation on one allocated
another. One `Random(seed)` took 43 to 47 ms and left about half a megabyte of garbage
(0.30 ms on the Mac's 64-bit MicroPython); one word drawn 47 µs, `random()` 105 µs,
`randrange(17)` 117 µs. In the program the garbage of four seeds does not fit the 3 MB heap
beside 0.65 MB of live data, so collections land inside the build: that is why a plan costs
1.5 to 2.5 times more there than alone. With the state kept as 16-bit halves, so that
nothing reaches 2^30, a first sketch of the same seeding gave the identical state and took
13.5 ms on the clock. `arthur/mtrandom.py` was rewritten that way the same day (on the Mac's
64-bit MicroPython it costs a little more than before, 0.32 ms a seed against 0.25 to 0.28,
because the arithmetic is done once per half; one code path for both was preferred to a
faster one the Mac's tests would not run). What is left of the rat's plan is its own work:
placing the rat 103 times (74 ms) and mirroring 89 sprites (69 ms) in round 0.

**Not used: MicroPython's native code.** The clock's build has the native and viper
emitters (`@micropython.native`, `@micropython.viper` compile and run there); the Mac's
arm64 build has neither ("invalid micropython decorator"). Code compiled that way would run
only on the clock and under emulation, outside the 40,000-frame comparison the Mac runs, so
it was left alone.

**The log follower costs nothing that shows.** With `logs follow` asking every 2 s, the
drawing times were the same to a tenth of a millisecond.

**After the change** (commit `b10a520`, pushed over Wi-Fi at 14:49 on 2026-11-22: the
generator's state in 16-bit halves, no weather fetch during a visit, and `status` saying
what was on screen at each late wake). A scene's plan in the running program, ms:

| | before (the 14 hours to 14:49): mean, worst | after (the first five plans) |
|---|---|---|
| rat | 735, 1,035 (47 plans) | 456 and 467 |
| stretch | 411, 431 (48 plans) | 151 and 152 |
| bird | 374, 399 (47 plans) | 158 |

Every plan now ends at least 0.4 s before its scene; the rat's had overrun its 0.875 s lead
at its worst. What is left of the rat's plan is its own work, above.

`late_by_part` over those first 14 minutes: eight late wakes. Five in the first three
seconds (the weather's first handshake falls in the program's first push, which is why that
push takes 2.5 s). Then **the Whisker poll, every five minutes**: 162 ms on the home page at
14:54:31, and 198 ms and 35 ms in the middle of the rat scene at 14:59:32. Its TLS handshake
(0.3 s) is not held back from pages and scenes as the weather's is; about one poll in five
falls in a scene. That is the next thing that shows. The first scheduled weather fetch on
the new code, at 15:04:41, fell on the home page (1,990 ms). A weather fetch held back by
a visit was first seen on 2026-11-23: the one due at 10:36 fell in the 10:27 visit and ran
at 10:38:24, as the screen went back to the home page (read from the 2 s late wake it
leaves; fetches are not logged).

**The 19 hours that code then ran** (to 10:05 on 2026-11-23; `status` read at 18.9 hours:
49,446 pushes, none failed, one visit drawn through at 22:19). Plans over 66 plays of each
scene, mean and worst in ms: bird 182 and 208, stretch 151 and 157, rat 427 and 680, so all
inside the 0.875 s lead. Late wakes by what was on screen, count and worst: home 371
(2,136 ms, the weather fetch), the weather page 122 (483 ms), the three scenes 25 between
them (227 ms), a wipe 2 (156 ms), waiting 3 (85 ms), cleaning 1 (38 ms). How many of the
page's and the scenes' are the poll is not broken down: about 68 polls fell on the page and
45 in scenes in that time, and one poll can make two late wakes.
Heap after a collection: 0.76 MB (0.78 at most).

**The second change over Wi-Fi** (commit `8ed2a48`, the cleaning countdown after an
interruption, D-0032), at 10:05:53 on 2026-11-23: the 8 files slot a lacked of 111
(31,878 bytes) in 2.1 s, and `push` reported the program back 5.7 s after it began. On the
screen, from the clock's log: the new program started at 10:05:56, the first frame (NO
DATA) was pushed at 10:06:01, and the home page followed at 10:06:02 when Whisker
answered from the saved token. So about 5 s on the last frame and under a second of NO
DATA. The log follower lost the door once during the restart and carried on. The boot
script marked slot a healthy after its two minutes (tries a: 0).

**The routine poll held back from pages and scenes** (D-0033, commit `321b8d5`, pushed at
11:03:26 on 2026-11-23: 8 files, 33,221 bytes in 2.0 s into slot b; first frame at
11:03:33, the home page at 11:03:36, so NO DATA showed for 3 s, the weather's first
handshake falling in it; healthy after two minutes). The clock's program answers the
monitor's `poll_ok`: no while it is idle with a page or scene on or beginning within 5 s.
A due poll then waits for the home page, 90 s at most; it is never held on any other
screen, with the live subscription down, or as a retry.

| | late wakes on the weather page | in scenes | polls held |
|---|---|---|---|
| the 19 hours to 10:05 | 122 (6.5 an hour) | 25 (1.3 an hour) | none could be |
| the 58 minutes from 10:06 | 4 | 0 | none could be |
| the first 22 minutes with the hold | 0 | 0 | 3 of the 4 due, 59.7 s at longest |

`poll_held` read `[3, 59.7]`. Late wakes of about 0.2 s, which is what a poll costs, fell
on the home page at 11:14:02 (185 ms), 11:19:39 (176 ms) and 11:25:40 (200 ms); the first
poll, due at 11:08:36, left none among the eight worst. A poll that changes nothing writes
no log line, so its time shows only this way. `live_up` read true at both readings.
Twenty-two minutes is a small sample: whether the page's and the scenes' late wakes stay at none, and
so how many of the old ones were the poll, wants a day of `late_by_part`.

## Assumed about the real clock, not checked

- The daemon stops the running autostart (and so our launcher, which stops the program) on
  `ctl restart-autostart`; `remove` kills what is left either way. (That it starts the
  launcher is seen; a trap on a signal was not tried, only on exit.)
- `os.rename` over an existing file is atomic on JFFS2, so `state.json`, `config.json` and the
  manifest are always whole.
- The heap the program needs on the clock is what the emulated ARMv7 build needed: 1.5 MB ran
  the measured day, 1 MB did not, and the launcher gives it 3M (see "The whole program,
  measured"). Weeks of running are not measured. `--heap` changes it at install.
- What boot's manifest check (hashing about 290 KB at each start) costs on the clock:
  estimated at tens of milliseconds. (From the boot note to the program's first line was
  about a second, the check and the imports together.)
- `adb push` keeps a mode-600 file's mode in `/tmp`, and `umask` in the clock's `sh` applies
  to `cp`; `secrets.json` is `chmod 600` after the copy either way.
- The daemon starts the launcher with a clean environment (no `ARTHUR_ONCLOCK_TEST`).
- The system time is set from the network soon after a boot with Wi-Fi; until it is past
  2026-01-01 the program shows NO DATA and does not try Whisker. With no network time at all
  it would wait for ever, saying so once in the console.
- MicroPython's `Stream.readinto` exists in the clock's build (it does in the Mac's 1.29 and
  in the same static ARMv7 file under emulation; `os.getenv` was seen on the clock).
- What a TLS handshake with the real services costs on the clock (the real chains are
  longer than the fake's; the first login, the read and the live connection together took
  16 s, not broken down).

**Checked on the real clock on 2026-11-20** (over USB, read-only, the built MicroPython run
from `/tmp` and removed afterwards; AWTRIX and the Mac's feeder kept running):
- `scripts/clock.py --serial S status` works there: the helper and the binary are pushed to
  `/tmp/arthur-tool`, run and removed. Nothing of ours was installed; 8,007,680 bytes were
  free on `/data` and 14.3 MiB of memory available.
- `os.ilistdir` reports file types on both file systems: regular files (32768) in
  `/data/awtrix-ng/state` (JFFS2), directories (16384) and files in `/tmp` (tmpfs). So the
  door's refusal of an unknown type will not stop a transfer.
- `os.getenv` exists, and the system time was right (not checked soon after a boot).
- The clock's `sh` has `umask` (it reports 000 over adb; the launcher sets 077), `wait $!`
  and `trap` (tried on exit).

**Seen at the first install, 2026-11-21:** the daemon starts our launcher from the autostart
file; the Mac reaches the door on port 7531 over the home Wi-Fi; `secrets.json` is mode 600
on the flash; the program loads in 2.7 s; real Cognito and Whisker answer within the client's
timeouts from the clock; the heap and the speed are as measured above, not as estimated.
