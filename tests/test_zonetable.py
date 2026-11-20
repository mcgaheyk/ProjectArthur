"""A TableZone gives the same local time as zoneinfo (D-0029).

On the clock there is no ``zoneinfo``: the config carries a table of the zone's UTC-offset
changes and :class:`arthur.zonetable.TableZone` reads it. Here, on CPython, it is checked
against ``zoneinfo`` itself at every change in the table, a second either side, and at random
instants, both ways: the local wall clock of a UTC instant, and the UTC instant of a local
hour (as the schedule builds the quiet hours), including the hours a change repeats or skips.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from arthur.zonetable import TableZone, from_table, zone_table

START = datetime(2024, 11, 20, tzinfo=UTC)
END = datetime(2046, 11, 20, tzinfo=UTC)

ZONES = (
    "America/New_York",  # the config's
    "UTC",
    "Europe/London",
    "Australia/Lord_Howe",  # a half-hour change
    "Asia/Kolkata",  # no changes
    "America/Sao_Paulo",  # changes abolished
    "Pacific/Auckland",  # southern hemisphere
    "America/St_Johns",  # a half-hour offset
)


@pytest.fixture(scope="module", params=ZONES)
def zones(request):
    table = zone_table(request.param, START, END)
    return table, from_table(table), ZoneInfo(request.param)


def _wall(dt: datetime) -> tuple[int, ...]:
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond, dt.fold)


def _instants(table: dict) -> list[datetime]:
    out = []
    for t, _ in table["changes"]:
        for d in (-3601, -1, 0, 1, 1799, 3599, 3600, 3601):
            out.append(datetime.fromtimestamp(t + d, UTC))
    rng = random.Random(table["key"])
    for _ in range(2000):
        s = rng.randrange(table["from"], table["until"])
        out.append(datetime.fromtimestamp(s, UTC) + timedelta(microseconds=rng.randrange(10**6)))
    return out


def test_the_table_has_changes_where_zoneinfo_does(zones):
    table, _, _ = zones
    if table["key"] in ("UTC", "Asia/Kolkata", "America/Sao_Paulo"):
        assert table["changes"] == []
    else:
        assert len(table["changes"]) == 2 * 22  # two a year


def test_local_time_of_a_utc_instant_matches_zoneinfo(zones):
    table, tz, zone = zones
    for now in _instants(table):
        assert _wall(now.astimezone(tz)) == _wall(now.astimezone(zone)), now


def test_utc_instant_of_a_local_time_matches_zoneinfo(zones):
    """Local times round every change, both folds: the repeated hour and the skipped one
    are read as zoneinfo reads them. The schedule's ``_at_local_hour`` uses fold 0."""
    table, tz, zone = zones
    for now in _instants(table):
        local = now.astimezone(zone).replace(tzinfo=None)
        for wall in (local, local.replace(minute=0, second=0, microsecond=0)):
            for fold in (0, 1):
                a = wall.replace(tzinfo=tz, fold=fold).astimezone(UTC)
                b = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                assert a == b, (wall, fold)


def test_every_local_hour_round_the_changes(zones):
    """Each whole local hour for a day either side of each change, as the quiet hours are
    built: ``datetime(y, m, d, h, tzinfo=tz).astimezone(UTC)``."""
    table, tz, zone = zones
    for t, _ in table["changes"]:
        day = datetime.fromtimestamp(t, UTC).astimezone(zone).date()
        for dd in (-1, 0, 1):
            d = day + timedelta(days=dd)
            for h in range(24):
                a = datetime(d.year, d.month, d.day, h, tzinfo=tz).astimezone(UTC)
                b = datetime(d.year, d.month, d.day, h, tzinfo=zone).astimezone(UTC)
                assert a == b, (d, h)


def test_outside_the_table_the_nearest_offset_holds():
    tz = TableZone("X", -18000, [[100, -14400], [200, -18000]])
    assert tz.offset_at_utc(-10**9) == -18000
    assert tz.offset_at_utc(150) == -14400
    assert tz.offset_at_utc(10**12) == -18000


def test_the_schedule_is_the_same_with_a_table_zone():
    """The quiet hours, waking and the local date, from a TableZone and from ZoneInfo, every
    ten minutes for two days round each change of 2026 and 2027 and a day in summer."""
    import dataclasses

    from arthur import schedule
    from arthur.config import default_config

    cfg = default_config()
    zone = cfg.tz
    table_cfg = dataclasses.replace(cfg, tz=from_table(zone_table(zone.key, START, END)))
    days = [datetime(2026, 3, 7, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC),
            datetime(2027, 3, 13, tzinfo=UTC), datetime(2027, 11, 6, tzinfo=UTC),
            datetime(2026, 7, 4, tzinfo=UTC)]
    for d0 in days:
        for k in range(2 * 24 * 6):
            now = d0 + timedelta(minutes=10 * k)
            since = now - timedelta(hours=9)
            for fn in (schedule.is_quiet, schedule.quiet_window, schedule.last_quiet_end):
                assert fn(now, table_cfg) == fn(now, cfg), (fn.__name__, now)
            for fn in (schedule.asleep_since, schedule.woke_at):
                assert fn(since, now, table_cfg) == fn(since, now, cfg), (fn.__name__, now)
            assert table_cfg.local(now).date() == cfg.local(now).date()
