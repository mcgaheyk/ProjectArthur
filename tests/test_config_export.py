"""The config exported as JSON for the clock builds the same Config as the TOML (D-0029)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from arthur.config import (
    DEFAULT_PATH,
    SOURCE_KEY,
    ZONE_KEY,
    ConfigError,
    config_from_json,
    load_config,
    load_config_json,
)
from arthur.tools import export_config
from arthur.zonetable import from_table


def _round_trip(data):
    return json.loads(json.dumps(data))


def test_toml_and_exported_json_give_the_same_config():
    assert config_from_json(_round_trip(export_config.export())) == load_config()


def test_a_changed_toml_still_matches(tmp_path):
    text = DEFAULT_PATH.read_text()
    p = tmp_path / "display.toml"
    p.write_text(text.replace("page_pct = 30", "page_pct = 33.3")
                 .replace('timezone = "America/New_York"', 'timezone = "Europe/London"'))
    cfg = config_from_json(_round_trip(export_config.export(p)))
    assert cfg == load_config(p)
    assert cfg.idle.page_pct == 33.3
    assert cfg.tz.key == "Europe/London"


def test_the_zone_table_reaches_two_years_back_and_twenty_ahead():
    now = datetime(2026, 11, 20, tzinfo=UTC)
    table = export_config.export(now=now)[ZONE_KEY]
    assert table["key"] == "America/New_York"
    assert table["from"] == datetime(2024, 1, 1, tzinfo=UTC).timestamp()
    assert table["until"] == datetime(2047, 1, 1, tzinfo=UTC).timestamp()
    assert len(table["changes"]) == 2 * 23
    zone = from_table(table)
    assert zone.offset_at_utc(datetime(2041, 7, 1, tzinfo=UTC).timestamp()) == -4 * 3600


def test_the_cli_writes_the_json(tmp_path, capsys):
    out = tmp_path / "display.json"
    assert export_config.main([str(out)]) == 0
    assert "America/New_York" in capsys.readouterr().out
    cfg = load_config_json(str(out))
    assert cfg == load_config()
    data = json.loads(out.read_text())
    assert data[SOURCE_KEY] == str(DEFAULT_PATH)


def test_reserved_keys_are_refused(tmp_path):
    p = tmp_path / "display.toml"
    p.write_text(DEFAULT_PATH.read_text() + '\n[_zone]\nkey = "x"\n')
    with pytest.raises(ValueError, match="reserved"):
        export_config.export(p)


def test_a_bad_toml_is_refused(tmp_path):
    p = tmp_path / "display.toml"
    p.write_text(DEFAULT_PATH.read_text().replace("round_s = 180", "round_s = 0"))
    with pytest.raises(ConfigError):
        export_config.export(p)


def test_toml_dates_are_refused(tmp_path):
    p = tmp_path / "display.toml"
    p.write_text(DEFAULT_PATH.read_text() + "\n[extra]\nwhen = 2026-11-20\n")
    with pytest.raises(TypeError, match="cannot be exported"):
        export_config.export(p)
