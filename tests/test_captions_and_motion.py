"""Captions from the config fit where they are drawn; idle stays nearly still."""

from dataclasses import replace
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import list_sizes, list_variants, render
from arthur.render.screens import CAPTION_USES

CFG = default_config()


def _render_everything():
    for fx in FIXTURES.values():
        state = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
        for size in list_sizes():
            for v in list_variants(fx.kind, size):
                for dt in range(8):
                    render(state, fx.now + timedelta(seconds=dt), CFG, size=size, variant=v)


def test_every_configured_caption_fits_every_line_it_is_used_on():
    _render_everything()
    too_wide = []
    for key, captions in CFG.captions.items():
        assert key in CAPTION_USES, f"[captions] {key} is never drawn by any screen"
        for size, width, font, scale in CAPTION_USES[key]:
            for c in captions:
                assert all(ord(ch) in font.glyphs for ch in c), f"{c!r}: glyph missing"
                if font.width(c, scale) > width:
                    too_wide.append(f"{key} {size}: {c!r} is {font.width(c, scale)}px > {width}")
    assert not too_wide, "captions that would be skipped:\n" + "\n".join(too_wide)


def _caption_row(f):
    """The in-use caption headline: rows 1-7, columns 17-47 (beside the poop emoji)."""
    return [f.get(x, y) for y in range(1, 8) for x in range(17, 48)]


def test_caption_is_fixed_for_a_visit_and_varies_between_visits():
    fx = FIXTURES["in_use"]
    picks = set()
    for visit in range(12):
        since = fx.now - timedelta(seconds=47 + 977 * visit)
        snap = replace(fx.snapshot, status_since=since)
        state = to_display_state(snap, fx.health, fx.now, CFG)
        # Same visit, across a minute: every time the poop page (the caption) comes round,
        # it shows the same caption. The fixtures' "now" starts the 6 s loop; seconds 4-5
        # are the poop page.
        rows = {
            tuple(_caption_row(render(state, fx.now + timedelta(seconds=t), CFG, "52x16",
                                      "duo")))
            for t in [*range(4, 60, 6), *range(5, 60, 6)]
        }
        assert len(rows) == 1
        picks.add(rows.pop())
    assert len(picks) >= 3


@pytest.mark.parametrize("last_visit_s", [2 * 3600, 5 * 60])
@pytest.mark.parametrize("size", list(list_sizes()))
def test_idle_changes_rarely(size, last_visit_s):
    """Every changed frame is an HTTP push to the clock: idle may blink or flick its tail a
    couple of times a minute at most."""
    fx = FIXTURES["ready"]
    snap = replace(fx.snapshot, last_visit_at=fx.now - timedelta(seconds=last_visit_s))
    state = to_display_state(snap, fx.health, fx.now, CFG)
    for v in list_variants("ready", size):
        prev = None
        changes = 0
        for t in range(300):  # five minutes, once a second
            f = render(state, fx.now + timedelta(seconds=t), CFG, size=size, variant=v)
            if prev is not None and f != prev:
                changes += 1
            prev = f
        # A blink is two changes (close, open). Two blinks a minute = 20 in five minutes,
        # plus the "last visit" text ticking over once a minute at most.
        assert changes <= 26, f"{v}: {changes} frame changes in 5 minutes"


@pytest.mark.parametrize("name", ["in_use", "waiting", "cleaning", "interrupted"])
def test_live_states_animate(name):
    fx = FIXTURES[name]
    state = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    for size in list_sizes():
        frames = {render(state, fx.now + timedelta(seconds=t), CFG, size=size).to_rgb888()
                  for t in range(6)}
        assert len(frames) >= 2
