# MicroPython v1.29.0, unix port, as one static ARMv7 hard-float (musl) executable for the
# Ulanzi TC002's Linux (Cortex-A7, NEON/VFPv4, Linux 4.9). D-0029.
#
# Built natively inside an emulated linux/arm/v7 Alpine container (Docker Desktop runs it
# under QEMU on Apple Silicon), with Alpine's own musl gcc. No distribution packages a musl
# ARMv7 cross gcc, and the prebuilt ones are third-party. Carried over from the spike
# (spike/mpy/build-armv7/ on the branch spike/micropython-slice), whose binary ran on the
# real clock on 2026-11-20.
#
# Run it with build.sh (or `uv run python scripts/clock.py build`), which also runs check.sh
# on the result. The binary is not committed: it lands in dist/, which git ignores.

FROM --platform=linux/arm/v7 alpine:3.21 AS build

RUN apk add --no-cache build-base git python3 linux-headers

ARG MPY_TAG=v1.29.0
RUN git clone --depth 1 --branch ${MPY_TAG} https://github.com/micropython/micropython /src/micropython

WORKDIR /src/micropython

# The unix port's documented steps (ports/unix/README.md): mpy-cross, submodules, make.
# ffi and btree off (ffi needs libffi; neither is needed). Everything else is the standard
# variant: ssl/tls (mbedTLS), socket, asyncio, select, json, binascii, hashlib, struct, time,
# os, and exec/eval (MICROPY_PY_BUILTINS_EVAL_EXEC, which arthur.compat.record needs; on by
# default in the unix port and checked below). -static for one self-contained binary.
ARG MAKEOPTS="MICROPY_PY_FFI=0 MICROPY_PY_BTREE=0 LDFLAGS_EXTRA=-static"
ARG CPUFLAGS="-mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard"

# FIX 1 of 2 for the clock's missing entropy. MicroPython 1.29's mp_hal_get_random
# (ports/unix/unix_mphal.c) uses getrandom() only with glibc; with musl it opens /dev/random.
# On the clock (Linux 4.9, entropy pool at 10 to 57 bits) that read BLOCKS for minutes: seen
# on the real clock on 2026-11-20, never under Docker, which has plenty of entropy. musl has
# getrandom() in <sys/random.h>; _HAVE_GETRANDOM selects that branch.
ARG RNGFLAGS="-include sys/random.h -D_HAVE_GETRANDOM"
RUN make -C mpy-cross -j"$(nproc)"
RUN make -C ports/unix submodules ${MAKEOPTS}

# FIX 2 of 2: the same trap inside mbedTLS 3.6.6. library/entropy_poll.c uses the getrandom()
# system call only with glibc and otherwise reads "/dev/random"; with only fix 1, creating an
# SSL context still hung on the clock. Let it use the system call on any Linux. The grep
# fails the build if the line was not found (a new mbedTLS would need a fresh look).
RUN sed -i 's/#if ((defined(__linux__) \&\& defined(__GLIBC__)) || defined(__midipix__))/#if (defined(__linux__) || defined(__midipix__))/' \
        lib/mbedtls/library/entropy_poll.c \
    && grep -n '^#if (defined(__linux__) || defined(__midipix__))$' lib/mbedtls/library/entropy_poll.c

RUN make -C ports/unix -j"$(nproc)" ${MAKEOPTS} CFLAGS_EXTRA="${CPUFLAGS} ${RNGFLAGS}" \
    && strip ports/unix/build-standard/micropython \
    && ls -l ports/unix/build-standard/micropython \
    && ports/unix/build-standard/micropython -c "import sys; print(sys.implementation)" \
    && ports/unix/build-standard/micropython -c "exec('x = 6 * 7'); assert x == 42; print('exec ok')"

FROM scratch AS out
COPY --from=build /src/micropython/ports/unix/build-standard/micropython /micropython-armv7
