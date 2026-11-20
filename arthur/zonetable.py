"""A time zone from a table of its UTC-offset changes, for MicroPython (D-0029).

MicroPython has no ``zoneinfo``. The shared code needs local time in two ways: the local
wall clock of a UTC instant (the quiet hours, the weather page's date) and the UTC instant of
a local hour (when the quiet hours begin and end). Both go through ``Config.tz``, a
``datetime.tzinfo``: on CPython a ``ZoneInfo``, as before; on the clock a :class:`TableZone`
built from the table the config exporter writes (:func:`zone_table`, CPython only, from
``zoneinfo``).

A :class:`TableZone` answers as ``ZoneInfo`` does, including the hour a clock change repeats
or skips: a local time is read with ``fold`` as zoneinfo reads it (``fold=0``, the default
the schedule uses, takes the offset in force before the change, both for the repeated hour
and for the skipped one), and converting from UTC sets ``fold=1`` on the second pass through
a repeated hour. ``tests/test_zonetable.py`` compares it with ``zoneinfo`` at every change in
the table, a second either side, and at random instants.

Outside the table's span the nearest offset in it is used: the one before its first change,
or after its last. The exporter covers two years back to twenty ahead.
"""

from datetime import datetime, timedelta, tzinfo

_ONE_S = timedelta(seconds=1)


def _bisect_right(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


class TableZone(tzinfo):
    """A zone given by ``first`` (the UTC offset in seconds before the first change) and
    ``changes`` (``[utc_seconds, offset_seconds_after], ...`` in time order)."""

    def __init__(self, key, first, changes):
        self.key = key
        utc = [int(c[0]) for c in changes]
        offsets = [int(first)] + [int(c[1]) for c in changes]
        self._utc = utc
        self._offsets = offsets
        # Local wall-clock second of each change, as zoneinfo reads a local time: with
        # fold=0 a change happens at the later of its two wall times (so the repeated and
        # the skipped hour both take the earlier offset), with fold=1 at the earlier.
        late, early = [], []
        for i, t in enumerate(utc):
            a, b = offsets[i], offsets[i + 1]
            late.append(t + max(a, b))
            early.append(t + min(a, b))
        self._wall = (late, early)

    def __repr__(self):
        return "TableZone(" + repr(self.key) + ")"

    def __eq__(self, other):
        return (isinstance(other, TableZone) and self.key == other.key
                and self._utc == other._utc and self._offsets == other._offsets)

    def __hash__(self):
        return hash(self.key)

    # --- lookups -------------------------------------------------------------------------

    def offset_at_utc(self, seconds):
        """The UTC offset, in seconds, at the UTC instant ``seconds`` after the epoch."""
        return self._offsets[_bisect_right(self._utc, seconds)]

    def offset_at_local(self, seconds, fold=0):
        """The UTC offset, in seconds, of a local wall-clock time ``seconds`` after the local
        epoch, read with ``fold`` as zoneinfo reads it."""
        return self._offsets[_bisect_right(self._wall[1 if fold else 0], seconds)]

    def _fold_at_utc(self, seconds):
        """1 when the UTC instant is the second pass through an hour a change repeats."""
        i = _bisect_right(self._utc, seconds)
        if i == 0:
            return 0
        back = self._offsets[i - 1] - self._offsets[i]
        return 1 if back > 0 and seconds < self._utc[i - 1] + back else 0

    # --- tzinfo --------------------------------------------------------------------------

    def utcoffset(self, dt):
        if dt is None:
            return None
        local = (dt.replace(tzinfo=None) - _EPOCH_NAIVE) // _ONE_S
        return timedelta(seconds=self.offset_at_local(local, dt.fold))

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return self.key

    def fromutc(self, dt):
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        utc = (dt.replace(tzinfo=None) - _EPOCH_NAIVE) // _ONE_S
        local = dt + timedelta(seconds=self.offset_at_utc(utc))
        if self._fold_at_utc(utc):
            local = local.replace(fold=1)
        return local


_EPOCH_NAIVE = datetime(1970, 1, 1)


def zone_table(key, start, end):
    """The UTC-offset changes of the zoneinfo zone ``key`` from ``start`` to ``end`` (aware
    datetimes), for :class:`TableZone`: ``{"key", "first", "changes", "from", "until"}``,
    times in whole seconds since the epoch. CPython only."""
    from datetime import UTC
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(key)

    def offset(t):
        return int(datetime.fromtimestamp(t, UTC).astimezone(zone).utcoffset().total_seconds())

    t0 = int(start.timestamp())
    t1 = int(end.timestamp())
    first = offset(t0)
    changes = []
    step = 6 * 3600
    t, cur = t0, first
    while t < t1:
        nxt = min(t + step, t1)
        o = offset(nxt)
        if o != cur:
            lo, hi = t, nxt  # offset(lo) == cur, offset(hi) != cur: find the first second
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if offset(mid) == cur:
                    lo = mid
                else:
                    hi = mid
            o = offset(hi)
            changes.append([hi, o])
            cur = o
            t = hi
            continue
        t = nxt
    return {"key": key, "first": first, "changes": changes, "from": t0, "until": t1}


def from_table(table):
    """A :class:`TableZone` from what :func:`zone_table` returned (or its JSON)."""
    return TableZone(table["key"], table["first"], table["changes"])
