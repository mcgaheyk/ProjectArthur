import dataclasses
import tomllib
from datetime import UTC, datetime

import pytest

from arthur.config import (
    DEFAULT_PATH,
    REQUIRED_ROLES,
    ConfigError,
    Idle,
    WeatherConfig,
    config_from_dict,
    default_config,
    load_config,
    parse_hex,
    parse_size,
)


def test_repo_config_loads_with_documented_defaults():
    cfg = load_config()
    assert cfg.size == (52, 16)
    t = cfg.timings
    assert (t.heartbeat_s, t.ttl_s, t.just_cleaned_s) == (30, 90, 5)
    assert (t.clean_cycle_estimate_s, t.no_data_after_s) == (130, 600)
    assert (t.cat_left_fallback_s, t.visit_min_s, t.poll_s) == (120, 30, 300)
    assert cfg.thresholds.drawer_warn_pct == 80
    assert cfg.thresholds.litter_empty_pct == 60
    for role in REQUIRED_ROLES:
        assert len(cfg.rgb(role)) == 3
    assert cfg.rgb("stale") == (0x6E, 0x07, 0x00)
    assert cfg.rgb("off") == (0, 0, 0)


def test_config_is_frozen():
    cfg = default_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.size = (52, 16)  # type: ignore[misc]
    with pytest.raises(TypeError):
        cfg.palette.colors["ok"] = (1, 2, 3)  # type: ignore[index]


def test_palette_chars_unique_and_icon_reserved_chars_unused():
    cfg = default_config()
    chars = list(cfg.palette.chars.values())
    assert len(chars) == len(set(chars))
    assert "*" not in chars and "%" not in chars
    assert cfg.palette.role_for_char("G") == "ok"


def _data():
    with DEFAULT_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_size_defaults_to_52x16():
    d = _data()
    del d["display"]["size"]
    assert config_from_dict(d).size == (52, 16)


def test_32x8_config_rejected():
    # The TC001's 32x8 size was dropped when the TC002 (52x16) was bought.
    d = _data()
    d["display"]["size"] = "32x8"
    with pytest.raises(ConfigError, match="not supported; use one of 52x16"):
        config_from_dict(d)


@pytest.mark.parametrize("bad", ["64x32", "big", "32"])
def test_bad_size_rejected(bad):
    with pytest.raises(ConfigError):
        parse_size(bad)


def test_missing_role_rejected():
    d = _data()
    del d["palette"]["alert"]
    with pytest.raises(ConfigError, match="alert"):
        config_from_dict(d)


def test_unknown_timing_rejected():
    d = _data()
    d["timings"]["heartbeat_ms"] = 3
    with pytest.raises(ConfigError, match="heartbeat_ms"):
        config_from_dict(d)


def test_bad_colour_rejected():
    with pytest.raises(ConfigError):
        parse_hex("#12345")
    assert parse_hex("#00FF80") == (0, 255, 128)


def test_load_from_path(tmp_path):
    p = tmp_path / "d.toml"
    p.write_text(DEFAULT_PATH.read_text().replace("just_cleaned_s = 5 ", "just_cleaned_s = 12"))
    assert load_config(p).timings.just_cleaned_s == 12


# --- [display] timezone, [idle] and [weather] (D-0019) ---


def test_repo_config_has_boston_idle_and_weather():
    cfg = load_config()
    assert cfg.tz.key == "America/New_York"
    assert (cfg.idle.round_s, cfg.idle.page_pct, cfg.idle.scene_pct) == (180, 30, 20)
    assert (cfg.idle.settle_s, cfg.idle.quiet_from_h, cfg.idle.quiet_until_h) == (45, 22, 7)
    assert cfg.weather == WeatherConfig(latitude=42.36, longitude=-71.06)
    assert (cfg.weather.refresh_s, cfg.weather.max_age_s) == (900, 2700)


