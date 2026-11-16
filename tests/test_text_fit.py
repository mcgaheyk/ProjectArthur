"""Every label fits its box, and nothing is drawn on or against a label.

Renders every fixture x variant over a span of seconds (every blink, animation phase
and caption page) with the strict text mode on. In strict mode ``put_text`` raises when a
label is wider than its box, and ``render`` checks that no other pixel touches a label.
"""

from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import Frame, list_sizes, list_variants, render
from arthur.render.font import small_font
from arthur.render.text import (
    TextFitError,
    check_text,
    draw_behind_text,
    put_text,
    strict_text,
)

CFG = default_config()

#: Seconds rendered per case: long enough to cover every animation loop and caption page.
SPAN_S = 24


def cases():
    for name, fx in FIXTURES.items():
        for size in list_sizes():
            for v in list_variants(fx.kind, size):
                yield pytest.param(name, size, v, id=f"{name}-{size[0]}x{size[1]}-{v}")


@pytest.mark.parametrize(("name", "size", "variant"), list(cases()))
def test_every_label_fits_and_stands_clear(name, size, variant):
    fx = FIXTURES[name]
    state = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    with strict_text():
        for dt in range(SPAN_S):
            render(state, fx.now + timedelta(seconds=dt), CFG, size=size, variant=variant)


def test_strict_mode_catches_overflow():
    f = Frame(52, 16)
    with strict_text(), pytest.raises(TextFitError):
        put_text(f, 17, 1, "MUCH TOO LONG FOR THIS", (255, 255, 255), small_font(), right=47)


def test_non_strict_clips_to_the_box_not_beyond():
    f = Frame(52, 16)
    put_text(f, 17, 1, "MUCH TOO LONG FOR THIS", (255, 255, 255), small_font(), right=30)
    assert all(f.get(x, y) == (0, 0, 0) for x in range(31, 52) for y in range(16))


def _label():
    f = Frame(52, 16)
    put_text(f, 20, 9, "2H AGO", (255, 255, 255), small_font(), right=50)
    return f


LINE = [(x, 11, (255, 122, 0)) for x in range(14, 45)]  # a line across the label's middle


def test_a_line_through_a_label_is_still_caught():
    with strict_text() as records, pytest.raises(TextFitError):
        f = _label()
        for x, y, c in LINE:
            f.pixel(x, y, c)
        check_text(f, records)


def test_draw_behind_text_tints_ink_hides_gaps_and_is_allowed():
    tint = (1, 2, 3)
    with strict_text() as records:
        f = _label()
        ink = set(records[0].ink)
        w = small_font().width("2H AGO")
        tinted = draw_behind_text(f, LINE, tint, box=(20, 9, 20 + w - 1, 13))
        check_text(f, records)  # allowed: no error
    assert tinted and tinted <= ink
    assert all(f.get(x, 11) == (tint if (x, 11) in ink else (0, 0, 0))
               for x in range(20, 20 + w))
    assert f.get(14, 11) == f.get(19, 11) == (255, 122, 0)  # drawn outside the label


def test_draw_behind_text_exempts_only_its_own_pixels():
    with strict_text() as records, pytest.raises(TextFitError):
        f = _label()
        draw_behind_text(f, LINE[:3], (1, 2, 3), box=(20, 9, 42, 13))
        f.pixel(19, 12, (9, 9, 9))  # something else against the label
        check_text(f, records)
