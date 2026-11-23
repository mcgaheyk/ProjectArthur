"""The live source on our own Whisker client, for the clock and for the Mac (D-0029).

:class:`WireSource` does what :class:`arthur.sources.whisker.WhiskerSource` does, on
:mod:`arthur.wire` instead of pylitterbot, and runs unchanged on CPython 3.13 and MicroPython
1.29. It implements the feeder's ``Source`` protocol (``view``, ``run``, ``update_config``):

- **The connection** is :class:`arthur.wire.monitor.Monitor`'s: connect (token, then robot),
  the live subscription, a poll every ``poll_s`` (never under 120 s), a heartbeat every 15
  minutes, the history at start, every six hours and when asked, never closer than 120 s,
  and the back-offs (a rejected login 600 s doubling to an hour; anything else 30 s doubling
  to 600 s; a failed poll waits at least 120 s). The error clears with the next contact.
- **Logins, as cautiously as today's source** (:func:`arthur.spikelog.connect_account`): the
  saved token is used while it is valid and renewed from its refresh token when it is not;
  the password is used only when there is no usable token, its renewal was refused, or
  Whisker refused it (then once, at once). A token refused while polling is dropped and the
  next connect logs in with the password. A refused password login waits out the slow
  back-off. Missing credentials: NO DATA (``auth``) until restarted, no connection.
- **Visits** (D-0017, D-0018, D-0025): every record goes to the shared
  :class:`~arthur.sources.tracker.Tracker`, dated when it was seen. A contact more than
  ``no_data_after_s`` after the one before it is a gap in watching: the tracker forgets what
  only watching can tell and the history is read. So is a rise in the robot's cycle count
  that no watched visit explains. Every history read seeds the last visit (later, never
  earlier) and, mid-visit, its start and when the cat left. A visit with no weight
  ``cat_left_fallback_s`` after it began asks the history once for its weight.
- **The log**: the spike's lines (:mod:`arthur.sources.spikeformat`) through a *sink*: on
  CPython the daily ``logs/spike-*.jsonl`` files by default, on the clock what the caller
  passes (or :class:`~arthur.sources.spikeformat.MemorySink`).
- **The console**: the same lines :class:`WhiskerSource` logs ("Whisker connected", "Not
  watching from … to …", "Clean cycle with no visit seen; …", "Last visit moved from … to …",
  "Whisker poll failed [api]: …"), plus one when the live subscription is opened or closes.
  On CPython they go to the ``arthur.whisker`` logger; ``say(level, text)`` replaces that.
- **Tokens**: the ``.whisker-token.json`` shape pylitterbot writes (``access_token``,
  ``id_token``, ``refresh_token``) through ``load_token()`` / ``save_token(tokens)`` /
  ``delete_token()``; on CPython the file by default, so the Mac can switch sources.

Credentials are passed in; nothing here reads the environment or ``.env``.
"""

import asyncio
import sys

from arthur.model import FeederHealth
from arthur.sources import spikeformat as sf
from arthur.sources.records import (
    hhmm,
    latest_weight_since,
    seeds_from_activity,
    snapshot_from_record,
)
from arthur.sources.tracker import Tracker
from arthur.wire import errors, util
from arthur.wire.client import WhiskerClient
from arthur.wire.const import MIN_POLL_S
from arthur.wire.monitor import Monitor, sleep_or_stop

IS_MPY = sys.implementation.name == "micropython"

#: The fallback read for a visit asks for history from this long before the visit's start:
#: activity timestamps are the robot's clock, and detection reaches us -1 to +5 s from it.
FALLBACK_MARGIN_S = 30.0
FALLBACK_LIMIT = 20
#: After stop, how long the connection may take to end by itself before it is cancelled.
STOP_GRACE_S = 5.0

MISSING_CREDENTIALS = ("Whisker credentials are not set (WHISKER_USERNAME, WHISKER_PASSWORD "
                       "in .env); showing NO DATA. Restart after setting them.")


def _console():
    """``say(level, text)`` to the ``arthur.whisker`` logger (CPython), else print."""
    if IS_MPY:
        def say(level, text):
            print(level.upper(), "arthur.whisker:", text)
        return say
    import logging

    log = logging.getLogger("arthur.whisker")
    levels = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

    def say(level, text):
        log.log(levels[level], "%s", text)
    return say


def _file_tokens(path):
    """The token file's load, save and delete (CPython)."""
    from pathlib import Path

    from arthur import spikelog as sl

    path = Path(path)
    return (lambda: sl.load_token(path), lambda tokens: sl.save_token(path, tokens),
            lambda: sl.delete_token(path))


