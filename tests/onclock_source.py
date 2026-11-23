"""A scripted robot source for the clock's program, for both Pythons (tests only).

:class:`ReplaySource` replays the real robot's state lines (``tests/data/real-cycles.jsonl``,
logged by the spike) through the shared tracker, shifted in time, and answers the feeder's
``Source`` protocol. ``view(now)`` depends only on ``now`` (asked in order), so CPython can
replay the same view for any moment the program under MicroPython drew, and check its frames.
Whisker is always in contact.
"""

import json
from datetime import UTC, datetime, timedelta

from arthur.model import FeederHealth, RobotSnapshot
from arthur.sources.tracker import Tracker
from arthur.wire import util

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ts_us(text):
    """An ISO timestamp as whole microseconds since 1970 (exact on both Pythons)."""
    y, mo, d, h, mi, s, frac, offset = util._split_iso(text.replace("Z", "+00:00"))
    secs = util.days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s - offset
    return secs * 1_000_000 + (int((frac + "000000")[:6]) if frac else 0)


def at(us):
    return EPOCH + timedelta(microseconds=us)


def load_lines(path):
    """``[(ts_us, record), ...]`` for the state lines, in order."""
    out = []
    with open(path) as fh:
        for text in fh:
            text = text.strip()
            if not text:
                continue
            d = json.loads(text)
            if d.get("kind") == "state" and isinstance(d.get("record"), dict):
                out.append((ts_us(d["ts"]), d["record"]))
    return out


def _num(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _bool(value, default):
    return value if isinstance(value, bool) else default


def _str(value, default):
    return value if isinstance(value, str) and value else default


def snapshot(record, observed):
    """``arthur.sources.logfile.snapshot_from_record``, without ``last_seen`` (never shown)."""
    wait = _num(record.get("clean_cycle_wait_time_minutes"), None)
    return RobotSnapshot(
        status=_str(record.get("status"), "UNKNOWN"),
        status_code=_str(record.get("status_code"), None),
        is_online=_bool(record.get("is_online"), True),
        is_on=_bool(record.get("is_on"), True),
        waste_drawer_level=_num(record.get("waste_drawer_level"), 0.0),
        litter_level=_num(record.get("litter_level"), 100.0),
        litter_level_state=_str(record.get("litter_level_state"), None),
        is_sleeping=_bool(record.get("is_sleeping"), False),
        is_laser_dirty=_bool(record.get("is_laser_dirty"), False),
        power_type=_str(record.get("power_type"), "AC"),
        wait_time_minutes=int(wait) if wait is not None else 7,
        last_seen=None,
        pet_weight_lbs=_num(record.get("pet_weight"), None),
        status_since=observed.status_since,
        previous_status=observed.previous_status,
        last_visit_at=observed.last_visit_at,
        visits_today=None,
        visit_started_at=observed.visit_started_at,
        cat_left_at=observed.cat_left_at,
        cycled_s=observed.cycled_s,
    )


class ReplaySource:
    """The lines (``load_lines``) moved by ``shift_us``, fed to a tracker as ``now`` passes
    them. ``on_event(name)`` hears the source start and stop."""

    def __init__(self, lines, shift_us, visit_min_s=30.0, on_event=None):
        self.lines = [(t + shift_us, r) for t, r in lines]
        self.tracker = Tracker(visit_min_s)
        self.i = 0
        self.record = None
        self.on_event = on_event or (lambda name: None)
        self.runs = 0
        self.running = 0

    def view(self, now):
        now_us = (now - EPOCH) // timedelta(microseconds=1)
        while self.i < len(self.lines) and self.lines[self.i][0] <= now_us:
            t, rec = self.lines[self.i]
            self.tracker.feed(rec, at(t))
            self.record = rec
            self.i += 1
        if self.record is None:
            return None, FeederHealth(last_contact_at=now)
        return snapshot(self.record, self.tracker.observed(now)), FeederHealth(
            last_contact_at=now)

    async def run(self, stop):
        self.runs += 1
        self.running += 1
        self.on_event("source started")
        try:
            await stop.wait()
        finally:
            self.running -= 1
            self.on_event("source stopped")

    def update_config(self, cfg):
        pass
