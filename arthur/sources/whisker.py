"""The live source: the robot through Whisker's cloud, for the feeder.

:class:`WhiskerSource` holds the one connection to Whisker (one login, one WebSocket
subscription, a ``refresh()`` every ``poll_s``), writes every update to the spike's log files
in the spike's own format (so the studio's Robot log source keeps working while the feeder
runs, and the data collection goes on), feeds each record to a
:class:`~arthur.sources.tracker.Tracker` dated with the wall time it was seen (D-0015), and
answers :meth:`WhiskerSource.view` for the feeder's loop.

The connection handling, records, log lines, token cache and error classification are the
spike's (:mod:`arthur.spikelog`); what differs is that the feeder never gives up:

- a rejected login is ``"auth"`` and is retried slowly (600 s rising to 3600 s) so the account
  is not locked out; missing credentials are ``"auth"`` for good (the environment is read
  once, at start);
- a network or API failure is ``"api"`` (30 s rising to 600 s); a token rejected while polling
  is dropped and the feeder logs in again, as the spike does;
- no robot on the account is ``"no_robots"``, retried like ``"api"``;
- the error clears with the next contact (an update pushed or polled).

What was not watched is filled in from the robot's activity history (D-0018, D-0025): at
start, after a gap in contact longer than ``no_data_after_s``, after a clean cycle with no
visit seen, and at the routine read every six hours. A read that is due waits at least
:data:`arthur.spikelog.MIN_POLL_SECONDS` after the one before it.

pylitterbot is imported only inside :meth:`WhiskerSource._monitor` (and lazily by
:func:`arthur.spikelog.connect_account`), so importing this module never touches the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arthur import spikelog as sl
from arthur.config import Config
from arthur.model import FeederHealth, RobotSnapshot
from arthur.sources.records import (
    hhmm,
    latest_weight_since,
    seeds_from_activity,
    snapshot_from_record,
)
from arthur.sources.spikeformat import action_text  # noqa: F401  (re-exported)
from arthur.sources.tracker import Tracker

log = logging.getLogger("arthur.whisker")

#: Retry after a rejected login: slow, so the account is not locked out.
AUTH_BACKOFF_START = 600.0
AUTH_BACKOFF_MAX = 3600.0
#: How often the monitor loop wakes (flush held lines, check what is due).
TICK_S = 1.0
#: The first activity fetch after start asks for this much history. Without a start the API
#: returns about six days capped at 100 entries, and which 100 is not documented; a shorter
#: window makes sure the newest entries (the visit under way) are among them.
FIRST_ACTIVITY_WINDOW = timedelta(hours=72)
#: The fallback fetch for a visit asks for history from this long before the visit's start:
#: activity timestamps are the robot's clock, and detection reaches us -1 to +5 s from it.
FALLBACK_MARGIN = timedelta(seconds=30)
FALLBACK_LIMIT = 20

ConnectFn = Callable[[str, str, Path], Awaitable[tuple[Any, Any]]]
SleepFn = Callable[[asyncio.Event, float], Awaitable[bool]]


# Activity history → tracker seeds: pure, shared with the on-clock source in
# :mod:`arthur.sources.records` (re-exported here under the names this module always had).


def _hhmm(t: datetime) -> str:
    """Local time as HH:MM, for the console."""
    return hhmm(t)


# --------------------------------------------------------------------------------------------
# The source
# --------------------------------------------------------------------------------------------


class WhiskerSource:
    """The robot, live. Run :meth:`run` as a task; call :meth:`view` as often as you like."""

    def __init__(
        self,
        cfg: Config,
        *,
        credentials: tuple[str, str] | None,
        log_dir: str | Path = Path("logs"),
        token_path: Path = sl.TOKEN_FILE,
        connect: ConnectFn | None = None,
        wall_fn: Callable[[], datetime] | None = None,
        mono_fn: Callable[[], float] = time.monotonic,
        sleep: SleepFn | None = None,
    ) -> None:
        self.cfg = cfg
        self._credentials = credentials
        self.token_path = Path(token_path)
        self._connect: ConnectFn = connect or sl.connect_account
        self._wall = wall_fn or (lambda: datetime.now(UTC))
        self._mono = mono_fn
        self._sleep: SleepFn = sleep or sl._sleep_or_stop
        self.tracker = Tracker(cfg.timings.visit_min_s)
        self.logger = sl.SpikeLogger(
            Path(log_dir),
            recorder=sl.Recorder(wall=self._wall, mono=self._mono),
            echo=False,
            on_record=self._on_record,
        )
        self._record: dict[str, Any] | None = None
        self.last_contact_at: datetime | None = None
        self.error: str | None = None
        self._fallback_done_for: datetime | None = None
        self.fallback_fetches = 0
        self._history_due = False  # a gap or an unexplained clean: read the history soon
        self._cycle_count: int | None = None  # the last cycle_count a contact carried
        self._visit_baseline: datetime | None = None  # last visit at the last rise or read
        self._seeded_once = False

    # -- what the feeder asks ----------------------------------------------------------------

    def view(self, now: datetime) -> tuple[RobotSnapshot | None, FeederHealth]:
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
        )
        return snap, health

    def update_config(self, cfg: Config) -> None:
        """Take a reloaded config (poll interval, visit length, fallback time)."""
        self.cfg = cfg
        self.tracker.visit_min_s = float(cfg.timings.visit_min_s)

    @property
    def poll_s(self) -> float:
        return sl.clamp_poll_seconds(float(self.cfg.timings.poll_s))[0]

    # -- bookkeeping -------------------------------------------------------------------------

    def _on_record(self, record: dict[str, Any], source: str) -> None:
        """Every observed record (SpikeLogger.observe): feed the tracker, note the contact.
        A heartbeat re-reads the robot's cached data, so it is not a contact.

        A contact long after the one before it ends a gap in watching: the tracker forgets
        what only watching can tell and the history is read. So is a rise in the robot's
        cycle count that no watched visit explains."""
        now = self._wall()
        self._record = dict(record)
        contact = source != "heartbeat"
        before = self.last_contact_at
        if (contact and before is not None
                and (now - before).total_seconds() > self.cfg.timings.no_data_after_s):
            self.tracker.gap(before)
            self._history_due = True
            log.info("Not watching from %s to %s; reading the robot's history",
                     _hhmm(before), _hhmm(now))
        self.tracker.feed(record, now)
        if contact:
            self.last_contact_at = now
            self.error = None
            self._check_cycle_count(record.get("cycle_count"), now)

    def _check_cycle_count(self, count: Any, now: datetime) -> None:
        """A clean cycle the robot counted: if the last visit has not moved since the last
        rise (or the last history read), no watched visit explains it."""
        if isinstance(count, bool) or not isinstance(count, int):
            return
        old, self._cycle_count = self._cycle_count, count
        if old is None or count <= old:
            return  # the first record after a connect, or the robot's count was reset
        last = self.tracker.observed(now).last_visit_at
        if last == self._visit_baseline and not self._history_due:
            self._history_due = True
            log.info("Clean cycle with no visit seen; reading the robot's history")
        self._visit_baseline = last

    def _fail(self, kind: str, where: str, exc: BaseException | str,
              retry_in: float | None) -> None:
        self.error = kind
        self.logger.log_error(where, exc, retry_in=retry_in)
        what = f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else exc
        retry = f"; retrying in {sl.format_duration(retry_in)}" if retry_in else ""
        log.warning("Whisker %s failed [%s]: %s%s", where, kind, sl.scrub(str(what)), retry)

    def _seed(self, activities: Iterable[Any]) -> None:
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
        self.logger.log_info("seeded", **{k: sl.utc_iso_ms(v) for k, v in seeds.items()})
        if startup or last == was or last is None:
            return
        if was is None:
            log.info("Last visit set to %s by the robot's history", _hhmm(last))
        else:
            log.info("Last visit moved from %s to %s by the robot's history",
                     _hhmm(was), _hhmm(last))

    async def _fallback(self, robot: Any) -> None:
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
        since = start - FALLBACK_MARGIN
        try:
            activities = list(await robot.get_activity_history(limit=FALLBACK_LIMIT, start=since))
        except Exception as exc:  # noqa: BLE001 - a side job: the robot's state still arrives
            self.logger.log_error("activity", exc)
            return
        self.logger.record_activity(activities)
        left = latest_weight_since(activities, since)
        now_st = self.tracker.state
        if (left is not None and now_st.in_visit and now_st.visit_started_at == start
                and now_st.cat_left_at is None):
            self.tracker.seed(cat_left_at=left)
            self.logger.log_info("cat_left_from_history", cat_left_at=sl.utc_iso_ms(left))

    # -- the connection ----------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Hold the connection until ``stop``. Never returns on failure."""
        try:
            if self._credentials is None:
                self.error = "auth"
                self.logger.log_error("login", "WHISKER_USERNAME or WHISKER_PASSWORD not set")
                log.error("Whisker credentials are not set (WHISKER_USERNAME, "
                          "WHISKER_PASSWORD in .env); showing NO DATA. Restart after setting "
                          "them.")
                await stop.wait()
                return
            await self._connect_loop(stop, *self._credentials)
        finally:
            self.logger.robot = None
            with contextlib.suppress(OSError):
                self.logger.log_info("exit")
            self.logger.close()

    async def _connect_loop(self, stop: asyncio.Event, username: str, password: str) -> None:
        auth_backoff = sl.Backoff(AUTH_BACKOFF_START, AUTH_BACKOFF_MAX)
        api_backoff = sl.Backoff()
        while not stop.is_set():
            try:
                account, robot = await sl._until_stop(
                    stop, self._connect(username, password, self.token_path)
                )
            except sl._Stopped:
                return
            except sl.AuthFailed:
                delay = auth_backoff.next()
                self._fail("auth", "login", "login rejected", delay)
                if await self._sleep(stop, delay):
                    return
                continue
            except sl.NoRobotFound as exc:
                delay = api_backoff.next()
                self._fail("no_robots", "connect", exc, delay)
                if await self._sleep(stop, delay):
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - network/API: retry with backoff
                delay = api_backoff.next()
                self._fail("api", "connect", exc, delay)
                if await self._sleep(stop, delay):
                    return
                continue
            auth_backoff.reset()
            api_backoff.reset()
            try:
                needs_password = await self._monitor(account, robot, stop)
            finally:
                self.logger.robot = None
                with contextlib.suppress(Exception):
                    await account.disconnect()
            if needs_password:
                sl.delete_token(self.token_path)

    async def _monitor(self, account: Any, robot: Any, stop: asyncio.Event) -> bool:
        """Watch one connected robot until stop. Returns True if the token went bad and a
        fresh login is needed. The same cadence as the spike's ``monitor``."""
        from pylitterbot.event import EVENT_UPDATE

        lg = self.logger
        lg.robot = robot
        self._cycle_count = None  # the first record after a connect only sets it
        unsubscribe = robot.on(EVENT_UPDATE, lg.on_update)
        lg.log_info("connected", robots=len(account.get_robots(type(robot))))
        lg.observe("start")
        log.info("Whisker connected")

        mono = self._mono
        poll_backoff = sl.Backoff()
        activity_backoff = sl.Backoff()
        next_poll = mono() + self.poll_s
        next_heartbeat = mono() + sl.HEARTBEAT_SECONDS
        next_activity = mono()  # now, then every 6 hours, or sooner when a read is due
        activity_tried: float | None = None  # when the last history read was attempted
        activity_failing = False  # the last read failed: its backoff decides the next

        def due_read_allowed(at: float) -> bool:
            return self._history_due and not activity_failing and (
                activity_tried is None or at >= activity_tried + sl.MIN_POLL_SECONDS)

        pets_refresh = False  # connect() just loaded the pets
        try:
            while not stop.is_set():
                now = mono()
                lg.flush()
                if self._history_due and not activity_failing:
                    earliest = now if activity_tried is None else max(
                        now, activity_tried + sl.MIN_POLL_SECONDS)
                    next_activity = min(next_activity, earliest)
                if now >= next_activity:
                    activity_tried = now
                    try:
                        first_since = self._wall() - FIRST_ACTIVITY_WINDOW
                        activities = await lg.fetch_activity(
                            account.pets or [], refresh_pets=pets_refresh,
                            first_since=first_since,
                        )
                        pets_refresh = True
                        activity_backoff.reset()
                        activity_failing = False
                        next_activity = mono() + sl.ACTIVITY_SECONDS
                        self._history_due = False
                        self._seed(activities)
                    except Exception as exc:  # noqa: BLE001
                        activity_failing = True
                        delay = activity_backoff.next()
                        lg.log_error("activity", exc, retry_in=delay)
                        next_activity = mono() + max(delay, self.poll_s)
                await self._fallback(robot)
                if now >= next_poll:
                    try:
                        await lg.poll()
                        poll_backoff.reset()
                        next_poll = mono() + self.poll_s
                    except Exception as exc:  # noqa: BLE001
                        if sl.is_auth_error(exc):
                            self._fail("auth", "poll", exc, None)
                            return True
                        # Even a retry stays above the politeness floor.
                        delay = max(poll_backoff.next(), float(sl.MIN_POLL_SECONDS))
                        self._fail("api", "poll", exc, delay)
                        next_poll = mono() + delay
                if now >= next_heartbeat:
                    lg.observe("heartbeat")
                    next_heartbeat = mono() + sl.HEARTBEAT_SECONDS
                if due_read_allowed(mono()):
                    continue  # read it now, not a tick later; the read clears it or fails
                if await self._sleep(stop, TICK_S):
                    break
        finally:
            with contextlib.suppress(Exception):
                unsubscribe()
            lg.flush()
        return False
