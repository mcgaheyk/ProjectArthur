# The program on the clock: layout, start-up, the update door

How the on-clock program (D-0029) is installed, started, updated and removed. Written
2026-11-20 with the code: `deploy/clock/` (the build, `boot.py`, `autostart.sh`,
`clockctl.py`), `arthur/onclock/door.py`, `arthur/tools/onclock_files.py`,
`arthur/tools/doorclient.py`, `arthur/clocktool.py` (`scripts/clock.py`). **None of it has
run on the real clock yet**: it was proven against temporary folders, local sockets, the
Mac's MicroPython and an emulated ARMv7 "clock" in Docker. The clock's own facts are in
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
| `ROOT/tokens.json` | Whisker's tokens | the program (the wire source) |
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

**The launcher** (`autostart.sh`, shell builtins plus `mkdir` and `cp`): makes `/tmp/arthur`,
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
   `asyncio.run(main(ROOT, source_factory))`.
5. **Health:** after 120 s, and then every 15 s until it works, it connects to the door on
   127.0.0.1 and reads its greeting. The first time it does, the slot's count goes back to 0
   and its error is cleared. Why the door and not a call the program makes: it needs nothing
   in the contract, it checks the event loop is turning, and it checks the one thing a
   fall-back is for: a program whose door answers can be fixed from the Mac, so it is not
   "failed" even if something else is wrong; a program whose door never opens can only be
   fixed by the cable, so it is left counted. A program is never killed for being unhealthy.
6. `main` returns 0 (stop: exit 0) or 42 (exit 42: the launcher starts it again). An import
   error or an exception from `main` is written to `state.json` (`errors.<slot>`, 200
   characters at most), and after 5 s boot exits 42, so the launcher (not the daemon) starts
   it again and the count decides. A crash that kills the process (a signal) reaches the
   daemon; the count still works across those starts.
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

**The integration point:** `boot.source_factory(*args, **kwargs)` imports
`arthur.sources.wire_source` from the slot and calls its `make_source(...)`. The names are a
placeholder until that module lands (marked in boot.py).

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
    log: ClockLog                       # .append(line) -> seq ; .read(after_seq, max_bytes)
arthur/onclock/main.py:  async def main(root, source_factory) -> int
```

The program opens the door with `door = await arthur.onclock.door.start(app, root)` (port
7531 on every interface; raises `DoorError` without a valid key in `secrets.json`, and the
program should carry on without it) and closes it with `door.close()`. The tests' stub is
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
  bundle, came from the Mac's export with the same code); the program should still survive a
  bad one, since both slots read the same `config.json`.

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
bad tag). Every read times out after 20 s; at most 3 connections at once (more are closed
with no greeting) and 2,000 requests each. The Mac checks the answer's tag.

| Verb | Body | Does |
|---|---|---|
| `status` | | the app's `status()`, the running and active slot, both bundle ids, the config id, tries, halted, free flash |
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
  reconnect), so the Mac cannot get in for as long as they keep at it. The display itself
  keeps running;
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

## Assumed about the real clock, not checked

- The daemon stops the running autostart (and so our launcher, which stops the program) on
  `ctl restart-autostart`; `remove` kills what is left either way.
- The daemon starts the autostart with `/bin/sh` from its `#!` line, as the spike's four-line
  script was; busybox `sh` there has `trap`, `wait` and `$!` (Alpine's busybox does).
- `os.ilistdir` on JFFS2 and tmpfs reports file types (a type of 0, "unknown", is refused by
  the door, which would make every transfer fail).
- `os.rename` over an existing file is atomic on JFFS2, so `state.json`, `config.json` and the
  manifest are always whole.
- A 3 MiB heap is enough for the real program with TLS and the large font; the spike's slice
  ran in 1 MiB. `--heap` changes it at install.
- The clock's Wi-Fi lets the Mac reach port 7531 (no client isolation on the home network).
- How long the real program takes to start, and how often boot's manifest check (hashing
  about 290 KB at each start) costs on the clock: estimated at tens of milliseconds.
- `adb push` keeps a mode-600 file's mode in `/tmp`, and `umask` in the clock's `sh` applies
  to `cp`; `secrets.json` is `chmod 600` after the copy either way.
