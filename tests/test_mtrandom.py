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
