"""Load config/display.toml into frozen dataclasses.

Everything that is a colour, threshold, timing or caption lives in that one file. Screens and icons
refer to palette *roles* (``"ok"``, ``"alert"``, ...), never to hex values.

Shared with MicroPython (D-0029), which has no TOML reader and no ``zoneinfo``: there the
config comes from the JSON that ``python -m arthur.tools.export_config`` writes from the same
TOML (:func:`config_from_json`), with the time zone as a table of its offset changes
(:mod:`arthur.zonetable`). On CPython ``tz`` is a ``ZoneInfo``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from arthur.compat import IS_MICROPYTHON, field, field_names, record

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import tzinfo
    from typing import Any

    RGB = tuple[int, int, int]

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "display.toml"

#: Roles every config must define. Extra roles are allowed.
REQUIRED_ROLES: tuple[str, ...] = (
    "off",
    "text",
    "dim",
    "ok",
    "attention",
    "alert",
    "fur",
    "stripe",
    "cream",
    "nose",
    "eye",
    "poop",
    "poop_hi",
    "gold",
    "sand",
    "soap",
    "soap_hi",
    "stink",
    "ghost",
    "stale",
    "drawer",
    "caution",
)

#: Frame sizes the renderer supports (width, height): the Ulanzi TC002's 52x16 only.
SIZES: tuple[tuple[int, int], ...] = ((52, 16),)


class ConfigError(ValueError):
    """The config file is missing a key or has a bad value."""


def parse_hex(value: str) -> RGB:
    """``"#RRGGBB"`` → ``(r, g, b)``."""
    s = value.strip()
    if s.startswith("#"):  # noqa: FURB188 (MicroPython has no str.removeprefix)
        s = s[1:]
    if len(s) != 6:
        raise ConfigError(f"colour must be #RRGGBB, got {value!r}")
    try:
        n = int(s, 16)
    except ValueError as exc:
        raise ConfigError(f"colour must be #RRGGBB, got {value!r}") from exc
    return ((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)


def parse_size(value: str) -> tuple[int, int]:
    """``"52x16"`` → ``(52, 16)``; must be one of :data:`SIZES`."""
    try:
        w, h = (int(p) for p in value.lower().split("x"))
    except ValueError as exc:
        raise ConfigError(f"size must look like 52x16, got {value!r}") from exc
    if (w, h) not in SIZES:
        raise ConfigError(f"size {value!r} not supported; use one of {format_sizes()}")
    return (w, h)


def format_size(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


def format_sizes() -> str:
    return ", ".join(format_size(s) for s in SIZES)


@record("colors chars")
class Palette:
    """Role name → RGB, plus the one-character code for each role."""

    colors: Mapping[str, RGB]
    chars: Mapping[str, str]

    def rgb(self, role: str) -> RGB:
        try:
            return self.colors[role]
        except KeyError:
            raise KeyError(f"unknown palette role {role!r}") from None

    def role_for_char(self, ch: str) -> str:
        for role, c in self.chars.items():
            if c == ch:
                return role
        raise KeyError(f"no palette role uses character {ch!r}")

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(self.colors)


@record("drawer_warn_pct litter_empty_pct")
class Thresholds:
    drawer_warn_pct: float = 80
    litter_empty_pct: float = 60


@record(
    "heartbeat_s ttl_s just_cleaned_s clean_cycle_estimate_s no_data_after_s"
    " cat_left_fallback_s visit_min_s poll_s in_use_joke_s in_use_cat_s interrupted_title_s"
    " interrupted_phrase_s interrupted_rest_s"
)
class Timings:
    heartbeat_s: float = 30
    ttl_s: float = 90
    just_cleaned_s: float = 5
    clean_cycle_estimate_s: float = 130
    no_data_after_s: float = 600
    cat_left_fallback_s: float = 120
    visit_min_s: float = 30
    poll_s: float = 300
    in_use_joke_s: float = 6
    in_use_cat_s: float = 4
    interrupted_title_s: float = 2
    interrupted_phrase_s: float = 3
    interrupted_rest_s: float = 2


@record("round_s page_pct scene_pct settle_s quiet_from_h quiet_until_h")
class Idle:
    """The idle show (D-0019): rounds of home page, information page and scene."""

    round_s: float = 180
    #: Share of each round for the information page and for a scene, in percent. 0 is off; the
    #: home page keeps the rest.
    page_pct: float = 0
    scene_pct: float = 0
    #: No cutaway starts until idle has lasted this long.
    settle_s: float = 45
    #: Quiet hours (the cat asleep) in the local zone, whole hours; equal values mean none.
    quiet_from_h: int = 22
    quiet_until_h: int = 7


@record("latitude longitude refresh_s max_age_s units")
class WeatherConfig:
    """Where the weather page's forecast is for, and how often it is fetched."""

    latitude: float
    longitude: float
    refresh_s: float = 900
    max_age_s: float = 2700
    units: str = "fahrenheit"  # or "celsius": what the page's temperature is in


