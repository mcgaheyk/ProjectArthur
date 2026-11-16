import base64
import json

import pytest

from arthur.render import Frame
from arthur.sim.clock import STALE_RGB, ClockError, FakeClock, parse_color


class FakeTime:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(w=32, h=8):
    t = FakeTime()
    return FakeClock(w, h, time_fn=t), t


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


@pytest.mark.parametrize("size", [(32, 8), (52, 16)])
def test_bitmap_base64_round_trip(size):
    clock, _ = make(*size)
    f = pattern_frame(*size)
    clock.push("litter", bitmap_body(f))
    assert clock.screen() == f
    assert clock.screen_json()["pixels"] == f.to_ints()


def test_bitmap_colour_array_placed_and_clipped():
    clock, _ = make()
    clock.push("x", body({"draw": [["bitmap", 30, 6, 3, 3, ["#FF0000", "0F0", [0, 0, 255],
                                                              255, ["HSV", 0, 100, 100]]]]}))
    s = clock.screen()
    assert s.get(30, 6) == (255, 0, 0)
    assert s.get(31, 6) == (0, 255, 0)
    # third colour at x=32 is clipped; fourth starts row 7
    assert s.get(30, 7) == (0, 0, 255)
    assert s.get(31, 7) == (255, 0, 0)  # HSV red
    assert s.get(0, 0) == (0, 0, 0)


def test_bitmap_wrong_length_is_422():
    clock, _ = make()
    data = base64.b64encode(b"\x00" * 100).decode()
    e = err(clock.push, "x", body({"draw": [["bitmap", 0, 0, 32, 8, data]]}))
    assert e.status == 422 and e.code == "validationFailed" and e.field == "draw[0]"
    assert "768" in e.message


def test_bitmap_bad_base64_and_args():
    clock, _ = make()
    assert err(clock.push, "x", body({"draw": [["bitmap", 0, 0, 1, 1, "!!!"]]})).status == 422
    assert err(clock.push, "x", body({"draw": [["bitmap", 0, 0, 1]]})).field == "draw[0]"
    assert err(clock.push, "x", body({"draw": [["bitmap", "a", 0, 1, 1, "AAAA"]]})).status == 422
    assert err(clock.push, "x", body({"draw": [["nope", 1]]})).code == "validationFailed"


def test_unknown_key_is_422_naming_field_and_nothing_saved():
    clock, _ = make()
    f = pattern_frame(32, 8)
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


def test_body_limit_per_size():
    small, _ = make(32, 8)
    big, _ = make(52, 16)
    raw = body({"draw": [], "durationMs": 1, "lifetimeExpiry": "remove" + " " * 0}) + b" " * 9000
    assert err(small.push, "x", raw).status == 413
    big.push("x", raw)  # 2 MiB limit on the 52x16
    assert err(big.push, "x", b" " * (2 * 1024 * 1024 + 1)).status == 413


def test_non_json_content_type_is_415():
    clock, _ = make()
    assert err(clock.push, "x", b'{"draw":[]}', "text/plain").status == 415
    clock.push("x", b'{"draw":[]}', None)  # an omitted header is accepted


def test_mark_after_expiry_with_injected_time():
    clock, t = make()
    f = pattern_frame(32, 8)
    clock.push("litter", bitmap_body(f, lifetimeMs=90_000, lifetimeExpiry="mark"))
    t.t += 89.9
    assert clock.screen() == f
    st = clock.status()
    assert not st["marked"] and st["lifetime_remaining_s"] == pytest.approx(0.1)
    t.t += 0.2
    s = clock.screen()
    assert clock.status()["marked"]
    for x in range(32):
        assert s.get(x, 0) == STALE_RGB and s.get(x, 7) == STALE_RGB
    for y in range(8):
        assert s.get(0, y) == STALE_RGB and s.get(31, y) == STALE_RGB
    assert s.get(5, 3) == f.get(5, 3)  # inside untouched


