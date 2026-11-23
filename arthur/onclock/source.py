"""The robot source on the clock: :class:`~arthur.sources.wire_source.WireSource` with the
clock's login, token file and log ring (D-0029, D-0030).

``deploy/clock/boot.py`` calls :func:`make_source` (through ``arthur.onclock.main``) with the
config, the program's :class:`~arthur.onclock.clocklog.ClockLog`, the shared
:class:`~arthur.wire.net.Tls` and the install root. What it adds to the wire source:

- **The login** is read from ``<root>/secrets.json`` (``whisker_username``,
  ``whisker_password``; ``scripts/clock.py install`` writes it, mode 600). It is never
  logged, never in ``status()`` and never sent through the door. Unreadable, or without both
  values: NO DATA with the ``auth`` reason, and the file is read again after 10 minutes,
  doubling to an hour (a file only the cable can change does not need more).
- **The tokens** are loaded from and saved to ``<root>/tokens.json``, written (whole, through
  a temporary file and a rename) only when the refresh token changes: after a password
  login. A renewal returns no new refresh token, so the hourly renewals never touch the
  flash (8 MiB, which the program must not wear out). After a restart the saved id token may
  have expired; the first call renews it.
- **The clock's time.** Until the system time is past :data:`SANE_EPOCH` (a clock that has
  just booted without Wi-Fi may think it is 1970), nothing is attempted: TLS would refuse
  every certificate as not yet valid and each try would count as a failed login. It looks
  again every :data:`TIME_CHECK_S`.
- **The robot log** (the spike's ``state``, ``info``, ``error`` and ``activity`` lines) and
  the source's console lines go into the log ring, in the shapes ``scripts/clock.py logs
  pull`` writes back into the Mac's ``logs/spike-*.jsonl``, ``spike-activity-*.jsonl`` and
  ``feeder-console.log``.
- **Polls held back from the idle show.** :meth:`ClockSource.set_poll_ok` takes the program's
  ``poll_ok`` (``ClockApp._poll_ok``) and hands it to the wire source, now or when it is
  built; :meth:`ClockSource.poll_held` reports the monitor's count of held polls and
  :meth:`ClockSource.live_up` whether its live subscription is up.
- **A second run** of the same source (after a pause from the Mac, or after its task died)
  starts with :meth:`WireSource.forget_contact`: nothing old is shown as current, and the
  first contact is treated as the end of a gap in watching (D-0025), so the history is read.
"""

import json
import os

from arthur.model import FeederHealth
from arthur.wire import errors, util
from arthur.wire.const import AUTH_BACKOFF_MAX, AUTH_BACKOFF_START
from arthur.wire.monitor import Backoff, sleep_or_stop

SECRETS_NAME = "secrets.json"
TOKENS_NAME = "tokens.json"
#: 2026-01-01 00:00 UTC. A system time before this has not been set yet.
SANE_EPOCH = 1767225600
#: While the time is not set: look again this often (seconds).
TIME_CHECK_S = 2.0
TOKEN_KEYS = ("access_token", "id_token", "refresh_token")


def read_credentials(root):
    """``((username, password), None)`` from ``<root>/secrets.json``, or ``(None, why)``.
    ``why`` never carries a value from the file."""
    try:
        with open(root + "/" + SECRETS_NAME) as f:
            data = json.loads(f.read())
    except OSError as exc:
        return None, SECRETS_NAME + " could not be read (" + type(exc).__name__ + ")"
    except ValueError:
        return None, SECRETS_NAME + " is not JSON"
    if not isinstance(data, dict):
        return None, SECRETS_NAME + " is not a JSON object"
    user = data.get("whisker_username")
    password = data.get("whisker_password")
    if not isinstance(user, str) or not user.strip() or not isinstance(password, str) \
            or not password:
        return None, SECRETS_NAME + " has no Whisker login (whisker_username, whisker_password)"
    return (user.strip(), password), None


class TokenStore:
    """``<root>/tokens.json``: the three tokens, in the shape pylitterbot's token file has.
    :meth:`save` writes only when the refresh token differs from the one on the flash."""

    def __init__(self, root):
        self.path = root + "/" + TOKENS_NAME
        self._on_flash = None  # the refresh token in the file, as far as we know
        self.writes = 0

    def load(self):
        try:
            with open(self.path) as f:
                data = json.loads(f.read())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        for k in TOKEN_KEYS:
            if not isinstance(data.get(k), str) or not data.get(k):
                return None
        self._on_flash = data["refresh_token"]
        out = {}
        for k in TOKEN_KEYS:
            out[k] = data[k]
        return out

    def save(self, tokens):
        rt = tokens.get("refresh_token")
        if not rt or rt == self._on_flash:
            return False
        data = {}
        for k in TOKEN_KEYS:
            data[k] = tokens.get(k)
        tmp = self.path + ".new"
        with open(tmp, "w") as f:
            f.write(json.dumps(data))
        os.rename(tmp, self.path)
        self._on_flash = rt
        self.writes += 1
        return True

    def delete(self):
        self._on_flash = None
        try:
            os.remove(self.path)
        except OSError:
            pass


class LogRingSink:
    """The wire source's sink (``state``, ``activity``, ``close``) into the log ring."""

    def __init__(self, log):
        self.log = log

    def state(self, line):
        self.log.append(line)

    def activity(self, entry):
        self.log.append(entry)

    def close(self):
        pass


