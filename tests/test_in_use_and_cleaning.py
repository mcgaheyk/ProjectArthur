"""In use (joke page and cat page), cleaning (soapy ring) and the soapy-blue palette."""

import itertools
from dataclasses import replace
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import Frame, list_variants, overlay_stale, render
from arthur.render.font import large_font, small_font
from arthur.render.icons import draw_icon, icon_cells, icon_names
from arthur.render.screens import (
    _REGISTRY,
    BOLD_GLYPHS,
    EDGE_RING,
    POOP_FACE_LOOP,
    POOP_Y,
    SMUG_LOOP,
    STINK_TOP,
    _clean_left,
    bold_width,
    fmt_countdown,
    in_use_page,
    phase,
    ring_pixels,
)
from arthur.render.text import check_text, strict_text

CFG = default_config()
S = (52, 16)
JOKES = ("IN SESH", "NO PEEK", "POOPIN?", "PEEIN?", "THRONE", "VIP ONLY")


def _state(name, since_s=None, cfg=CFG, **snap):
    fx = FIXTURES[name]
    s = fx.snapshot
    if since_s is not None:
        began = fx.now - timedelta(seconds=since_s)
        s = replace(s, status_since=began,
                    visit_started_at=began if s.visit_started_at is not None else None)
    if snap:
        s = replace(s, **snap)
    return to_display_state(s, fx.health, fx.now, cfg), fx.now


def _strict(state, now, cfg=CFG):
    """Render under the strict text check; also returns the labels drawn, in order."""
    fn = _REGISTRY[(S, state.kind)]["default"]
    with strict_text() as records:
        f = fn(state, now, cfg)
        check_text(f, records)
    assert f == render(state, now, cfg)
    return f, [r.text for r in records]


def _icon_frame(name, y=0):
    f = Frame(*S)
    draw_icon(f, name, 0, y, CFG, size=16)
    return f


def _left(f, w=16):
    return tuple(f.get(x, y) for y in range(16) for x in range(w))


# --- in use: one layout, two pages -----------------------------------------------------------


def _visit(since_s=0, cfg=CFG):
    """An in-use state that began exactly at the returned ``start``."""
    state, now = _state("in_use", since_s, cfg)
    return state, now - timedelta(seconds=since_s)


def _at(start, t):
    return start + timedelta(seconds=t)


def test_in_use_has_one_layout_and_no_box_icons():
    assert list_variants("in_use", S) == ["default"]
    assert not [n for n in icon_names() if n.startswith("arthur_box")]
    assert "door" in icon_names()  # still the vacant idle layout's icon


def test_in_use_jokes_are_the_six_and_all_fit():
    assert CFG.captions["in_use"] == JOKES
    for j in JOKES:
        assert large_font().width(j) <= 31, j


@pytest.mark.parametrize(("joke_s", "cat_s"), [(6, 4), (3, 2), (5, 7)])
def test_page_lengths_follow_the_config(joke_s, cat_s):
    cfg = replace(CFG, timings=replace(CFG.timings, in_use_joke_s=joke_s, in_use_cat_s=cat_s))
    state, start = _visit(cfg=cfg)
    loop = joke_s + cat_s
    for t in range(3 * loop):
        page, k = in_use_page(state, _at(start, t), cfg)
        assert page == ("joke" if t % loop < joke_s else "cat"), t
        assert k == t // loop
        _, texts = _strict(state, _at(start, t), cfg)
        assert ("IN USE" in texts) == (page == "joke"), t


def test_default_page_lengths():
    assert (CFG.timings.in_use_joke_s, CFG.timings.in_use_cat_s) == (6, 4)


def test_joke_page_shows_the_shared_poop_face_and_in_use():
    state, start = _visit()
    for t in range(6):
        now = _at(start, t)
        f, texts = _strict(state, now)
        assert texts[1:] == ["IN USE"] and texts[0] in JOKES
        assert not any(ch.isdigit() for ch in "".join(texts))  # no timer on this page
        face = POOP_FACE_LOOP[phase(now, len(POOP_FACE_LOOP))]
        cells = {(x, y + POOP_Y) for x, y in _icon_cells_all(face)}
        want = _icon_frame(face, POOP_Y)
        assert all(f.get(x, y) == want.get(x, y) for x, y in cells), (t, face)