def test_absent_tables_give_defaults():
    d = _data()
    del d["idle"], d["weather"], d["display"]["timezone"]
    cfg = config_from_dict(d)
    assert cfg.tz.key == "UTC"
    assert cfg.idle == Idle()
    assert (cfg.idle.round_s, cfg.idle.page_pct, cfg.idle.scene_pct) == (180, 0, 0)
    assert (cfg.idle.settle_s, cfg.idle.quiet_from_h, cfg.idle.quiet_until_h) == (45, 22, 7)
    assert cfg.weather is None


def test_weather_defaults_when_only_coordinates_given():
    d = _data()
    d["weather"] = {"latitude": 0, "longitude": 0}
    w = config_from_dict(d).weather
    assert (w.refresh_s, w.max_age_s) == (900, 2700)
    assert w.units == "fahrenheit"


def test_weather_in_celsius():
    d = _data()
    d["weather"]["units"] = "celsius"
    assert config_from_dict(d).weather.units == "celsius"


def test_local_summer_and_winter():
    cfg = load_config()
    summer = cfg.local(datetime(2026, 7, 1, 16, 0, tzinfo=UTC))
    assert (summer.hour, summer.utcoffset().total_seconds()) == (12, -4 * 3600)  # EDT
    winter = cfg.local(datetime(2026, 1, 15, 16, 0, tzinfo=UTC))
    assert (winter.hour, winter.utcoffset().total_seconds()) == (11, -5 * 3600)  # EST


def test_unknown_timezone_rejected():
    d = _data()
    d["display"]["timezone"] = "America/Gotham"
    with pytest.raises(ConfigError, match="America/Gotham"):
        config_from_dict(d)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"round_s": 0}, "round_s must be above 0"),
        ({"page_pct": -5}, "page_pct must not be negative"),
        ({"scene_pct": 101}, "scene_pct must be 0 to 100"),
        ({"page_pct": 60, "scene_pct": 40}, "together must be at most 90"),
        ({"settle_s": -1}, "settle_s must not be negative"),
        ({"settle_s": "45"}, "settle_s must be a number"),
        ({"quiet_from_h": 24}, "quiet_from_h must be a whole hour"),
        ({"quiet_until_h": 6.5}, "quiet_until_h must be a whole hour"),
        ({"quiet_until_h": -1}, "quiet_until_h must not be negative"),
        ({"scenes_pct": 20}, "unknown key.*scenes_pct"),
    ],
)
def test_bad_idle_rejected(changes, match):
    d = _data()
    d["idle"].update(changes)
    with pytest.raises(ConfigError, match=match):
        config_from_dict(d)


def test_idle_shares_at_the_limits_accepted():
    d = _data()
    d["idle"].update(page_pct=30, scene_pct=60, quiet_from_h=7, quiet_until_h=7)
    idle = config_from_dict(d).idle
    assert (idle.page_pct, idle.scene_pct, idle.quiet_from_h) == (30, 60, 7)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"latitude": 90.5}, "latitude must be within"),
        ({"latitude": -91}, "latitude must be within"),
        ({"longitude": -180.1}, "longitude must be within"),
        ({"longitude": "-71.06"}, "longitude must be a number"),
        ({"refresh_s": 299}, "refresh_s must be at least 300"),
        ({"refresh_s": 1800, "max_age_s": 1799}, "max_age_s must be at least refresh_s"),
        ({"city": 1}, "unknown key.*city"),
        ({"units": "kelvin"}, "units must be"),
        ({"units": "Celsius"}, "units must be"),
        ({"units": ["celsius"]}, "units must be"),
        ({"units": 1}, "units must be"),
    ],
)
def test_bad_weather_rejected(changes, match):
    d = _data()
    d["weather"].update(changes)
    with pytest.raises(ConfigError, match=match):
        config_from_dict(d)


def test_weather_without_coordinates_rejected():
    d = _data()
    del d["weather"]["longitude"]
    with pytest.raises(ConfigError, match="missing longitude"):
        config_from_dict(d)
