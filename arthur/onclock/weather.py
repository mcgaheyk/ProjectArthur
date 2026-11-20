"""The weather for the idle show's page, fetched by the clock itself (D-0019, D-0029).

The same as :class:`arthur.sources.weather.WeatherSource` on the Mac: the same request and
parsing (:mod:`arthur.sources.openmeteo`), one reading at start and then every ``[weather]
refresh_s`` (never more often than once a minute), the last reading kept on any failure with
one log line, retries after 60 s doubling to 900 s, and :meth:`WeatherService.view` answers the
latest reading as it is (mapping judges its age). It is a decoration and never raises out of
:meth:`run`.

Over verified TLS with :mod:`arthur.wire` (the CA file is the clock's bundle). One connection
per fetch, closed after it: fetches are 15 minutes apart.

``quiet_fn``, when given, is asked before each fetch: while it says False (a scene is about to
play or playing) the fetch waits, up to :data:`QUIET_WAIT_S`, so the TLS handshake's
arithmetic does not land on the frames that move most.
"""

import asyncio
import json

from arthur.onclock import httpconn
from arthur.sources.openmeteo import (
    MIN_INTERVAL_S,
    OPEN_METEO_URL,
    RETRY_MAX_S,
    TIMEOUT_S,
    USER_AGENT,
    WeatherError,
    parse_open_meteo,
    query_string,
    request_params,
)

#: Longest a fetch waits for ``quiet_fn``.
QUIET_WAIT_S = 60.0
#: How often a fetch in flight looks at the stop event, real seconds.
POLL_S = 0.05


class _Stopped(Exception):
    pass


class Backoff:
    """60 s doubling to 900 s (``arthur.spikelog.Backoff``)."""

    def __init__(self, start, maximum):
        self.start = start
        self.maximum = maximum
        self._next = start

    def next(self):
        delay = self._next
        self._next = min(self._next * 2, self.maximum)
        return delay

    def reset(self):
        self._next = self.start


def _duration(seconds):
    """``arthur.spikelog.format_duration``."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "%.1fs" % seconds  # noqa: UP031 - the same on MicroPython
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh%02dm%02ds" % (hours, minutes, secs)  # noqa: UP031
    return "%dm%02ds" % (minutes, secs)  # noqa: UP031


class WeatherService:
    """Run :meth:`run` as a task; call :meth:`view` as often as you like.

    ``clock`` is an :class:`arthur.onclock.clock.Clock`, ``tls`` an
    :class:`arthur.wire.net.Tls` and ``log`` a function ``log(level, message)``.
    """

    def __init__(self, cfg, tls, clock, *, log=None, url=OPEN_METEO_URL, timeout_s=TIMEOUT_S,
                 quiet_fn=None):
        self.cfg = cfg
        self.tls = tls
        self.clock = clock
        self.url = url
        self.timeout_s = timeout_s
        self.quiet_fn = quiet_fn
        self._log = log or (lambda level, msg: None)
        self.reading = None
        self.error = None
        self.fetches = 0
        self.failures = 0

    def view(self, now):
        """The latest reading, however old; None before the first, or with no ``[weather]``."""
        if self.cfg.weather is None:
            return None
        return self.reading

    def update_config(self, cfg):
        """Take a reloaded config. A new place or refresh applies from the next fetch; a new
        unit drops the reading at once, so a number is never shown in the wrong unit."""
        old, new = self.cfg.weather, cfg.weather
        if old is not None and new is not None and old.units != new.units:
            self.reading = None
        self.cfg = cfg

    def request_url(self, wc):
        return self.url + "?" + query_string(request_params(wc, self.cfg.tz.key))

    async def fetch(self, wc):
        """One request and its reading. Raises on any failure."""
        url = self.request_url(wc)
        scheme, _, rest = url.partition("://")
        cut = rest.find("/")
        origin, path = scheme + "://" + rest[:cut], rest[cut:]
        conn = httpconn.HttpConnection(origin, self.tls, timeout_s=self.timeout_s,
                                       user_agent=USER_AGENT)
        self.fetches += 1
        try:
            answer = await conn.request("GET", path, headers={"Accept": "application/json",
                                                              "Connection": "close"})
        finally:
            await conn.close()
        try:
            data = json.loads(answer.body)
        except ValueError:
            raise WeatherError("HTTP " + str(answer.status) + ", body is not JSON") from None
        if answer.status != 200:
            reason = data.get("reason") if isinstance(data, dict) else None
            raise WeatherError("HTTP " + str(answer.status) + ": "
                               + str(reason or "no reason given"))
        return parse_open_meteo(data, self.clock.wall(), wc.units)

    @staticmethod
    async def _until_stop(stop, coro):
        """``await coro``, or :class:`_Stopped` as soon as ``stop`` is set (the request is
        cancelled)."""
        task = asyncio.create_task(coro)
        while not task.done():
            if stop.is_set():
                task.cancel()
                try:
                    await task
                except BaseException:  # noqa: BLE001, S110 - cancelled on purpose
                    pass
                raise _Stopped()
            await asyncio.sleep(POLL_S)
        return await task

    async def _wait_quiet(self, stop):
        """Wait while ``quiet_fn`` says no, up to :data:`QUIET_WAIT_S`. True if stopped."""
        if self.quiet_fn is None:
            return False
        end = self.clock.mono() + QUIET_WAIT_S
        while not stop.is_set() and self.clock.mono() < end:
            try:
                if self.quiet_fn():
                    return False
            except Exception:  # noqa: BLE001 - a broken hint must not stop the weather
                return False
            await self.clock.sleep(0.5)
        return stop.is_set()

    async def run(self, stop):
        """Fetch now, then every ``refresh_s``, until ``stop``. Never returns on failure."""
        backoff = Backoff(MIN_INTERVAL_S, RETRY_MAX_S)
        while not stop.is_set():
            wc = self.cfg.weather
            if wc is None:  # [weather] was removed: nothing to fetch, look again later
                delay = MIN_INTERVAL_S
            else:
                if await self._wait_quiet(stop):
                    return
                try:
                    reading = await self._until_stop(stop, self.fetch(wc))
                except _Stopped:
                    return
                except Exception as exc:  # noqa: BLE001 - a decoration: keep going
                    delay = max(backoff.next(), MIN_INTERVAL_S)
                    self.failures += 1
                    self.error = type(exc).__name__ + ": " + str(exc)
                    kept = "keeping the last reading" if self.reading else "no reading yet"
                    self._log("warning", "weather fetch failed: " + self.error + "; " + kept
                              + "; retrying in " + _duration(delay))
                else:
                    self.reading = reading
                    backoff.reset()
                    delay = max(float(wc.refresh_s), MIN_INTERVAL_S)
                    if self.error is not None:
                        self._log("info", "weather fetched again")
                    self.error = None
            if await self.clock.wait(stop, delay):
                return
