#!/bin/sh
# Check a built clock binary under emulation (Docker, linux/arm/v7), without the real clock.
#
#   sh deploy/clock/micropython/check.sh [BINARY]     (default: dist/micropython-armv7)
#
# The test that matters: the binary must never read /dev/random. On the clock that read
# blocks for minutes (almost no entropy, Linux 4.9), and Docker never shows it because its
# entropy pool is full. So the check makes /dev/random and /dev/urandom named pipes with no
# writer: opening one for reading blocks forever, exactly the clock's failure, and the probe
# is killed after a timeout. The probe creates TLS contexts (mbedTLS seeds its generator
# there), asks for os.urandom and seeds `random`; it must finish. A control run that opens
# /dev/random itself must hang, which proves the trap works.
#
# Also checked: the modules the on-clock program needs, and exec (arthur.compat.record).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${1:-$HERE/dist/micropython-armv7}
[ -f "$BIN" ] || { echo "check: no binary at $BIN (build it first)"; exit 2; }
DIR=$(cd "$(dirname "$BIN")" && pwd)
NAME=$(basename "$BIN")

# --init: without it the probe can end up as PID 1, which ignores the timeout's signal.
run() {
    docker run --rm --init --platform linux/arm/v7 -v "$DIR:/w:ro" alpine:3.21 sh -c "$1"
}

TRAP='rm -f /dev/random /dev/urandom && mkfifo /dev/random /dev/urandom'

echo "== file"
file "$BIN" 2>/dev/null || true
ls -l "$BIN"

echo "== modules and exec"
run "/w/$NAME -c \"
import sys, os, ssl, tls, asyncio, socket, select, json, binascii, hashlib, struct, time, errno
exec('x = 6 * 7')
assert x == 42
print(sys.implementation)
print('modules ok, exec ok')
\""

echo "== control: a probe that reads /dev/random must hang in the trap"
if run "$TRAP && timeout -s KILL 15 /w/$NAME -c \"open('/dev/random', 'rb').read(1); print('read')\""; then
    echo "check FAILED: the trap did not block a read of /dev/random"
    exit 1
fi
echo "control: hung and was killed, as on the clock (good)"

echo "== randomness without /dev/random or /dev/urandom"
if run "$TRAP && timeout -s KILL 120 /w/$NAME -c \"
import os, ssl, random
print('urandom', len(os.urandom(32)))
for i in range(3):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
print('ssl contexts ok')
random.seed()
print('random ok', random.getrandbits(8) >= 0)
print('NO-DEV-RANDOM-OK')
\""; then
    echo "check passed: TLS contexts and os.urandom never opened /dev/random or /dev/urandom"
else
    echo "check FAILED: the binary blocked on /dev/random or /dev/urandom (or crashed);"
    echo "on the clock that hangs for minutes. Are both fixes in the Dockerfile applied?"
    exit 1
fi
