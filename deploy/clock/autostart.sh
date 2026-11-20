#!/bin/sh
# ARTHUR-AUTOSTART v1: ProjectArthur's program on the clock (D-0029), from scripts/clock.py.
# Remove with: uv run python scripts/clock.py --serial SERIAL remove
#
# AWTRIX NG runs this file (/data/awtrix-ng/state/autostart, root, mode 700) at start. It
# runs MicroPython on the boot script with a capped heap, and starts it again at once when it
# exits 42 ("restart me": a new bundle, or a restart from the Mac), so those never count
# against the daemon's limit of 5 failures in a row. Any other exit code is passed to the
# daemon: 0 ends it until the next start, anything else gets the daemon's back-off.
# Shell builtins only, plus mkdir and cp: the clock has no sleep, grep, head or tr.
# The three settings below are filled in by `scripts/clock.py install`.
ROOT=/data/awtrix-ng/state/arthur
TMP=/tmp/arthur
# 3M: twice the smallest heap that ran the measured day on the clock's build (1.5 MB; 1 MB
# did not), see docs/reference/onclock.md, "The whole program, measured".
HEAP=3M

umask 077  # what the program writes (tokens.json, state.json, the slots) is root's alone
mkdir -p "$TMP"
if [ -f "$TMP/console.log" ]; then cp "$TMP/console.log" "$TMP/console.prev.log"; fi
: > "$TMP/console.log"
child=
trap 'if [ -n "$child" ]; then kill -TERM "$child" 2>/dev/null; fi; exit 143' TERM INT HUP
while :; do
    "$ROOT/micropython" -X "heapsize=$HEAP" "$ROOT/boot.py" "$ROOT" "$TMP" >>"$TMP/console.log" 2>&1 &
    child=$!
    wait "$child"
    code=$?
    child=
    if [ "$code" -ne 42 ]; then exit "$code"; fi
done