def console(log, epoch_fn=None, name="arthur.whisker", echo=True):
    """``say(level, text)``: one ``console`` line into the log ring (and stdout), the shape
    ``ClockApp.console`` writes, with the logger's ``name`` as the Mac's log shows it."""
    epoch_fn = epoch_fn or util.now

    def say(level, text):
        log.append({"kind": "console", "ts": util.utc_iso_ms(epoch_fn()), "level": level,
                    "name": name, "msg": text})
        if echo:
            print(level.upper(), name + ":", text)
    return say


class ClockSource:
    """The feeder's ``Source`` protocol (``view``, ``run``, ``update_config``) for the clock.
    The wire source inside is built on the first run that has a login (so a clock without one
    holds no client), and kept: its tracker remembers the last visit across pauses."""

    def __init__(self, cfg, *, log, tls, root, epoch_fn=None, sleep=None, wire_kw=None,
                 echo=True):
        self.cfg = cfg
        self.log = log
        self.tls = tls
        self.root = root.rstrip("/") if len(root) > 1 else root
        self._epoch = epoch_fn or util.now
        self._sleep = sleep or sleep_or_stop
        self._wire_kw = wire_kw or {}
        self.say = console(log, self._epoch, echo=echo)
        self.tokens = TokenStore(self.root)
        self.inner = None
        self.poll_ok = None  # handed to the wire source (set_poll_ok)
        self.error = None
        self.runs = 0
        self.secret_reads = 0

    # -- the Source protocol ---------------------------------------------------------------

    def view(self, now):
        if self.inner is not None:
            snap, health = self.inner.view(now)
            if self.error is None:
                return snap, health
            return snap, FeederHealth(last_contact_at=health.last_contact_at,
                                      ws_connected=health.ws_connected, error=self.error)
        return None, FeederHealth(last_contact_at=None, ws_connected=True, error=self.error)

    def update_config(self, cfg):
        self.cfg = cfg
        if self.inner is not None:
            self.inner.update_config(cfg)

    async def run(self, stop):
        self.runs += 1
        if not await self._wait_for_time(stop):
            return
        creds = await self._wait_for_login(stop)
        if creds is None:
            return
        self.error = None
        if self.inner is None:
            self.inner = self._build(creds)
        elif self.runs > 1:
            self.inner.forget_contact()
        await self.inner.run(stop)

    def set_poll_ok(self, fn):
        """``poll_ok`` for the wire source's monitor: a routine poll waits while it says
        False (:data:`arthur.wire.monitor.POLL_HOLD_S` at most)."""
        self.poll_ok = fn
        if self.inner is not None:
            self.inner.set_poll_ok(fn)

    def poll_held(self):
        """``[count, worst seconds]`` of polls held back; ``[0, 0.0]`` before the wire source
        is built."""
        if self.inner is None:
            return [0, 0.0]
        return self.inner.poll_held()

    def live_up(self):
        """The wire source's live subscription is up; None before it is built."""
        if self.inner is None:
            return None
        return self.inner.live_up()

    # -- before the connection ---------------------------------------------------------------

    async def _wait_for_time(self, stop):
        """Until the system time is set. False if stopped meanwhile."""
        said = False
        while self._epoch() < SANE_EPOCH:
            if not said:
                self.say("warning", "the clock's time is not set yet ("
                         + util.utc_iso_ms(self._epoch()) + "); waiting for it before "
                         "connecting to Whisker")
                said = True
            if await self._sleep(stop, TIME_CHECK_S):
                return False
        if said:
            self.say("info", "the clock's time is set (" + util.utc_iso_ms(self._epoch())
                     + ")")
        return True

    async def _wait_for_login(self, stop):
        """The login from secrets.json; while there is none, NO DATA (``auth``) and a slow
        look again. None if stopped meanwhile."""
        backoff = Backoff(AUTH_BACKOFF_START, AUTH_BACKOFF_MAX)
        while True:
            self.secret_reads += 1
            creds, why = read_credentials(self.root)
            if creds is not None:
                return creds
            self.error = errors.AUTH
            delay = backoff.next()
            self.log.append({"kind": "error", "ts": util.utc_iso_ms(self._epoch()), "t": 0.0,
                             "where": "login", "error": "Error", "message": why,
                             "retry_in": delay})
            self.say("error", "no Whisker login on the clock: " + why + "; showing NO DATA, "
                     "looking again in " + str(int(delay)) + " s")
            if await self._sleep(stop, delay):
                return None

    def _build(self, creds):
        from arthur.sources.wire_source import WireSource

        kw = dict(self._wire_kw)
        client_kw = kw.pop("client_kw", None)
        return WireSource(self.cfg, credentials=creds, tls=self.tls,
                          sink=LogRingSink(self.log), load_token=self.tokens.load,
                          save_token=self.tokens.save, delete_token=self.tokens.delete,
                          say=self.say, client_kw=client_kw, poll_ok=self.poll_ok, **kw)


def make_source(*, cfg, log, tls, root, **kw):
    """What ``deploy/clock/boot.py``'s ``source_factory`` calls (through
    ``arthur.onclock.main``)."""
    return ClockSource(cfg, log=log, tls=tls, root=root, **kw)