def _icon_cells_all(name):
    out = []
    for ch in set(CFG.palette.chars.values()) - {"."}:
        try:
            out += icon_cells(name, 16, ch)
        except KeyError:
            pass
    return out


def _faces_on_joke_page(state, start, loops):
    """(face, position within the joke page) for every joke-page second."""
    seen = set()
    for t in range(loops * 10):
        now = _at(start, t)
        page, _ = in_use_page(state, now, CFG)
        if page == "joke":
            seen.add((POOP_FACE_LOOP[phase(now, len(POOP_FACE_LOOP))], t % 10))
    return seen


def test_poop_face_runs_on_its_own_not_with_the_pages():
    # Within one visit, consecutive joke pages do not replay the same faces.
    state, start = _visit()
    seqs = [tuple(POOP_FACE_LOOP[phase(_at(start, k * 10 + t), len(POOP_FACE_LOOP))]
                  for t in range(6)) for k in range(3)]
    assert len(set(seqs)) == 3
    # Over visits that began at different seconds, each face other than the plain smile turns
    # up at different positions within the joke page.
    positions: dict[str, set[int]] = {}
    for since in range(15):
        state, start = _visit(since)
        for face, pos in _faces_on_joke_page(state, start, 3):
            positions.setdefault(face, set()).add(pos)
    for face in set(POOP_FACE_LOOP) - {"poop_full"}:
        assert len(positions.get(face, ())) >= 2, face


def _stink(f):
    k = CFG.rgb("stink")
    return {(x, y) for y in range(16) for x in range(16) if f.get(x, y) == k}


def test_stink_lines_rise():
    state, start = _visit()
    frames = [_stink(render(state, _at(start, t), CFG)) for t in range(6)]
    assert len(set(map(frozenset, frames))) >= 3
    for a, b in itertools.pairwise(frames):
        moved = {(x, y - 1) for x, y in a if y - 1 >= STINK_TOP}
        assert moved <= b  # everything still in view one row higher: rising
        assert a != b