#: Values of ``[weather] units``, with the symbol Open-Meteo answers for each.
WEATHER_UNITS: Mapping[str, str] = MappingProxyType({"fahrenheit": "\u00b0F",
                                                    "celsius": "\u00b0C"})


#: An empty caption table, for configs without [captions].
NO_CAPTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType({})


def utc_zone() -> tzinfo:
    """UTC as ``Config.tz`` holds a zone: ``ZoneInfo("UTC")``, or a table on MicroPython."""
    if IS_MICROPYTHON:
        from arthur.zonetable import TableZone

        return TableZone("UTC", 0, [])
    from zoneinfo import ZoneInfo

    return ZoneInfo("UTC")


@record("size palette thresholds timings source captions tz idle weather")
class Config:
    size: tuple[int, int]
    palette: Palette
    thresholds: Thresholds
    timings: Timings
    source: str = "<defaults>"
    #: Caption key → the jokes to pick from. Screens fall back to a plain word when a key is
    #: missing or none of its captions fits.
    captions: Mapping[str, tuple[str, ...]] = NO_CAPTIONS
    #: The local time zone (``[display] timezone``); UTC when the key is absent. A
    #: ``ZoneInfo`` on CPython, an ``arthur.zonetable.TableZone`` on MicroPython.
    tz: tzinfo = field(default_factory=utc_zone)
    idle: Idle = field(default_factory=Idle)
    #: ``None`` when there is no ``[weather]`` table.
    weather: WeatherConfig | None = None

    def rgb(self, role: str) -> RGB:
        return self.palette.rgb(role)

    def local(self, now: datetime) -> datetime:
        """The aware ``now`` as wall-clock time in :attr:`tz`."""
        return now.astimezone(self.tz)

    def at_local_hour(self, day: date, hour: int) -> datetime:
        """``hour``:00 on the local ``day``, as an aware UTC instant. Built in the local zone,
        so it is right across a clock change; an hour the change skips or repeats is read
        with the offset in force before it (zoneinfo's ``fold=0``)."""
        return datetime(day.year, day.month, day.day, hour, tzinfo=self.tz).astimezone(UTC)


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    sec = data.get(name, {})
    if not isinstance(sec, dict):
        raise ConfigError(f"[{name}] must be a table")
    return sec


def _numbers(sec: Mapping[str, Any], cls: type, name: str) -> Any:
    fields = field_names(cls)
    unknown = set(sec) - set(fields)
    if unknown:
        raise ConfigError(f"unknown key(s) in [{name}]: {', '.join(sorted(unknown))}")
    kwargs = {}
    for key, value in sec.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"[{name}] {key} must be a number, got {value!r}")
        if value < 0:
            raise ConfigError(f"[{name}] {key} must not be negative")
        kwargs[key] = value
    return cls(**kwargs)


#: The top-level key of the exported JSON that holds the time zone's table (MicroPython).
ZONE_KEY = "_zone"
#: The top-level key of the exported JSON that holds the TOML file's path.
SOURCE_KEY = "_source"


def _timezone(display: Mapping[str, Any], table: Mapping[str, Any] | None = None) -> tzinfo:
    name = display.get("timezone", "UTC")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"[display] timezone must be a zone name, got {name!r}")
    if IS_MICROPYTHON:
        from arthur.zonetable import from_table

        if table is None:
            if name.strip() == "UTC":
                return utc_zone()
            raise ConfigError(f"[display] timezone {name!r} needs the exported zone table")
        if table.get("key") != name.strip():
            raise ConfigError(f"the zone table is for {table.get('key')!r}, not {name!r}")
        return from_table(table)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"[display] timezone {name!r} is not a known time zone") from exc


def _idle(sec: Mapping[str, Any]) -> Idle:
    idle = _numbers(sec, Idle, "idle")
    if idle.round_s <= 0:
        raise ConfigError("[idle] round_s must be above 0")
    for key in ("page_pct", "scene_pct"):
        if getattr(idle, key) > 100:
            raise ConfigError(f"[idle] {key} must be 0 to 100")
    if idle.page_pct + idle.scene_pct > 90:
        raise ConfigError("[idle] page_pct and scene_pct together must be at most 90")
    for key in ("quiet_from_h", "quiet_until_h"):
        h = getattr(idle, key)
        if not isinstance(h, int) or h > 23:
            raise ConfigError(f"[idle] {key} must be a whole hour, 0 to 23")
    return idle


