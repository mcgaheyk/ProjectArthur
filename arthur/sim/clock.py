"""A fake AWTRIX NG 1.2.3 clock: the API subset ProjectArthur uses, and loud refusal of the rest.

Pure: no HTTP and no wall clock. Time comes from an injectable ``time_fn`` (seconds, monotonic),
so tests can move it. The HTTP layer in :mod:`arthur.sim.server` only translates requests into
calls here and :class:`ClockError` into NG-shaped error bodies.

Spec: docs/reference/awtrix-ng.md (checked against NG's openapi.yaml and payload.md at v1.2.3).
Every behaviour that is a guess rather than something in that reference is marked
``ASSUMPTION`` below.
"""

from __future__ import annotations

import base64
import binascii
import colorsys
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from arthur.render.frame import Frame

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
STALE_RGB = (0x6E, 0x07, 0x00)
MAX_NESTING = 16

#: Body limit in bytes: 2 MiB on the Ulanzi TC002 (52x16).
BODY_LIMIT = 2 * 1024 * 1024

#: Pushed-app keys we simulate.
SIMULATED_KEYS = frozenset({"draw", "lifetimeMs", "lifetimeExpiry", "durationMs"})

#: Keys that exist in NG 1.2.3 (payload.md) but that this simulator does not render. A push that
#: uses one is refused with a "not simulated" error instead of being silently accepted.
#: ASSUMPTION: the notification-only keys (name, hold, stack, wakeup, sound) are listed here
#: too; whether NG accepts them on a pushed app is not stated, so we refuse them loudly.
NOT_SIMULATED_KEYS = frozenset({
    "text", "textCase", "font", "textColor", "textBlinkMs", "textFadeMs", "textAlign",
    "scroll", "textOffsetX", "textInFront", "icon", "iconMode", "iconOffsetX", "iconGap",
    "icons", "repeat", "backgroundColor", "barChart", "lineChart", "chartAutoscale",
    "chartColor", "progress", "progressColor", "progressTrackColor", "effect", "effectSpeed",
    "palette", "paletteBlend", "paletteSpan", "paletteSpeed", "overlay", "layout",
    "name", "hold", "stack", "wakeup", "sound",
})

#: Draw commands that exist in NG but that we do not simulate (we only send ``bitmap``).
NOT_SIMULATED_DRAW = frozenset({
    "pixel", "pixels", "line", "rect", "rectFill", "circle", "circleFill", "text",
})


class ClockError(Exception):
    """An NG-style API error: ``{"error": {"code", "message", "field"?}}`` with a status."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            err["field"] = self.field
        return {"error": err}


def not_simulated(what: str, field: str | None = None, status: int = 422) -> ClockError:
    """The simulator's own refusal: real NG would accept this, but we do not render it."""
    return ClockError(
        status,
        "notSimulated",
        f"{what} is part of AWTRIX NG but is NOT SIMULATED by the ProjectArthur fake clock. "
        "The feeder only sends a full-frame 'bitmap' draw (D-0003).",
        field,
    )


def validation(message: str, field: str) -> ClockError:
    return ClockError(422, "validationFailed", message, field)


# --- colours ------------------------------------------------------------------------------