def test_stink_keeps_clear_of_poop_text_and_border():
    state, start = _visit()
    for t in range(30):
        now = _at(start, t)
        if in_use_page(state, now, CFG)[0] != "joke":
            continue
        f, _ = _strict(state, now)  # the text check: nothing touches the joke or IN USE
        face = POOP_FACE_LOOP[phase(now, len(POOP_FACE_LOOP))]
        poop = {(x, y + POOP_Y) for x, y in _icon_cells_all(face)}
        for x, y in _stink(f):
            assert y >= 1
            near = {(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            assert not near & poop, (t, x, y)


def _jokes(state, start, loops):
    """The joke shown on each second of each joke page, by loop."""
    out = []
    for k in range(loops):
        texts = {tuple(_strict(state, _at(start, k * 10 + t))[1]) for t in range(6)}
        assert len(texts) == 1, f"joke changed within page {k}: {texts}"
        out.append(texts.pop()[0])
    return out


def test_jokes_stay_put_per_page_change_each_page_and_all_show():
    state, start = _visit()
    jokes = _jokes(state, start, 12)
    assert all(a != b for a, b in itertools.pairwise(jokes))
    assert set(jokes[:6]) == set(JOKES)
    assert jokes[6:] == jokes[:6]


def test_visits_shuffle_differently():
    orders = set()
    for v in range(8):
        state, start = _visit(since_s=97 * v + 13)
        # _visit keeps the fixture's now; the visit's own start is what seeds the order.
        orders.add(tuple(_jokes(state, start, 6)))
    assert len(orders) >= 3
    assert len({o[0] for o in orders}) >= 2  # visits start on different jokes


def test_cat_page_shows_smug_arthur_timer_and_drawer_bar():
    state, start = _visit()
    smug = {_left(_icon_frame(n)) for n in SMUG_LOOP}
    box = {CFG.rgb("ghost"), CFG.rgb("dim")}
    for t in range(6, 10):
        now = _at(start, t)
        f, texts = _strict(state, now)
        assert texts == [f"0:{t:02d}"]
        assert _left(f) in smug
        assert not box & {f.get(x, y) for x in range(16) for y in range(16)}  # no litter box
        assert f.get(17, 13) == CFG.rgb("drawer") and f.get(47, 14) == CFG.rgb("dim")


def test_smug_poses_differ_and_share_the_smirk():
    names = set(SMUG_LOOP)
    assert len(names) >= 4
    assert len({_left(_icon_frame(n)) for n in names}) == len(names)
    mouth = {tuple(_icon_frame(n).get(x, y) for x in range(4, 11) for y in (9, 10))
             for n in names - {"arthur_smug_tail"}}
    assert len(mouth) == 1


# --- cleaning ------------------------------------------------------------------------------


def _cleaning(dt, **snap):
    state, now = _state("cleaning", **snap)
    return render(state, now + timedelta(seconds=dt), CFG)


def test_cleaning_has_one_layout_with_a_ring():
    assert list_variants("cleaning", S) == ["default"]
    assert (S, "cleaning", "default") in EDGE_RING


def test_cleaning_ring_is_soap_with_moving_bubbles():
    soap, hi = CFG.rgb("soap"), CFG.rgb("soap_hi")
    prev = None
    for dt in range(12):
        f = _cleaning(dt)
        ring = ring_pixels(52, 16)
        assert {f.get(x, y) for x, y in ring} == {soap, hi}
        bubbles = {(x, y) for x, y in ring if f.get(x, y) == hi}
        assert len(bubbles) >= 8
        assert bubbles != prev
        prev = bubbles


def test_cleaning_keeps_a_gap_inside_the_ring():
    off = CFG.rgb("off")
    for dt in range(30):
        f = _cleaning(dt)
        gap = {(x, y) for x in range(1, 51) for y in (1, 14)}
        gap |= {(x, y) for x in (1, 50) for y in range(1, 15)}
        assert all(f.get(x, y) == off for x, y in gap), dt


def test_stale_overlay_covers_the_cleaning_ring():
    stale = CFG.rgb("stale")
    for dt in (0, 1, 2):
        f = overlay_stale(_cleaning(dt), stale)
        assert all(f.get(x, y) == stale for x, y in ring_pixels(52, 16))


def test_countdown_digits_cover_every_value():
    for s in range(600):
        text = fmt_countdown(s)
        assert all(ch in BOLD_GLYPHS for ch in text), text
        assert bold_width(text) <= 34, text
    for s in (600, 5999, 6000, 359999, 360000):
        assert all(ch in BOLD_GLYPHS for ch in fmt_countdown(s)), s
    assert all(len(g) == 6 for g in BOLD_GLYPHS.values())


def test_cleaning_renders_every_countdown_cleanly():
    state, now = _state("cleaning")
    for left in [*range(0, 600, 7), 599]:
        s = replace(state, cycle_end=now + timedelta(seconds=left))
        f, texts = _strict(s, now)
        assert texts == ["CLEANING"]
        assert _clean_left(s, now) == fmt_countdown(left)
        # The digits sit on rows 2-7, right of the drum.
        assert any(f.get(x, y) == CFG.rgb("text") for x in range(16, 50) for y in range(2, 8))
    s = replace(state, cycle_end=None)
    assert _clean_left(s, now) == "--"
    _strict(s, now)


def test_cleaning_word_fits():
    assert CFG.captions["cleaning"] == ("CLEANING",)
    assert small_font().width("CLEANING") <= 34


# --- soapy blue ----------------------------------------------------------------------------


def test_no_mint_anywhere():
    assert "mint" not in CFG.palette.colors
    assert "M" not in CFG.palette.chars.values()
    old = (0x30, 0xFF, 0xB0)
    for name, fx in FIXTURES.items():
        state = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
        for v in list_variants(fx.kind, S):
            for dt in range(4):
                assert old not in render(state, fx.now + timedelta(seconds=dt), CFG,
                                         variant=v).pixels, name


def test_soap_roles_are_their_own_colours():
    for role in ("soap", "soap_hi"):
        same = [r for r in CFG.palette.colors if r != role and CFG.rgb(r) == CFG.rgb(role)]
        assert not same, role


def test_just_cleaned_is_soapy_blue():
    state, now = _state("just_cleaned")
    for dt in (0, 1):
        f = render(state, now + timedelta(seconds=dt), CFG)
        sparkle = {f.get(x, y) for x in range(16) for y in range(16)}
        off = {CFG.rgb("off")}
        top = {f.get(x, y) for x in range(17, 48) for y in range(1, 6)} - off
        word = {f.get(x, y) for x in range(17, 48) for y in range(7, 14)} - off
        assert CFG.rgb("soap") in sparkle
        assert top == {CFG.rgb("soap_hi")}  # FRESHLY
        assert word == {CFG.rgb("soap")}  # CLEANED
