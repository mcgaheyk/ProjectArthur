"""Export ``config/display.toml`` as JSON for the clock (D-0029).

MicroPython has no TOML reader and no ``zoneinfo``, so the program on the clock reads this
JSON instead: the TOML's tables as they are, plus the time zone as a table of its UTC-offset
changes from two years back to twenty ahead (``_zone``) and the TOML's path (``_source``).
``arthur.config.config_from_json`` builds the same ``Config`` from it on either Python; the
TOML stays the one config file.

    uv run python -m arthur.tools.export_config OUT.json [--config PATH]

CPython only.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arthur.config import DEFAULT_PATH, SOURCE_KEY, ZONE_KEY, config_from_dict
from arthur.zonetable import zone_table

#: How far back and ahead the zone table reaches, in whole years from 1 January.
ZONE_YEARS_BACK = 2
ZONE_YEARS_AHEAD = 20


def _plain(value: Any, where: str) -> None:
    """Refuse anything JSON cannot carry exactly (TOML dates and times)."""
    if isinstance(value, dict):
        for k, v in value.items():
            _plain(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _plain(v, f"{where}[{i}]")
    elif not isinstance(value, (str, int, float, bool)):
        raise TypeError(f"{where}: a {type(value).__name__} cannot be exported to JSON")


def export(path: str | Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The JSON-ready config: the TOML at ``path`` (default the repository's), checked by
    building a ``Config`` from it, with the zone table and source added."""
    p = Path(path) if path is not None else DEFAULT_PATH
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    for key in (ZONE_KEY, SOURCE_KEY):
        if key in data:
            raise ValueError(f"{p}: the top-level key {key!r} is reserved for the export")
    _plain(data, p.name)
    cfg = config_from_dict(data, source=str(p))  # raises ConfigError if the file is bad
    now = now or datetime.now(UTC)
    start = datetime(now.year - ZONE_YEARS_BACK, 1, 1, tzinfo=UTC)
    end = datetime(now.year + ZONE_YEARS_AHEAD + 1, 1, 1, tzinfo=UTC)
    out = dict(data)
    out[SOURCE_KEY] = str(p)
    out[ZONE_KEY] = zone_table(cfg.tz.key, start, end)  # type: ignore[attr-defined]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export config/display.toml as JSON for the clock.")
    ap.add_argument("out", help="where to write the JSON")
    ap.add_argument("--config", help="the TOML to export (default config/display.toml)")
    opts = ap.parse_args(argv)
    data = export(opts.config)
    Path(opts.out).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    zone = data[ZONE_KEY]
    print(f"wrote {opts.out}: {zone['key']} with {len(zone['changes'])} offset changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
