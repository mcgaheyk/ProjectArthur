"""arthur.compat on CPython: every helper is CPython's own thing (D-0029).

The MicroPython side of each helper is exercised by the on-clock guard
(tests/test_micropython.py), which compares what the shared code draws and decides.
"""

from __future__ import annotations

import dataclasses
import random
import typing

import pytest

from arthur import compat


def test_cpython_is_not_micropython():
    assert compat.IS_MICROPYTHON is False


def test_record_is_a_frozen_dataclass():
    @compat.record("a b c")
    class R:
        a: int
        b: str = "x"
        c: tuple[int, ...] = ()

        def twice(self) -> int:
            return 2 * self.a

    r = R(1)
    assert dataclasses.is_dataclass(r)
    assert r == R(1, "x", ())
    assert r.twice() == 2
    assert compat.field_names(R) == ("a", "b", "c")
    assert compat.field_default(R, "b") == "x"
    assert compat.replace(r, b="y") == R(1, "y", ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.a = 2  # type: ignore[misc]


def test_record_fields_must_match_the_annotations():
    with pytest.raises(TypeError, match="do not match"):
        @compat.record("a c")
        class Bad:
            a: int
            b: int


def test_tuple_record_is_a_named_tuple():
    @compat.tuple_record("x y")
    class P:
        """A point."""

        x: int
        y: int = 0

        def flip(self) -> P:
            return P(self.y, self.x)

    p = P(3)
    assert isinstance(p, tuple)
    assert typing.NamedTuple in getattr(P, "__orig_bases__", ())
    assert p == (3, 0)
    assert P._fields == ("x", "y")
    assert P._field_defaults == {"y": 0}
    assert p.flip() == P(0, 3)
    assert repr(p) == "P(x=3, y=0)"
    assert P.__doc__ == "A point."


def test_random_is_cpythons():
    assert compat.Random is random.Random


@pytest.mark.parametrize("x", [0, 30, 33.3, 12.5, 0.1, 99.99, 1e-5, 2.5e3])
def test_decimal_ratio_reads_the_printed_decimal(x):
    from fractions import Fraction

    assert compat.decimal_ratio(x) == (Fraction(str(x)).numerator,
                                       Fraction(str(x)).denominator)


def test_stable_sorted_keeps_ties_in_order():
    items = [(1, "b"), (0, "z"), (1, "a"), (0, "y")]
    assert compat.stable_sorted(items, key=lambda t: t[0]) == [
        (0, "z"), (0, "y"), (1, "b"), (1, "a")]


def test_zip_strict_refuses_lengths_that_differ():
    assert list(compat.zip_strict([1, 2], "ab")) == [(1, "a"), (2, "b")]
    with pytest.raises(ValueError):
        list(compat.zip_strict([1, 2], "a"))
