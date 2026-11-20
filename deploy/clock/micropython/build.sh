#!/bin/sh
# Build MicroPython v1.29.0 for the clock (a static ARMv7 hard-float executable), then check it.
#
#   sh deploy/clock/micropython/build.sh       (needs Docker; or: scripts/clock.py build)
#
# Leaves deploy/clock/micropython/dist/micropython-armv7 (git ignores dist/). The build runs
# under QEMU emulation, so the first one takes a while (tens of minutes); Docker caches it.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
docker buildx build --platform linux/arm/v7 --progress plain \
    --target out --output "type=local,dest=$HERE/dist.new" "$HERE"
mkdir -p "$HERE/dist"
cp "$HERE/dist.new/micropython-armv7" "$HERE/dist/micropython-armv7"
rm -rf "$HERE/dist.new"
chmod 755 "$HERE/dist/micropython-armv7"
sh "$HERE/check.sh" "$HERE/dist/micropython-armv7"
