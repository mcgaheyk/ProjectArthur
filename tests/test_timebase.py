"""The show's time base: exact frame numbers and the wall-clock beat."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from arthur.timebase import SHOW_FPS, frames_since, until_next_tick

# Awkward starts: just before and after a second, thirds, an odd microsecond count.
STARTS = [
    datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC),
    datetime(2026, 11, 18, 9, 0, 0, 999_999, tzinfo=UTC),
    datetime(2026, 11, 18, 9, 0, 1, 1, tzinfo=UTC),
    datetime(2026, 11, 18, 9, 0, 0, 333_333, tzinfo=UTC),
    datetime(2026, 11, 18, 23, 59, 59, 876_543, tzinfo=UTC),
    datetime(2031, 3, 4, 5, 6, 7, 123_457, tzinfo=UTC),
]
FRAMES = 2400


def boundary_us(k: int, fps: int) -> int:
    """The first whole microsecond at or after frame k's start (k/fps s, exact)."""
    return -(-k * 1_000_000 // fps)


def test_show_fps_is_eight():
    assert SHOW_FPS == 8


@pytest.mark.parametrize("fps", [4, 8, 10, 12])
@pytest.mark.parametrize("start", STARTS)
def test_frames_since_is_exact_at_every_boundary(start, fps):
    for k in range(FRAMES):
        at = start + timedelta(microseconds=boundary_us(k, fps))
        assert frames_since(start, at, fps) == k
        assert frames_since(start, at - timedelta(microseconds=1), fps) == k - 1


def test_float_version_would_be_wrong_at_ten():
    """The trap the integer version avoids: over the same boundaries the float formula
    is off by one on thousands of frames at 10 a second."""
    fps = 10
    misses = sum(
        math.floor((at.timestamp() - start.timestamp()) * fps) != k
        for start in STARTS
        for k in range(FRAMES)
        for at in [start + timedelta(microseconds=boundary_us(k, fps))]
    )
    assert misses > 1000


def test_frames_since_floors_before_start():
    s = STARTS[0]
    assert frames_since(s, s) == 0
    assert frames_since(s, s - timedelta(microseconds=1)) == -1
    assert frames_since(s, s - timedelta(seconds=1)) == -SHOW_FPS


def test_frames_since_default_rate():
    s = STARTS[1]
    assert frames_since(s, s + timedelta(seconds=3, milliseconds=125)) == 3 * 8 + 1


@pytest.mark.parametrize("fps", [4, 8, 10, 12])
def test_until_next_tick_range_and_landing(fps):
    base = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)
    period = 1 / fps
    for us in [*range(0, 1_000_000, 977), 999_999, 1, 125_000, 124_999, 125_001]:
        now = base + timedelta(microseconds=us)
        wait = until_next_tick(now, fps)
        assert 0 < wait <= period + 1e-12
        # It lands on the first boundary after now: k/fps s into the second, k a whole number.
        k = (us / 1_000_000 + wait) * fps
        assert k == pytest.approx(round(k), abs=1e-6)
        assert round(k) == frames_since(base, now, fps) + 1


@pytest.mark.parametrize("fps", [4, 8, 10, 12])
def test_whole_second_is_a_boundary(fps):
    second = datetime(2026, 11, 18, 9, 0, 5, tzinfo=UTC)
    # On the second, the next tick is a full beat away (this tick is the one at the second).
    assert until_next_tick(second, fps) == pytest.approx(1 / fps)
    # Just before it, the second itself is next.
    assert until_next_tick(second - timedelta(microseconds=1), fps) == pytest.approx(1e-6)
    assert until_next_tick(second - timedelta(microseconds=1_000), fps) == pytest.approx(1e-3)


def test_eighths_are_exact_floats():
    """At 8 a second every wait is a whole number of microseconds over 2**k: exact in binary,
    so fake time can step by it and still compare with ==."""
    base = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)
    for k in range(8):
        assert until_next_tick(base + timedelta(microseconds=125_000 * k)) == 0.125
    assert until_next_tick(base + timedelta(microseconds=62_500)) == 0.0625


def test_naive_and_aware_agree():
    aware = datetime(2026, 11, 18, 9, 0, 0, 400_000, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)
    assert until_next_tick(aware) == until_next_tick(naive)