def _now_utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)


class WireSource:
    """The robot, live, through our own client. Run :meth:`run` as a task; call :meth:`view`
    as often as you like."""

    def __init__(self, cfg, *, credentials, tls=None, sink=None, log_dir="logs",
                 token_path=None, load_token=None, save_token=None, delete_token=None,
                 serial=None, wall_fn=None, mono_fn=None, sleep=None, say=None, local=None,
                 client_kw=None):
        self.cfg = cfg
        self._credentials = tuple(credentials) if credentials else None
        self._wall = wall_fn or _now_utc
        self._mono = mono_fn or util.mono
        self._say = say or _console()
        if local is None and IS_MPY:
            def local(t):
                return self.cfg.local(t)
        self._local = local
        if load_token is None and not IS_MPY:
            from arthur.spikelog import TOKEN_FILE

            load_token, file_save, file_delete = _file_tokens(token_path or TOKEN_FILE)
            save_token = save_token or file_save
            delete_token = delete_token or file_delete
        self._load_token = load_token
        self._save_token = save_token
        self._delete_token = delete_token
        if sink is None:
            if IS_MPY:
                sink = sf.MemorySink()
            else:
                from pathlib import Path

                from arthur.spikelog import DailyFiles

                sink = DailyFiles(Path(log_dir))
        self.sink = sink
        if tls is None:
            from arthur.wire.net import Tls

            if IS_MPY:
                from arthur.wire.const import CLOCK_CA_FILE

                tls = Tls(ca_file=CLOCK_CA_FILE)
            else:
                tls = Tls()  # CPython: the system's CA store, verified
        self.client = WhiskerClient(tls, credentials=self._credentials, serial=serial,
                                    on_tokens=self._tokens_changed, **(client_kw or {}))
        self.monitor = Monitor(
            self.client, self._on_record, on_error=self._on_error, on_info=self._on_info,
            on_activity=self._on_activity, poll_s=self.poll_s, mono=self._mono,
            now=self._epoch_now, sleep=sleep or sleep_or_stop, connect=self._connect,
            before_tick=self._flush, after_history=self._fallback)
        self.tracker = Tracker(cfg.timings.visit_min_s)
        self.recorder = sf.Recorder(wall=self._wall, mono=self._mono)
        self._record = None
        self.last_contact_at = None
        self.error = None
        self._seen_activity = set()
        self._fallback_done_for = None
        self.fallback_fetches = 0
        self._cycle_count = None  # the last cycle_count a contact carried
        self._visit_baseline = None  # last visit at the last rise or read
        self._seeded_once = False
        self._gap_from = None  # set by forget_contact: the next contact ends a gap

    # -- what the feeder asks ----------------------------------------------------------------

    def view(self, now):
        """The robot as last seen, with the observed facts at ``now``, and our health."""
        health = FeederHealth(last_contact_at=self.last_contact_at, ws_connected=True,
                              error=self.error)
        if self._record is None:
            return None, health
        obs = self.tracker.observed(now)
        snap = snapshot_from_record(
            self._record,
            status_since=obs.status_since,
            previous_status=obs.previous_status,
            last_visit_at=obs.last_visit_at,
            visit_started_at=obs.visit_started_at,
            cat_left_at=obs.cat_left_at,
            cycled_s=obs.cycled_s,
        )
        return snap, health

    def update_config(self, cfg):
        """Take a reloaded config (poll interval, visit length, fallback time)."""
        self.cfg = cfg
        self.tracker.visit_min_s = float(cfg.timings.visit_min_s)
        self.monitor.poll_s = self.poll_s

    def forget_contact(self):
        """Before running again after watching stopped on purpose (the clock's pause): show
        nothing old as current, and treat the next contact as the end of a gap in watching
        (D-0025), however short, so the history is read. The tracker keeps the last visit.
        Not used by the Mac's feeder."""
        if self.last_contact_at is not None:
            self._gap_from = self.last_contact_at
        self._record = None
        self.last_contact_at = None
        self.error = None

    @property
    def poll_s(self):
        return max(float(self.cfg.timings.poll_s), MIN_POLL_S)

    # -- small things ------------------------------------------------------------------------

    def _epoch_now(self):
        return self._wall().timestamp()

    def _hhmm(self, t):
        return hhmm(t, self._local)

    def _write(self, line):
        if line is not None:
            self.sink.state(line)

    def _log_error(self, where, exc, retry_in=None, name=None):
        try:
            self.sink.state(sf.error_line(self.recorder, where, exc, retry_in, name))
        except OSError:
            pass

    def _log_info(self, event, extra=None):
        self.sink.state(sf.info_line(self.recorder, event, extra))

    def _flush(self):
        try:
            self._write(self.recorder.flush_pending())
        except Exception as exc:  # noqa: BLE001 - as SpikeLogger.flush
            self._log_error("flush", exc)

    def _record_activity(self, acts):
        fetched = sf.utc_iso_ms(self._wall())
        entries, _ = sf.activity_entries(acts, fetched, self._seen_activity)
        for entry in entries:
            self.sink.activity(entry)

    # -- tokens ------------------------------------------------------------------------------

    def _tokens_changed(self, tokens):
        if self._save_token is None:
            return
        try:
            self._save_token(tokens)
        except OSError as exc:
            self._say("warning", "could not save the Whisker token: " + type(exc).__name__)

    def _drop_tokens(self):
        """The token went bad: the next connect logs in with the password."""
        self.client.tokens = None
        if self._delete_token is not None:
            try:
                self._delete_token()
            except OSError:
                pass

    async def _connect(self):
        """``client.connect``, and if Whisker refused a token we did not get by logging in
        just now, one password login at once (as ``connect_account`` does with a saved token
        that is refused)."""
        c = self.client
        logins = c.counts["login"]
        try:
            return await c.connect()
        except Exception as exc:
            if (errors.kind_of(exc) != errors.AUTH or c.counts["login"] != logins
                    or not self._credentials or c.tokens is None):
                raise
        self._drop_tokens()
        return await c.connect()

    # -- what the monitor hands on ------------------------------------------------------------

    def _on_record(self, record, source):
        """Every record: feed the tracker, note the contact, write the line. A heartbeat
        re-reads the record we hold, so it is not a contact.

        A contact long after the one before it ends a gap in watching: the tracker forgets
        what only watching can tell and the history is read. So is a rise in the robot's
        cycle count that no watched visit explains."""
        if source == "ws":
            try:
                self._observe(record, source)
            except Exception as exc:  # noqa: BLE001 - as SpikeLogger.on_update
                try:
                    self._log_error("update-callback", exc)
                except Exception:  # noqa: BLE001, S110
                    pass
            return
        self._observe(record, source)
        if source == "start":
            self._say("info", "Whisker connected")

    def _observe(self, record, source):
        if record is None:
            return
        now = self._wall()
        self._record = dict(record)
        contact = source != "heartbeat"
        before = self.last_contact_at
        forced = contact and before is None and self._gap_from is not None
        if forced:
            before, self._gap_from = self._gap_from, None
        if (contact and before is not None
                and (forced
                     or (now - before).total_seconds() > self.cfg.timings.no_data_after_s)):
            self.tracker.gap(before)
            self.monitor.want_history()
            self._say("info", "Not watching from " + self._hhmm(before) + " to "
                      + self._hhmm(now) + "; reading the robot's history")
        self.tracker.feed(record, now)
        if contact:
            self.last_contact_at = now
            self.error = None
            self._check_cycle_count(record.get("cycle_count"), now)
        self._write(self.recorder.observe(record, source, self.client.data))

    def _check_cycle_count(self, count, now):
        """A clean cycle the robot counted: if the last visit has not moved since the last
        rise (or the last history read), no watched visit explains it."""
        if isinstance(count, bool) or not isinstance(count, int):
            return
        old, self._cycle_count = self._cycle_count, count
        if old is None or count <= old:
            return  # the first record after a connect, or the robot's count was reset
        last = self.tracker.observed(now).last_visit_at
        if last == self._visit_baseline and not self.monitor.history_due:
            self.monitor.want_history()
            self._say("info", "Clean cycle with no visit seen; reading the robot's history")
        self._visit_baseline = last

    def _on_error(self, kind, where, message, retry_in):
        exc = self.monitor.last_exc
        if exc is None:
            exc = message
        if where == "activity":  # a side job: the robot's state still arrives
            self._log_error("activity", exc, retry_in)
            return
        if kind == errors.TIMEOUT:
            kind = errors.API  # as the feeder's source and the studio classify it
        name = None
        if kind == errors.NO_ROBOTS:
            name = "NoRobotFound"  # what the studio's log reader looks for
        if where == "connect" and kind == errors.AUTH:
            where = "login"
            exc = "login rejected (" + str(exc) + ")"
        self._fail(kind, where, exc, retry_in, name)
        if where == "poll" and kind == errors.AUTH:
            self._drop_tokens()

    def _fail(self, kind, where, exc, retry_in, name=None):
        self.error = kind
        self._log_error(where, exc, retry_in, name)
        if isinstance(exc, BaseException):
            what = (name or type(exc).__name__) + ": " + str(exc)
        else:
            what = exc
        retry = "; retrying in " + sf.format_duration(retry_in) if retry_in else ""
        self._say("warning", "Whisker " + where + " failed [" + kind + "]: " + sf.scrub(what)
                  + retry)

    def _on_info(self, event, text):
        if event == "connected":
            self._cycle_count = None  # the first record after a connect only sets it
            self._log_info("connected", {"robots": self.client.robots_seen})
        elif event == "ws_start_ack":
            self._say("info", "Whisker live updates subscribed")
        elif event == "ws_closed":
            self._say("info", "Whisker live connection closed: " + sf.scrub(str(text)))
        elif event == "live_ended":
            self._flush()

    def _on_activity(self, acts):
        self._record_activity(acts)
        self._seed(acts)

    def _seed(self, activities):
        """After every history read: fill in what was not watched. The last visit only ever
        moves later, and a read with no "Cat Detected" in it changes nothing."""
        now = self._wall()
        st = self.tracker.state
        was = self.tracker.observed(now).last_visit_at
        seeds = seeds_from_activity(
            activities, mid_visit=st.in_visit and st.visit_started_at is None
        )
        if seeds:
            self.tracker.seed(**seeds)
        last = self.tracker.observed(now).last_visit_at
        self._visit_baseline = last
        startup, self._seeded_once = not self._seeded_once, True
        if self.tracker.state == st:
            return
        extra = {}
        for k in seeds:
            extra[k] = sf.utc_iso_ms(seeds[k])
        self._log_info("seeded", extra)
        if startup or last == was or last is None:
            return
        if was is None:
            self._say("info", "Last visit set to " + self._hhmm(last)
                      + " by the robot's history")
        else:
            self._say("info", "Last visit moved from " + self._hhmm(was) + " to "
                      + self._hhmm(last) + " by the robot's history")

    async def _fallback(self):
        """Once per visit: no weight long after detection, so look for it in the history."""
        st = self.tracker.state
        start = st.visit_started_at
        if not st.in_visit or start is None or st.cat_left_at is not None:
            return
        if start == self._fallback_done_for:
            return
        if (self._wall() - start).total_seconds() < self.cfg.timings.cat_left_fallback_s:
            return
        self._fallback_done_for = start
        self.fallback_fetches += 1
        since = start.timestamp() - FALLBACK_MARGIN_S
        try:
            acts = await self.client.history(start=since, limit=FALLBACK_LIMIT)
        except Exception as exc:  # noqa: BLE001 - a side job: the robot's state still arrives
            self._log_error("activity", exc)
            return
        self._record_activity(acts)
        self.monitor.saw_activity(acts)
        from datetime import UTC, datetime

        left = latest_weight_since(acts, datetime.fromtimestamp(since, UTC))
        now_st = self.tracker.state
        if (left is not None and now_st.in_visit and now_st.visit_started_at == start
                and now_st.cat_left_at is None):
            self.tracker.seed(cat_left_at=left)
            self._log_info("cat_left_from_history", {"cat_left_at": sf.utc_iso_ms(left)})

    # -- the connection ----------------------------------------------------------------------

    async def run(self, stop):
        """Hold the connection until ``stop``. Never returns on failure."""
        try:
            if self._credentials is None:
                self.error = errors.AUTH
                self._log_error("login", "WHISKER_USERNAME or WHISKER_PASSWORD not set")
                self._say("error", MISSING_CREDENTIALS)
                await stop.wait()
                return
            if self.client.tokens is None and self._load_token is not None:
                tokens = self._load_token()
                self.client.tokens = dict(tokens) if tokens else None
            await self._hold(stop)
        finally:
            try:
                self._log_info("exit")
            except OSError:
                pass
            self.sink.close()

    async def _hold(self, stop):
        """The monitor until ``stop``; a connect or request still running then gets
        :data:`STOP_GRACE_S` to end before it is cancelled (pylitterbot's source cancels the
        login at once). A failure inside the monitor (a bug) is raised."""
        wake = asyncio.Event()

        async def main():
            try:
                await self.monitor.run(stop)
            finally:
                wake.set()

        async def stopper():
            await stop.wait()
            wake.set()

        task = asyncio.create_task(main())
        waiter = asyncio.create_task(stopper())
        try:
            await wake.wait()
        finally:
            waiter.cancel()
        if not task.done():
            try:
                await asyncio.wait_for(task, STOP_GRACE_S)
            except Exception as exc:
                if errors.kind_of(exc) != errors.TIMEOUT:
                    raise
            return
        await task