def test_remove_mode_drops_the_app():
    clock, t = make()
    clock.push("litter", bitmap_body(pattern_frame(32, 8), lifetimeMs=1000))  # default remove
    t.t += 1.0
    assert clock.screen() == Frame(32, 8)
    assert clock.apps == {} and clock.status()["app"] is None


def test_repush_resets_timer_and_clears_mark():
    clock, t = make()
    f = pattern_frame(32, 8)
    clock.push("litter", bitmap_body(f, lifetimeMs=1000, lifetimeExpiry="mark"))
    t.t += 5
    assert clock.status()["marked"]
    clock.push("litter", bitmap_body(f, lifetimeMs=1000, lifetimeExpiry="mark"))
    assert not clock.status()["marked"]
    assert clock.screen() == f
    assert clock.status()["lifetime_remaining_s"] == pytest.approx(1.0)


def test_most_recent_app_shows_and_delete():
    clock, _ = make()
    a, b = pattern_frame(32, 8), Frame(32, 8, (1, 2, 3))
    clock.push("a", bitmap_body(a))
    clock.push("b", bitmap_body(b))
    assert clock.screen() == b
    clock.delete_app("b")
    assert clock.screen() == a
    clock.delete_app("never-existed")  # idempotent


def test_no_app_is_black():
    clock, _ = make(52, 16)
    assert clock.screen() == Frame(52, 16)


def test_bad_lifetime_expiry_word_is_422_and_wrong_number_type_ignored():
    clock, _ = make()
    assert err(clock.push, "x", body({"draw": [], "lifetimeExpiry": "fade"})).field == \
        "lifetimeExpiry"
    clock.push("x", body({"draw": [], "lifetimeMs": "soon"}))
    assert clock.apps["x"].lifetime_ms == 0


# --- indicators ----------------------------------------------------------------------


def test_indicators_drawn_on_right_edge():
    clock, _ = make()
    clock.push("x", bitmap_body(Frame(32, 8, (9, 9, 9))))
    clock.set_indicator("1", body({"color": "#00FF00"}))
    clock.set_indicator(3, body({"color": [255, 0, 0]}))
    s = clock.screen()
    for x, y in ((31, 0), (30, 0), (31, 1)):
        assert s.get(x, y) == (0, 255, 0)
    for x, y in ((31, 7), (31, 6), (30, 7)):
        assert s.get(x, y) == (255, 0, 0)
    assert s.get(31, 3) == (9, 9, 9)


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
    assert clock.screen().get(31, 3) == (0, 0, 255)
    t.t = 1000.6  # second half: off
    assert clock.screen().get(31, 3) == (0, 0, 0)
    clock.set_indicator("2", body({"color": None}))
    assert not clock.indicators[2].on
    clock.set_indicator("1", body({"color": "#FFF", "blinkMs": 99999}))  # out of range → 0
    assert clock.indicators[1].blink_ms == 0
    clock.clear_indicator("1")
    assert not clock.indicators[1].on
    assert err(clock.clear_indicator, "0").status == 404


def test_indicators_at_52x16():
    clock, _ = make(52, 16)
    clock.set_indicator("2", body({"color": "#FFFFFF"}))
    s = clock.screen()
    assert s.get(51, 7) == (255, 255, 255) and s.get(51, 8) == (255, 255, 255)


def test_parse_color_forms():
    assert parse_color("#102030") == (16, 32, 48)
    assert parse_color("102030") == (16, 32, 48)
    assert parse_color("F00") == (255, 0, 0)
    assert parse_color([1, 2, 3]) == (1, 2, 3)
    assert parse_color(0x010203) == (1, 2, 3)
    assert parse_color(["HSV", 120, 100, 100]) == (0, 255, 0)
    for bad in ("#12345", [1, 2], [1, 2, 300], True, None, "zzzzzz", -1):
        assert parse_color(bad) is None
