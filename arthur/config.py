"""Load config/display.toml into frozen dataclasses.

Everything that is a colour, threshold, timing or caption lives in that one file. Screens and icons
refer to palette *roles* (``"ok"``, ``"alert"``, ...), never to hex values.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
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
    "mint",
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
    s = s.removeprefix("#")
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Thresholds:
    drawer_warn_pct: float = 80


@dataclass(frozen=True)
class Timings:
    heartbeat_s: float = 30
    ttl_s: float = 90
    just_cleaned_s: float = 60
    clean_cycle_estimate_s: float = 150
    no_data_after_s: float = 600
    idle_blink_every_s: float = 30


#: An empty caption table, for configs without [captions].
NO_CAPTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType({})


@dataclass(frozen=True)
class Config:
    size: tuple[int, int]
    palette: Palette
    thresholds: Thresholds
    timings: Timings
    source: str = "<defaults>"
    #: Caption key → the jokes to pick from. Screens fall back to a plain word when a key is
    #: missing or none of its captions fits.
    captions: Mapping[str, tuple[str, ...]] = NO_CAPTIONS

    def rgb(self, role: str) -> RGB:
        return self.palette.rgb(role)


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    sec = data.get(name, {})
    if not isinstance(sec, dict):
        raise ConfigError(f"[{name}] must be a table")
    return sec


def _numbers(sec: Mapping[str, Any], cls: type, name: str) -> Any:
    fields = cls.__dataclass_fields__
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
    """Build a :class:`Config` from already-parsed TOML data."""
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
    )


def load_config(path: str | Path | None = None) -> Config:
    """Read ``config/display.toml`` (or ``path``) into a :class:`Config`."""
    p = Path(path) if path is not None else DEFAULT_PATH
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return config_from_dict(data, source=str(p))


@lru_cache(maxsize=1)
def default_config() -> Config:
    """The repository's ``config/display.toml``, loaded once."""
    return load_config()
