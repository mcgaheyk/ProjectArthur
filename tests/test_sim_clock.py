import base64
import json

import pytest

from arthur.render import Frame
from arthur.sim.clock import BODY_LIMIT, STALE_RGB, ClockError, FakeClock, parse_color


class FakeTime:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(w=52, h=16, enlarge_apps=False):
    """A fake clock with ``enlargeApps`` off (native size) unless asked otherwise, so pushed
    bitmaps read back as pushed. The clock's own default (on) is tested separately."""
    t = FakeTime()
    return FakeClock(w, h, time_fn=t, enlarge_apps=enlarge_apps), t


def body(d) -> bytes:
    return json.dumps(d).encode()


def pattern_frame(w, h):
    f = Frame(w, h)
    for i in range(w * h):
        f.pixels[i] = ((i * 7) % 256, (i * 13) % 256, (i * 29 + 1) % 256)
    return f


def bitmap_body(f: Frame, **extra) -> bytes:
    data = base64.b64encode(f.to_rgb888()).decode()
    return body({"draw": [["bitmap", 0, 0, f.width, f.height, data]], **extra})


def err(fn, *a, **kw) -> ClockError:
    with pytest.raises(ClockError) as ei:
        fn(*a, **kw)
    return ei.value


def test_bitmap_base64_round_trip():
    clock, _ = make()
    f = pattern_frame(52, 16)
    clock.push("litter", bitmap_body(f))
    assert clock.screen() == f
    assert clock.screen_json()["pixels"] == f.to_ints()


def test_bitmap_colour_array_placed_and_clipped():
    clock, _ = make()
    clock.push("x", body({"draw": [["bitmap", 50, 14, 3, 3, ["#FF0000", "0F0", [0, 0, 255],
                                                               255, ["HSV", 0, 100, 100]]]]}))
    s = clock.screen()
    assert s.get(50, 14) == (255, 0, 0)
    assert s.get(51, 14) == (0, 255, 0)
    # third colour at x=52 is clipped; fourth starts row 15
    assert s.get(50, 15) == (0, 0, 255)
    assert s.get(51, 15) == (255, 0, 0)  # HSV red
    assert s.get(0, 0) == (0, 0, 0)


def test_bitmap_wrong_length_is_422():
    clock, _ = make()
    data = base64.b64encode(b"\x00" * 100).decode()
    e = err(clock.push, "x", body({"draw": [["bitmap", 0, 0, 52, 16, data]]}))
    assert e.status == 422 and e.code == "validationFailed" and e.field == "draw[0]"
    assert "2496" in e.message


def test_bitmap_bad_base64_and_args():
    clock, _ = make()
    assert err(clock.push, "x", body({"draw": [["bitmap", 0, 0, 1, 1, "!!!"]]})).status == 422
    assert err(clock.push, "x", body({"draw": [["bitmap", 0, 0, 1]]})).field == "draw[0]"
    assert err(clock.push, "x", body({"draw": [["bitmap", "a", 0, 1, 1, "AAAA"]]})).status == 422
    assert err(clock.push, "x", body({"draw": [["nope", 1]]})).code == "validationFailed"


def test_unknown_key_is_422_naming_field_and_nothing_saved():
    clock, _ = make()
    f = pattern_frame(52, 16)
    e = err(clock.push, "x", bitmap_body(f, bogus=1))
    assert (e.status, e.code, e.field) == (422, "validationFailed", "bogus")
    assert e.body() == {"error": {"code": "validationFailed", "message": e.message,
                                  "field": "bogus"}}
    assert clock.apps == {}  # all-or-nothing


@pytest.mark.parametrize("key", ["text", "icon", "progress", "effect", "barChart", "overlay"])
def test_not_simulated_keys_fail_loudly(key):
    clock, _ = make()
    e = err(clock.push, "x", body({key: 1}))
    assert e.status == 422 and e.code == "notSimulated" and e.field == key
    assert "NOT SIMULATED" in e.message
    assert clock.apps == {}


def test_not_simulated_draw_command():
    clock, _ = make()
    e = err(clock.push, "x", body({"draw": [["pixel", 0, 0, "#FFF"]]}))
    assert e.code == "notSimulated" and e.field == "draw[0]"


def test_empty_body_and_empty_object_are_422():
    clock, _ = make()
    assert err(clock.push, "x", b"").status == 422
    assert err(clock.push, "x", b"{}").status == 422
    assert err(clock.push, "x", b"{bad").code == "invalidJson"


def test_bad_name_is_400():
    clock, _ = make()
    e = err(clock.push, "bad name!", b'{"draw":[]}')
    assert (e.status, e.code, e.field) == (400, "invalidName", "name")
    assert err(clock.push, "x" * 33, b'{"draw":[]}').code == "invalidName"


