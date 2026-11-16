import dataclasses
import tomllib

import pytest

from arthur.config import (
    DEFAULT_PATH,
    REQUIRED_ROLES,
    ConfigError,
    config_from_dict,
    default_config,
    load_config,
    parse_hex,
    parse_size,
)


def test_repo_config_loads_with_documented_defaults():
    cfg = load_config()
    assert cfg.size == (32, 8)
    t = cfg.timings
    assert (t.heartbeat_s, t.ttl_s, t.just_cleaned_s) == (30, 90, 60)
    assert (t.clean_cycle_estimate_s, t.no_data_after_s) == (150, 600)
    assert cfg.thresholds.drawer_warn_pct == 80
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


def test_size_override():
    d = _data()
    d["display"]["size"] = "52x16"
    assert config_from_dict(d).size == (52, 16)


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
    p.write_text(DEFAULT_PATH.read_text().replace("just_cleaned_s = 60", "just_cleaned_s = 5"))
    assert load_config(p).timings.just_cleaned_s == 5