def parse_color(value: Any) -> tuple[int, int, int] | None:
    """NG colour forms → RGB: ``"#RRGGBB"``, ``"RRGGBB"``, ``"F00"``, ``[r,g,b]``,
    ``["HSV",h,s,v]`` or an integer. Returns None for anything unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
        return None
    if isinstance(value, str):
        s = value.strip().removeprefix("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6:
            return None
        try:
            n = int(s, 16)
        except ValueError:
            return None
        return ((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)
    if isinstance(value, list):
        if len(value) == 4 and isinstance(value[0], str) and value[0].upper() == "HSV":
            h, s, v = value[1:]
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (h, s, v)):
                return None
            r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, min(max(s, 0), 100) / 100,
                                          min(max(v, 0), 100) / 100)
            return (round(r * 255), round(g * 255), round(b * 255))
        if len(value) == 3 and all(
            isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255 for x in value
        ):
            return (value[0], value[1], value[2])
    return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _depth(v: Any, d: int = 1) -> int:
    if isinstance(v, dict):
        return max((_depth(x, d + 1) for x in v.values()), default=d)
    if isinstance(v, list):
        return max((_depth(x, d + 1) for x in v), default=d)
    return d


# --- state --------------------------------------------------------------------------------


@dataclass
class PushedApp:
    name: str
    layer: list[tuple[int, int, int] | None]  # what `draw` painted; None = untouched
    lifetime_ms: int
    expiry: str  # "remove" or "mark"
    received_at: float
    seq: int


@dataclass
class Indicator:
    on: bool = False
    color: tuple[int, int, int] = (0, 0, 0)
    blink_ms: int = 0
    fade_ms: int = 0


def _indicator_pixels(width: int, height: int) -> dict[int, tuple[tuple[int, int], ...]]:
    """Indicator pixels: AWTRIX 3's shapes at the top-right corner, the middle of the right
    edge and the bottom-right corner. At 52x16: (51,0) (50,0) (51,1); (51,7) (51,8);
    (51,15) (51,14) (50,15).

    ASSUMPTION: indicator pixels on NG are not confirmed.
    """
    r, mid, b = width - 1, height // 2, height - 1
    return {
        1: ((r, 0), (r - 1, 0), (r, 1)),
        2: ((r, mid - 1), (r, mid)),
        3: ((r, b), (r, b - 1), (r - 1, b)),
    }


#: Settings keys the fake clock simulates.
SIMULATED_SETTINGS = frozenset({"enlargeApps"})

#: The TC002's built-in apps, in their default order, and whether each has settings (`config`
#: in ``GET /api/v1/apps``). Both are in the rotation on a fresh install (pushed-apps.md, "Built-in
#: apps"; seen on Kyle's TC002 on 2026-11-17).
BUILTIN_APPS: dict[str, bool] = {"Time": True, "Status": False}

#: Built-in apps of other boards. On a TC002 they are ignored in an order call (pushed-apps.md).
OTHER_BOARD_BUILTINS = frozenset({"Date", "Temperature", "Humidity", "Battery"})


@dataclass
class FakeClock:
    """One simulated clock of ``width`` x ``height`` LEDs.

    ``enlarge_apps`` is the TC002's ``enlargeApps`` setting, on by default as on a freshly
    installed clock: pushed apps are drawn at double size on a (width/2) x (height/2) grid
    (docs/reference/awtrix-ng.md, "Double size on the TC002").
    """

    width: int
    height: int
    time_fn: Callable[[], float] = time.monotonic
    enlarge_apps: bool = True
    apps: dict[str, PushedApp] = field(default_factory=dict)
    indicators: dict[int, Indicator] = field(
        default_factory=lambda: {1: Indicator(), 2: Indicator(), 3: Indicator()}
    )
    #: The saved rotation: ``PUT /api/v1/apps/order``'s two lists of names. A real clock keeps
    #: both across a restart; pushed apps (``apps``) it does not.
    order: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    push_count: int = 0
    last_push_at: float | None = None
    _seq: int = 0

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def body_limit(self) -> int:
        return BODY_LIMIT

    # --- pushed apps -------------------------------------------------------------------

    @staticmethod
    def check_name(name: str) -> None:
        if not NAME_RE.match(name or ""):
            raise ClockError(400, "invalidName", "name must match [A-Za-z0-9_-]{1,32}", "name")

    def _parse_body(self, raw: bytes, content_type: str | None) -> Any:
        if content_type and content_type.split(";")[0].strip().lower() != "application/json":
            raise ClockError(415, "unsupportedMediaType", "Content-Type must be application/json")
        if len(raw) > self.body_limit:
            raise ClockError(
                413, "payloadTooLarge",
                f"body is {len(raw)} bytes; this clock takes at most {self.body_limit}",
            )
        if not raw.strip():
            raise validation("a JSON body is required; use DELETE to remove", "body")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ClockError(400, "invalidJson", f"body is not valid JSON: {exc}") from exc
        if _depth(body) > MAX_NESTING:
            raise ClockError(400, "invalidJson", f"JSON nesting deeper than {MAX_NESTING}")
        return body

    def push(self, name: str, raw: bytes, content_type: str | None = "application/json") -> None:
        """``PUT /api/v1/apps/pushed/{name}``. Validates everything before changing anything."""
        self.check_name(name)
        body = self._parse_body(raw, content_type)
        if isinstance(body, list):
            raise not_simulated("An array payload (indexed apps {name}0..n)", "body")
        if not isinstance(body, dict):
            raise validation("body must be a JSON object", "body")
        if not body:
            raise validation("a JSON body is required: {} is rejected; use DELETE", "body")

        for key in body:
            if key in SIMULATED_KEYS:
                continue
            if key in NOT_SIMULATED_KEYS:
                raise not_simulated(f"The pushed-app key '{key}'", key)
            raise validation(f"unknown key '{key}'", key)

        layer: list[tuple[int, int, int] | None] = [None] * (self.width * self.height)
        draw = body.get("draw", [])
        if not isinstance(draw, list):
            raise validation("draw must be an array of drawing commands", "draw")
        for i, cmd in enumerate(draw):
            self._draw(layer, cmd, f"draw[{i}]")

        # Number keys given another type are ignored and keep their default (payload.md,
        # "Errors"), they are not refused.
        lifetime = body.get("lifetimeMs", 0)
        lifetime_ms = int(lifetime) if _is_number(lifetime) else 0
        expiry = body.get("lifetimeExpiry", "remove")
        if not isinstance(expiry, str) or expiry.lower() not in ("remove", "mark"):
            raise validation("lifetimeExpiry must be 'remove' or 'mark'", "lifetimeExpiry")

        # All valid: apply. Replacing the app resets its lifetime and so clears the mark.
        # ASSUMPTION: that re-pushing clears the mark is inferred from NG's code structure,
        # not confirmed (docs/reference/awtrix-ng.md, "When expiry is checked").
        now = self.time_fn()
        self._seq += 1
        self.apps[name] = PushedApp(name, layer, lifetime_ms, expiry.lower(), now, self._seq)
        self.push_count += 1
        self.last_push_at = now

    def _draw(self, layer: list, cmd: Any, fld: str) -> None:
        if not isinstance(cmd, list) or not cmd or not isinstance(cmd[0], str):
            raise validation("a draw command must be an array starting with its name", fld)
        op = cmd[0]
        if op in NOT_SIMULATED_DRAW:
            raise not_simulated(f"The draw command '{op}'", fld)
        if op != "bitmap":
            raise validation(f"unknown draw command '{op}'", fld)
        if len(cmd) != 6:
            raise validation("bitmap takes x, y, w, h, data", fld)
        x, y, w, h, data = cmd[1:]
        if not all(_is_number(v) for v in (x, y, w, h)):
            raise validation("bitmap x, y, w and h must be numbers", fld)
        x, y, w, h = int(x), int(y), int(w), int(h)

        colors: list[tuple[int, int, int] | None]
        if isinstance(data, str):
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise validation(f"bitmap data is not valid base64: {exc}", fld) from exc
            # ASSUMPTION (stricter than NG): payload.md says the base64 form is w*h*3 bytes
            # but does not say what a wrong length does. We refuse it so a sizing bug in the
            # feeder shows up here instead of as a half-drawn frame on the real clock.
            if w > 0 and h > 0 and len(raw) != w * h * 3:
                raise validation(
                    f"bitmap data is {len(raw)} bytes; {w}x{h} RGB888 needs {w * h * 3}", fld
                )
            colors = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw) - 2, 3)]
        elif isinstance(data, list):
            colors = []
            for c in data:
                rgb = parse_color(c)
                if rgb is None:
                    raise validation(f"bitmap colour {c!r} cannot be read", fld)
                colors.append(rgb)
        else:
            raise validation("bitmap data must be base64 RGB888 or an array of colours", fld)

        if w <= 0 or h <= 0:
            return  # draws nothing, not an error
        # Too few colours leave the rest empty; extra colours are ignored; clipped to display.
        for j in range(min(len(colors), w * h)):
            px, py = x + j % w, y + j // w
            if 0 <= px < self.width and 0 <= py < self.height:
                layer[py * self.width + px] = colors[j]

    def delete_app(self, name: str) -> None:
        """``DELETE /api/v1/apps/{name}``: idempotent; an unknown name is fine."""
        self.check_name(name)
        self.apps.pop(name, None)

    def _expire(self, now: float) -> None:
        for name, app in list(self.apps.items()):
            if self._expired(app, now) and app.expiry == "remove":
                del self.apps[name]

    @staticmethod
    def _expired(app: PushedApp, now: float) -> bool:
        return app.lifetime_ms > 0 and (now - app.received_at) * 1000 >= app.lifetime_ms

    def current_app(self, now: float | None = None) -> PushedApp | None:
        """The app on screen: the most recently pushed one that is not switched off.

        ASSUMPTION: the rotation is not simulated. Built-in apps are never drawn and do not take
        turns, so a clock with Time and Status still in the rotation shows our app all the time
        here, where a real one shows it a third of the time.
        """
        now = self.time_fn() if now is None else now
        self._expire(now)
        shown = [a for a in self.apps.values() if a.name not in self.disabled]
        if not shown:
            return None
        return max(shown, key=lambda a: a.seq)

    # --- the rotation ------------------------------------------------------------------

    def apps_list(self) -> list[dict[str, Any]]:
        """The body of ``GET /api/v1/apps``: the arranged apps in order, then the rest.

        Follows openapi.yaml and http.md at v1.2.3: ``enabled`` is "not named in ``disabled``",
        ``inLoop`` is enabled and present, ``slot`` is the place in ``order`` (null when it has
        none), ``present`` is false for a name that only holds a place, and ``origin`` is null
        then. Apps not arranged come after the arranged ones: built-ins, then pushed apps in
        the order they first arrived, then names that are only switched off.
        ASSUMPTION: a name listed twice in ``order`` is listed once, with its first slot.
        """
        names: list[str] = []
        for name in [*self.order, *BUILTIN_APPS, *self.apps, *self.disabled]:
            if name not in names:
                names.append(name)
        out = []
        for name in names:
            builtin = name in BUILTIN_APPS
            present = builtin or name in self.apps
            enabled = name not in self.disabled
            entry: dict[str, Any] = {
                "name": name,
                "enabled": enabled,
                "inLoop": enabled and present,
                "slot": self.order.index(name) if name in self.order else None,
                "present": present,
                "origin": "builtin" if builtin else "pushed" if present else None,
            }
            if builtin:
                entry["config"] = BUILTIN_APPS[name]
            out.append(entry)
        return out

    def in_rotation(self) -> list[str]:
        """Names of the apps that take turns on the display (``inLoop``), in order."""
        return [a["name"] for a in self.apps_list() if a["inLoop"]]

    def set_order(self, raw: bytes, content_type: str | None = "application/json") -> None:
        """``PUT /api/v1/apps/order`` with ``{"order"?: [names], "disabled": [names]}``.

        From openapi.yaml and http.md at v1.2.3: ``disabled`` is required and is the complete
        list of switched-off apps; ``order`` is optional and kept when left out; a body that is
        not JSON, not an object or has no ``disabled`` is ``400 invalidJson``. Names of apps
        that are not there are fine (they hold a place). Duplicates in ``order`` count. On a
        TC002, Date, Temperature, Humidity and Battery are ignored.
        ASSUMPTION (stricter than NG, which does not say): an empty body is 400 like other
        non-JSON; a list item that is not a string is 400 ``invalidJson``; a name outside
        ``[A-Za-z0-9_-]{1,32}`` is 400 ``invalidName``; another key, or a name in both lists,
        is 422 ``validationFailed``. Validates everything before changing anything.
        """
        if not raw.strip():
            raise ClockError(400, "invalidJson", "a JSON object with 'disabled' is required")
        body = self._parse_body(raw, content_type)
        if not isinstance(body, dict):
            raise ClockError(400, "invalidJson", "body must be a JSON object")
        if "disabled" not in body:
            raise ClockError(400, "invalidJson", "'disabled' is required")
        for key in body:
            if key not in ("order", "disabled"):
                raise validation(f"unknown key '{key}'", key)
        lists: dict[str, list[str]] = {}
        for key in ("order", "disabled"):
            if key not in body:
                continue
            value = body[key]
            if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
                raise ClockError(400, "invalidJson", f"'{key}' must be an array of app names")
            for n in value:
                if not NAME_RE.match(n):
                    raise ClockError(400, "invalidName",
                                     "name must match [A-Za-z0-9_-]{1,32}", key)
            lists[key] = [n for n in value if n not in OTHER_BOARD_BUILTINS]
        both = set(lists.get("order", ())) & set(lists["disabled"])
        if both:
            raise validation(f"'{min(both)}' is in both 'order' and 'disabled'", "disabled")
        if "order" in lists:
            self.order = lists["order"]
        self.disabled = list(dict.fromkeys(lists["disabled"]))

    # --- settings ----------------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        """The body of ``GET /api/v1/settings``: only the keys the fake clock simulates."""
        return {"enlargeApps": self.enlarge_apps}

    def patch_settings(
        self, raw: bytes, content_type: str | None = "application/json"
    ) -> dict[str, Any]:
        """``PATCH /api/v1/settings``: validates every key before applying any (all-or-nothing)
        and answers with all settings, already updated."""
        body = self._parse_body(raw, content_type)
        if not isinstance(body, dict):
            raise validation("body must be a JSON object", "body")
        for key, value in body.items():
            if key not in SIMULATED_SETTINGS:
                # Real NG accepts its other settings keys and answers "unknown field" for keys
                # it does not have; we cannot tell the two apart, so refuse them all loudly.
                raise ClockError(
                    422, "notSimulated",
                    f"The settings key '{key}' is NOT SIMULATED by the ProjectArthur fake "
                    "clock, which simulates only 'enlargeApps'.", key,
                )
            if not isinstance(value, bool):
                raise validation("enlargeApps must be true or false", key)
        # ASSUMPTION: an empty object is a valid "subset" that changes nothing.
        if "enlargeApps" in body:
            self.enlarge_apps = body["enlargeApps"]
        return self.settings()

    # --- indicators --------------------------------------------------------------------

    @staticmethod
    def _indicator_id(ident: str | int) -> int:
        try:
            n = int(ident)
        except (TypeError, ValueError):
            n = 0
        if n not in (1, 2, 3):
            raise ClockError(404, "notFound", "id must be 1..3")
        return n

    def set_indicator(
        self, ident: str | int, raw: bytes, content_type: str | None = "application/json"
    ) -> None:
        """``PUT /api/v1/indicators/{id}`` with ``{color, blinkMs, fadeMs}``."""
        n = self._indicator_id(ident)
        body = self._parse_body(raw, content_type)
        if not isinstance(body, dict) or not body:
            raise validation("a JSON object body is required; use DELETE to turn it off", "body")
        for key in body:
            if key not in ("color", "blinkMs", "fadeMs"):
                # ASSUMPTION: unknown indicator keys are refused like unknown app keys.
                raise validation(f"unknown key '{key}'", key)
        color: tuple[int, int, int] | None = None
        turn_off = False
        if "color" in body:
            c = body["color"]
            if c is None or c == 0:
                turn_off = True
            else:
                color = parse_color(c)
                if color is None:
                    raise validation(f"colour {c!r} cannot be read", "color")
                if color == (0, 0, 0):
                    turn_off = True
                    color = None

        def ms(key: str) -> int:
            v = body.get(key, 0)
            # Out of range (or, ASSUMPTION, not an integer) is stored as 0.
            return int(v) if _is_number(v) and 0 <= v <= 65535 else 0

        ind = self.indicators[n]
        ind.blink_ms = ms("blinkMs")
        ind.fade_ms = ms("fadeMs")
        if turn_off:
            ind.on = False  # keeps the stored colour
        elif color is not None:
            ind.color = color
            ind.on = True
        # A body without `color` leaves on/off untouched.

    def clear_indicator(self, ident: str | int) -> None:
        self.indicators[self._indicator_id(ident)].on = False

    def _indicator_rgb(self, ind: Indicator, now: float) -> tuple[int, int, int] | None:
        if not ind.on:
            return None
        ms = now * 1000
        if ind.blink_ms > 0 and (ms % ind.blink_ms) >= ind.blink_ms / 2:
            return None  # 50% on/off blink
        if ind.fade_ms > 0:
            # ASSUMPTION: a cosine "breathing" fade over one fadeMs period.
            k = (1 - math.cos(2 * math.pi * (ms % ind.fade_ms) / ind.fade_ms)) / 2
            return tuple(round(c * k) for c in ind.color)  # type: ignore[return-value]
        return ind.color

    # --- the screen ---------------------------------------------------------------------

    def screen(self, now: float | None = None) -> Frame:
        """The composed display at ``now``: app bitmap → stale mark → indicators.

        ASSUMPTION: with no app the screen is black. What a real NG clock shows with nothing
        pushed (its built-in apps, a clock face, ...) is unknown and not simulated.
        ASSUMPTION: indicators are drawn on top of everything, including the mark ("drawn
        above apps" is not stated in the spec).

        With ``enlarge_apps`` on, the app's pixels at x < width/2, y < height/2 are each drawn
        as a 2x2 block and the rest of the app shows nothing. It is applied here, at render
        time, so flipping the setting changes the screen at once.
        ASSUMPTION: the stale border and the indicators stay at native 1-px size in both modes
        (the docs do not say whether they are doubled).
        """
        now = self.time_fn() if now is None else now
        f = Frame(self.width, self.height)
        app = self.current_app(now)
        if app is not None:
            layer = app.layer
            if self.enlarge_apps:
                w = self.width
                layer = [layer[(y // 2) * w + x // 2]
                         for y in range(self.height) for x in range(self.width)]
            f.pixels = [c if c is not None else (0, 0, 0) for c in layer]
            if self._expired(app, now) and app.expiry == "mark":
                f.rect(0, 0, self.width, self.height, STALE_RGB, filled=False)
        pixels = _indicator_pixels(self.width, self.height)
        for n, ind in self.indicators.items():
            rgb = self._indicator_rgb(ind, now)
            if rgb is not None:
                for x, y in pixels[n]:
                    f.pixel(x, y, rgb)
        return f

    def screen_json(self, now: float | None = None) -> dict[str, Any]:
        """The body of ``GET /api/v1/display/screen``."""
        return {"width": self.width, "height": self.height,
                "pixels": self.screen(now).to_ints()}

    def status(self, now: float | None = None) -> dict[str, Any]:
        """State for the studio: what is showing, lifetime left, whether the mark is on."""
        now = self.time_fn() if now is None else now
        app = self.current_app(now)
        out: dict[str, Any] = {
            "width": self.width,
            "height": self.height,
            "app": None,
            "apps": sorted(self.apps),
            "push_count": self.push_count,
            "last_push_age_s": None if self.last_push_at is None else now - self.last_push_at,
            "lifetime_ms": None,
            "lifetime_remaining_s": None,
            "expiry": None,
            "marked": False,
            "enlarge_apps": self.enlarge_apps,
            "rotation": self.in_rotation(),
            "disabled": list(self.disabled),
            "indicators": {
                str(n): {"on": i.on, "color": "#{:02X}{:02X}{:02X}".format(*i.color),
                         "blinkMs": i.blink_ms, "fadeMs": i.fade_ms}
                for n, i in self.indicators.items()
            },
        }
        if app is not None:
            age = now - app.received_at
            out.update(
                app=app.name,
                lifetime_ms=app.lifetime_ms,
                expiry=app.expiry,
                app_age_s=age,
                lifetime_remaining_s=(
                    None if app.lifetime_ms <= 0 else max(0.0, app.lifetime_ms / 1000 - age)
                ),
                marked=self._expired(app, now) and app.expiry == "mark",
            )
        return out