def test_body_limit_is_2_mib():
    clock, _ = make()
    assert clock.body_limit == BODY_LIMIT == 2 * 1024 * 1024
    # Padded with whitespace to exactly the limit: accepted.
    raw = body({"draw": [], "durationMs": 1})
    clock.push("x", raw + b" " * (BODY_LIMIT - len(raw)))
    e = err(clock.push, "x", raw + b" " * (BODY_LIMIT - len(raw) + 1))
    assert e.status == 413 and e.code == "payloadTooLarge"


def test_non_json_content_type_is_415():
    clock, _ = make()
    assert err(clock.push, "x", b'{"draw":[]}', "text/plain").status == 415
    clock.push("x", b'{"draw":[]}', None)  # an omitted header is accepted


def test_mark_after_expiry_with_injected_time():
    clock, t = make()
    f = pattern_frame(52, 16)
    clock.push("litter", bitmap_body(f, lifetimeMs=90_000, lifetimeExpiry="mark"))
    t.t += 89.9
    assert clock.screen() == f
    st = clock.status()
    assert not st["marked"] and st["lifetime_remaining_s"] == pytest.approx(0.1)
    t.t += 0.2
    s = clock.screen()
    assert clock.status()["marked"]
    for x in range(52):
        assert s.get(x, 0) == STALE_RGB and s.get(x, 15) == STALE_RGB
    for y in range(16):
        assert s.get(0, y) == STALE_RGB and s.get(51, y) == STALE_RGB
    assert s.get(5, 3) == f.get(5, 3)  # inside untouched


def test_remove_mode_drops_the_app():
    clock, t = make()
    clock.push("litter", bitmap_body(pattern_frame(52, 16), lifetimeMs=1000))  # default remove
    t.t += 1.0
    assert clock.screen() == Frame(52, 16)
    assert clock.apps == {} and clock.status()["app"] is None


def test_repush_resets_timer_and_clears_mark():
    clock, t = make()
    f = pattern_frame(52, 16)
    clock.push("litter", bitmap_body(f, lifetimeMs=1000, lifetimeExpiry="mark"))
    t.t += 5
    assert clock.status()["marked"]
    clock.push("litter", bitmap_body(f, lifetimeMs=1000, lifetimeExpiry="mark"))
    assert not clock.status()["marked"]
    assert clock.screen() == f
    assert clock.status()["lifetime_remaining_s"] == pytest.approx(1.0)


def test_most_recent_app_shows_and_delete():
    clock, _ = make()
    a, b = pattern_frame(52, 16), Frame(52, 16, (1, 2, 3))
    clock.push("a", bitmap_body(a))
    clock.push("b", bitmap_body(b))
    assert clock.screen() == b
    clock.delete_app("b")
    assert clock.screen() == a
    clock.delete_app("never-existed")  # idempotent


def test_no_app_is_black():
    clock, _ = make()
    assert clock.screen() == Frame(52, 16)


def test_bad_lifetime_expiry_word_is_422_and_wrong_number_type_ignored():
    clock, _ = make()
    assert err(clock.push, "x", body({"draw": [], "lifetimeExpiry": "fade"})).field == \
        "lifetimeExpiry"
    clock.push("x", body({"draw": [], "lifetimeMs": "soon"}))
    assert clock.apps["x"].lifetime_ms == 0


# --- enlargeApps --------------------------------------------------------------------


