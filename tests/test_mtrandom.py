"""arthur.mtrandom gives CPython's random.Random numbers, bit for bit (D-0029).

MicroPython has no ``random.Random``; the shared code gets :class:`arthur.mtrandom.Random`
there instead. Every screen that draws from a seeded generator (the scenes, the dissolve, the
in-use jokes) is only the same on the clock if the numbers are. Here the pure-Python generator
runs on CPython next to CPython's own; the on-clock guard (tests/test_micropython.py) runs
the same exercise under MicroPython.
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from arthur.mtrandom import Random, _bit_length

_SPEC = importlib.util.spec_from_file_location(
    "rngcheck", Path(__file__).resolve().parent.parent / "mpy" / "rngcheck.py")
assert _SPEC is not None and _SPEC.loader is not None
rngcheck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rngcheck)

SEEDS = list(rngcheck.SEEDS) + rngcheck.more_seeds(400)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_method_matches_cpython(seed):
    assert rngcheck.exercise(Random, seed) == rngcheck.exercise(random.Random, seed)


def test_long_runs_match_cpython():
    """Ten blocks of the twister, word for word, for a few seeds."""
    for seed in (0, 5216, 2**32 - 1, -3, 2**80 + 1):
        a, b = Random(seed), random.Random(seed)
        assert [a.getrandbits(32) for _ in range(6240)] == [
            b.getrandbits(32) for _ in range(6240)]


def test_the_scenes_seeds_match_cpython():
    """The seeds the show itself uses: the scenes' per-key seeds for many rounds."""
    import zlib

    for n in range(300):
        for name in ("bird", "rat", "stretch"):
            for key in ("bird", "arthur", "rat", "shape", "quiet", "plan", "hold"):
                seed = zlib.crc32(f"{name}|{n}|{key}".encode())
                a, b = Random(seed), random.Random(seed)
                assert [a.randint(0, 99) for _ in range(5)] + [a.random()] == [
                    b.randint(0, 99) for _ in range(5)] + [b.random()]


def test_as_text_is_exact():
    assert rngcheck.as_text([0.1, True, 3, [2.5]]) == (
        "[f3fb999999999999a,T,3,[f4004000000000000]]")


@pytest.mark.parametrize("n", [0, 1, 2, 3, 255, 256, 65535, 65536, 2**31, 2**64 + 1])
def test_bit_length(n):
    assert _bit_length(n) == n.bit_length()


def test_only_int_seeds():
    with pytest.raises(TypeError):
        Random(None)
    with pytest.raises(TypeError):
        Random(1.5)


# ---- the state in 16-bit halves: its edges -------------------------------------------------

EDGE_SEEDS = (0, 1, 2**30 - 1, 2**30, 2**31, 2**32 - 1, 2**32, 2**64 + 5, 2**100 + 3,
              -1, -(2**30), -(2**32), -(2**64 + 5))


@pytest.mark.parametrize("seed", EDGE_SEEDS)
def test_the_seeded_state_is_cpythons(seed):
    """Both halves of all 624 words, and the index, right after seeding."""
    ours = Random(seed)
    words = [(h << 16) | lo for h, lo in zip(ours._hi, ours._lo)]
    state = random.Random(seed).getstate()[1]
    assert words == list(state[:624])
    assert ours._i == state[624] == 624


@pytest.mark.parametrize("k", [1, 15, 16, 17, 29, 30, 31, 32, 33, 64])
def test_getrandbits_at_the_half_word_edges(k):
    for seed in EDGE_SEEDS:
        a, b = Random(seed), random.Random(seed)
        assert [a.getrandbits(k) for _ in range(700)] == [b.getrandbits(k) for _ in range(700)]


def test_random_matches_cpython_over_thousands_of_draws():
    for seed in (0, 2**30, 2**32 - 1, -7):
        a, b = Random(seed), random.Random(seed)
        assert [a.random() for _ in range(3000)] == [b.random() for _ in range(3000)]


def test_the_twist_wraps_past_two_blocks_with_mixed_widths():
    """More than 1,248 words from one generator, the widths changing as they go."""
    widths = (1, 30, 16, 17, 32, 15, 29, 31)
    for seed in (5216, 2**31, -3):
        a, b = Random(seed), random.Random(seed)
        got = [a.getrandbits(widths[i % 8]) for i in range(2600)]
        want = [b.getrandbits(widths[i % 8]) for i in range(2600)]
        assert got == want


@pytest.mark.parametrize("bits", [1, 2, 8, 15, 16, 17, 29, 30, 31, 32, 33])
def test_randbelow_at_powers_of_two(bits):
    p = 2**bits
    for seed in (0, 2**30, 99):
        a, b = Random(seed), random.Random(seed)
        for n in (p - 1, p, p + 1):
            if n > 0:
                assert [a._randbelow(n) for _ in range(200)] == [
                    b._randbelow(n) for _ in range(200)]


# ---- no big int on a 32-bit MicroPython ------------------------------------------------------


def _armv7_runner():
    """The ``docker run`` that starts the clock's own MicroPython (32-bit ARMv7, under QEMU),
    or None when Docker, the built binary or the local ``alpine:3.21`` image is missing."""
    import shutil
    import subprocess

    from arthur.clocktool import BINARY

    if shutil.which("docker") is None or not Path(BINARY).is_file():
        return None
    try:
        p = subprocess.run(["docker", "image", "inspect", "alpine:3.21"], capture_output=True,
                           timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    repo = Path(__file__).resolve().parent.parent
    rel = Path(BINARY).resolve().relative_to(repo)
    return ["docker", "run", "--rm", "--pull=never", "--network=none", "--platform",
            "linux/arm/v7", "-v", f"{repo}:/w:ro", "alpine:3.21", f"/w/{rel}", "-X",
            "heapsize=4M"]


def test_no_big_ints_on_a_32_bit_micropython():
    """On the clock's 32-bit build, seeding allocates only the two state lists (about 5 KB;
    it was about 0.5 MB with the words held whole), and drawing integers allocates nothing.
    ``random()`` allocates only its floats. Measured with ``gc.mem_alloc()`` and the
    collector off (``mpy/rngalloc.py``)."""
    import subprocess

    cmd = _armv7_runner()
    if cmd is None:
        pytest.skip("Docker, alpine:3.21 or deploy/clock/micropython/dist/micropython-armv7 "
                    "is missing: the 32-bit allocation check did NOT run "
                    "(scripts/clock.py build)")
    p = subprocess.run([*cmd, "/w/mpy/rngalloc.py", "/w"], capture_output=True, text=True,
                       timeout=300, check=False)
    assert p.returncode == 0, p.stderr
    lines = p.stdout.split()
    assert lines[-1] == "done", p.stdout
    got = dict(zip(lines[:-1:2], (int(x) for x in lines[1:-1:2])))
    print("bytes allocated on 32-bit MicroPython:", got)
    assert got["base"] < 6_000  # two lists of 624 small ints, built once
    for name in ("seed_small", "seed_max_small", "seed_negative"):
        assert got[name] < 6_000, name  # two lists of 624 words (5 KB) and the key
    assert got["seed_crc32"] < 6_000  # the same, and a few big ints from the seed itself
    for name, used in got.items():
        if name.endswith("_x2000") and name != "random_x2000":
            assert used < 200, name
    assert got["random_x2000"] < 2000 * 64  # its floats only (about 1 KB a draw before)