def _weather(data: Mapping[str, Any]) -> WeatherConfig | None:
    if "weather" not in data:
        return None
    sec = _section(data, "weather")
    unknown = set(sec) - set(field_names(WeatherConfig))
    if unknown:
        raise ConfigError(f"unknown key(s) in [weather]: {', '.join(sorted(unknown))}")
    kwargs = {}
    for key, value in sec.items():
        if key == "units":
            if not isinstance(value, str) or value not in WEATHER_UNITS:
                raise ConfigError(f"[weather] units must be \"fahrenheit\" or \"celsius\", "
                                  f"got {value!r}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"[weather] {key} must be a number, got {value!r}")
        kwargs[key] = value
    for key in ("latitude", "longitude"):
        if key not in kwargs:
            raise ConfigError(f"[weather] is missing {key}")
    if not -90 <= kwargs["latitude"] <= 90:
        raise ConfigError("[weather] latitude must be within -90 to 90")
    if not -180 <= kwargs["longitude"] <= 180:
        raise ConfigError("[weather] longitude must be within -180 to 180")
    weather = WeatherConfig(**kwargs)
    if weather.refresh_s < 300:
        raise ConfigError("[weather] refresh_s must be at least 300")
    if weather.max_age_s < weather.refresh_s:
        raise ConfigError("[weather] max_age_s must be at least refresh_s")
    return weather


def _captions(sec: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for key, value in sec.items():
        if not isinstance(value, list) or not value:
            raise ConfigError(f"[captions] {key} must be a non-empty list of strings")
        items = []
        for v in value:
            if not isinstance(v, str) or not v.strip():
                raise ConfigError(f"[captions] {key} must contain only non-empty strings")
            items.append(v.strip().upper())
        out[key] = tuple(items)
    return MappingProxyType(out)


def config_from_dict(data: Mapping[str, Any], source: str = "<dict>") -> Config:
    """Build a :class:`Config` from already-parsed TOML data (or the exported JSON, whose
    zone table only MicroPython reads)."""
    display = _section(data, "display")
    size = parse_size(str(display.get("size", "52x16")))

    pal = _section(data, "palette")
    chars = _section(data, "palette_chars")
    missing = [r for r in REQUIRED_ROLES if r not in pal]
    if missing:
        raise ConfigError(f"[palette] is missing role(s): {', '.join(missing)}")
    colors = {role: parse_hex(str(v)) for role, v in pal.items()}
    missing_chars = [r for r in colors if r not in chars]
    if missing_chars:
        raise ConfigError(f"[palette_chars] is missing role(s): {', '.join(missing_chars)}")
    char_map = {}
    for role in colors:
        c = str(chars[role])
        if len(c) != 1 or c == "*":
            raise ConfigError(f"[palette_chars] {role} must be one character other than '*'")
        char_map[role] = c
    if len(set(char_map.values())) != len(char_map):
        raise ConfigError("[palette_chars] characters must be unique")

    return Config(
        size=size,
        palette=Palette(MappingProxyType(colors), MappingProxyType(char_map)),
        thresholds=_numbers(_section(data, "thresholds"), Thresholds, "thresholds"),
        timings=_numbers(_section(data, "timings"), Timings, "timings"),
        source=source,
        captions=_captions(_section(data, "captions")),
        tz=_timezone(display, data.get(ZONE_KEY)),
        idle=_idle(_section(data, "idle")),
        weather=_weather(data),
    )


def load_config(path: str | Path | None = None) -> Config:
    """Read ``config/display.toml`` (or ``path``) into a :class:`Config`. CPython only."""
    import tomllib

    p = Path(path) if path is not None else DEFAULT_PATH
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return config_from_dict(data, source=str(p))


def config_from_json(data: Mapping[str, Any]) -> Config:
    """The :class:`Config` from the exported JSON (``arthur.tools.export_config``), already
    parsed: the same as from the TOML it was exported from, on either Python."""
    return config_from_dict(data, source=str(data.get(SOURCE_KEY, "<json>")))


def load_config_json(path: str) -> Config:
    """Read the exported JSON at ``path`` into a :class:`Config` (both Pythons)."""
    import json

    with open(path) as fh:
        data = json.load(fh)
    return config_from_json(data)


@lru_cache(maxsize=1)
def default_config() -> Config:
    """The repository's ``config/display.toml``, loaded once."""
    return load_config()