def doubled_quarter(f: Frame) -> Frame:
    """What a TC002 with enlargeApps on shows for ``f``: its top-left 26x8, each pixel 2x2."""
    out = Frame(f.width, f.height)
    for y in range(f.height):
        for x in range(f.width):
            out.pixels[y * f.width + x] = f.get(x // 2, y // 2)
    return out


def test_enlarge_apps_on_by_default_shows_doubled_quarter():
    clock = FakeClock(52, 16, time_fn=FakeTime())
    assert clock.enlarge_apps and clock.settings() == {"enlargeApps": True}
    assert clock.status()["enlarge_apps"] is True
    f = pattern_frame(52, 16)
    clock.push("litter", bitmap_body(f))
    s = clock.screen()
    assert s != f and s == doubled_quarter(f)
    assert s.get(51, 15) == f.get(25, 7) and s.get(50, 14) == f.get(25, 7)


def test_patch_enlarge_apps_takes_effect_at_once():
    clock, _ = make(enlarge_apps=True)
    f = pattern_frame(52, 16)
    clock.push("litter", bitmap_body(f))
    assert clock.patch_settings(body({"enlargeApps": False})) == {"enlargeApps": False}
    assert clock.screen() == f and clock.status()["enlarge_apps"] is False
    assert clock.patch_settings(body({"enlargeApps": True})) == {"enlargeApps": True}
    assert clock.screen() == doubled_quarter(f)


def test_enlarge_keeps_stale_border_and_indicators_native():
    clock, t = make(enlarge_apps=True)
    f = pattern_frame(52, 16)
    clock.push("litter", bitmap_body(f, lifetimeMs=1000, lifetimeExpiry="mark"))
    clock.set_indicator("2", body({"color": "#FFFFFF"}))
    t.t += 2
    s = clock.screen()
    assert s.get(0, 0) == s.get(51, 15) == STALE_RGB
    assert s.get(1, 1) == f.get(0, 0)  # inside the border: still the doubled app
    assert s.get(51, 7) == s.get(51, 8) == (255, 255, 255)
    assert s.get(50, 7) == f.get(25, 3)


def test_patch_settings_validation_is_all_or_nothing():
    clock, _ = make(enlarge_apps=True)
    e = err(clock.patch_settings, body({"enlargeApps": "no"}))
    assert (e.status, e.code, e.field) == (422, "validationFailed", "enlargeApps")
    e = err(clock.patch_settings, body({"enlargeApps": False, "brightness": 40}))
    assert (e.status, e.code, e.field) == (422, "notSimulated", "brightness")
    assert "only 'enlargeApps'" in e.message
    assert clock.enlarge_apps  # nothing applied
    assert err(clock.patch_settings, body([1])).status == 422
    assert err(clock.patch_settings, b"{bad").code == "invalidJson"
    assert err(clock.patch_settings, b'{"enlargeApps":false}', "text/plain").status == 415
    assert err(clock.patch_settings, b" " * (BODY_LIMIT + 1)).status == 413
    assert clock.enlarge_apps


# --- indicators ----------------------------------------------------------------------


def test_indicators_drawn_on_right_edge():
    clock, _ = make()
    clock.push("x", bitmap_body(Frame(52, 16, (9, 9, 9))))
    clock.set_indicator("1", body({"color": "#00FF00"}))
    clock.set_indicator("2", body({"color": "#FFFFFF"}))
    clock.set_indicator(3, body({"color": [255, 0, 0]}))
    s = clock.screen()
    for x, y in ((51, 0), (50, 0), (51, 1)):
        assert s.get(x, y) == (0, 255, 0)
    for x, y in ((51, 7), (51, 8)):
        assert s.get(x, y) == (255, 255, 255)
    for x, y in ((51, 15), (51, 14), (50, 15)):
        assert s.get(x, y) == (255, 0, 0)
    assert s.get(51, 3) == (9, 9, 9)


def test_indicator_rules():
    clock, t = make()
    assert err(clock.set_indicator, "4", body({"color": "#FFF"})).status == 404
    assert err(clock.set_indicator, "abc", body({"color": "#FFF"})).status == 404
    assert err(clock.set_indicator, "2", b"{}").status == 422
    assert err(clock.set_indicator, "2", body({"color": "nope"})).field == "color"
    clock.set_indicator("2", body({"color": "#0000FF"}))
    assert clock.indicators[2].on
    clock.set_indicator("2", body({"color": 0}))
    assert not clock.indicators[2].on and clock.indicators[2].color == (0, 0, 255)
    clock.set_indicator("2", body({"blinkMs": 500}))  # no colour: on/off untouched
    assert not clock.indicators[2].on and clock.indicators[2].blink_ms == 500
    clock.set_indicator("2", body({"color": "#0000FF", "blinkMs": 1000}))
    t.t = 1000.0  # 1000 s → phase 0 of a 1000 ms blink: on
    assert clock.screen().get(51, 7) == (0, 0, 255)
    t.t = 1000.6  # second half: off
    assert clock.screen().get(51, 7) == (0, 0, 0)
    clock.set_indicator("2", body({"color": None}))
    assert not clock.indicators[2].on
    clock.set_indicator("1", body({"color": "#FFF", "blinkMs": 99999}))  # out of range → 0
    assert clock.indicators[1].blink_ms == 0
    clock.clear_indicator("1")
    assert not clock.indicators[1].on
    assert err(clock.clear_indicator, "0").status == 404



def test_parse_color_forms():
    assert parse_color("#102030") == (16, 32, 48)
    assert parse_color("102030") == (16, 32, 48)
    assert parse_color("F00") == (255, 0, 0)
    assert parse_color([1, 2, 3]) == (1, 2, 3)
    assert parse_color(0x010203) == (1, 2, 3)
    assert parse_color(["HSV", 120, 100, 100]) == (0, 255, 0)
    for bad in ("#12345", [1, 2], [1, 2, 300], True, None, "zzzzzz", -1):
        assert parse_color(bad) is None
