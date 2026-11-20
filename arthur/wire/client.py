"""The calls: tokens, the robot, a refresh, the live subscription and the history.

:class:`WhiskerClient` holds what pylitterbot's ``Account``, ``LitterRobotSession`` and
``LitterRobot4`` hold for us: the three tokens, the chosen robot's raw data (merged on every
update, as pylitterbot's ``Robot._update_data`` does) and the user id. It decides nothing
about when to call; :class:`arthur.wire.monitor.Monitor` does.
"""

import asyncio
import json
import os

from arthur.wire import cognito, errors, http, lr4, record, srp, util, ws
from arthur.wire.const import (
    COGNITO_URL,
    LR4_URL,
    REQUEST_TIMEOUT_S,
    TOKEN_LEEWAY_S,
    WS_BACKOFF_MAX,
    WS_BACKOFF_START,
    WS_STALE_S,
)

#: A live session shorter than this does not reset the reconnect back-off.
WS_HEALTHY_S = 60.0


def _graphql_codes(errs):
    codes = []
    for e in errs if isinstance(errs, list) else []:
        if isinstance(e, dict):
            codes.append(str(e.get("errorType") or e.get("message") or ""))
    return codes


def _is_unauthorized(codes):
    for c in codes:
        if "Unauthorized" in c or "Token has expired" in c:
            return True
    return False


class WhiskerClient:
    """Read-only access to one account's Litter-Robot 4.

    ``tokens`` is the dict pylitterbot's token callback writes (``access_token``,
    ``id_token``, ``refresh_token``), so a token file can be shared. ``credentials`` is
    ``(username, password)`` or None; without it a dead refresh token is an ``auth`` error.
    ``on_tokens(tokens)`` is called after every renewal or login, to save them.
    """

    def __init__(self, tls, tokens=None, credentials=None, serial=None, lr4_url=LR4_URL,
                 cognito_url=COGNITO_URL, on_tokens=None, now_fn=util.now,
                 timeout=REQUEST_TIMEOUT_S, srp_window=srp.WINDOW, stale_s=WS_STALE_S,
                 ws_backoff=(WS_BACKOFF_START, WS_BACKOFF_MAX), sleep_fn=None):
        self.tls = tls
        self.tokens = dict(tokens) if tokens else None
        self.credentials = credentials
        self.serial = serial
        self.lr4_url = lr4_url
        self.cognito_url = cognito_url
        self.on_tokens = on_tokens
        self.now = now_fn
        self.timeout = timeout
        self.srp_window = srp_window
        self.stale_s = stale_s
        self.ws_backoff = ws_backoff
        self.sleep = sleep_fn or asyncio.sleep
        self.data = None
        self.robot_id = None
        #: How many onboarded Litter-Robot 4s the account had at the last load.
        self.robots_seen = 0
        self.counts = {"refresh": 0, "login": 0, "graphql": 0, "ws_connect": 0}

    # -- tokens -----------------------------------------------------------------------------

    def user_id(self):
        """The id token's ``mid`` claim (``LitterRobotSession.get_user_id``)."""
        if not self.tokens:
            return None
        return util.jwt_claims(self.tokens["id_token"]).get("mid")

    def token_valid(self):
        """The id token has more than 30 s left (``is_token_valid``)."""
        if not self.tokens or not self.tokens.get("id_token") \
                or not self.tokens.get("access_token"):
            return False
        try:
            exp = util.jwt_claims(self.tokens["id_token"]).get("exp")
        except (ValueError, TypeError, KeyError):
            return False
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            return False
        return exp > self.now() + TOKEN_LEEWAY_S

    def _set_tokens(self, tokens):
        self.tokens = tokens
        if self.on_tokens is not None:
            self.on_tokens(dict(tokens))

    async def ensure_token(self, force=False):
        """A usable id token: renewed with the refresh token when it is close to expiry (or
        ``force``), else a password login if we have the password."""
        if not force and self.token_valid():
            return
        refresh_token = self.tokens.get("refresh_token") if self.tokens else None
        if refresh_token:
            try:
                self.counts["refresh"] += 1
                tokens = await cognito.refresh(refresh_token, self.tls, self.cognito_url,
                                               self.timeout)
                self._set_tokens(tokens)
                return
            except errors.WireError as exc:
                if exc.kind != errors.AUTH or not self.credentials:
                    raise
                self.tokens = None  # refused: only the password can help now
        if not self.credentials:
            raise errors.auth_error("no valid Whisker token and no password to log in with")
        self.counts["login"] += 1
        tokens = await cognito.login(self.credentials[0], self.credentials[1], self.tls,
                                     self.cognito_url, self.timeout, self.now,
                                     self.srp_window)
        self._set_tokens(tokens)

    async def bearer(self, force=False):
        await self.ensure_token(force)
        return "Bearer " + self.tokens["id_token"]

    # -- GraphQL ----------------------------------------------------------------------------

    async def graphql(self, payload, field):
        """POST one query and return ``data[field]``. A 401 (or an Unauthorized GraphQL
        error) renews the token once and tries again; a second one is ``auth``."""
        body_text = json.dumps(payload)
        for attempt in (0, 1):
            bearer = await self.bearer(force=attempt == 1)
            self.counts["graphql"] += 1
            resp = await http.request(
                "POST", self.lr4_url, self.tls,
                headers={"Content-Type": "application/json", "Authorization": bearer},
                body=body_text, timeout=self.timeout)
            status = resp.status
            if status == 401:
                if attempt == 0:
                    continue
                raise errors.auth_error("Whisker refused the token (HTTP 401)", 401)
            if status == 403:
                raise errors.api_error("Whisker answered HTTP 403 Forbidden (a blocked "
                                       "User-Agent looked like this in April 2026)", 403)
            if status != 200:
                raise errors.api_error(f"Whisker answered HTTP {status}", status)
            body = resp.json()
            resp = None
            if not isinstance(body, dict):
                raise errors.api_error("Whisker answered JSON that is not an object")
            data = body.get("data")
            value = data.get(field) if isinstance(data, dict) else None
            errs = body.get("errors")
            if value is None and errs:
                codes = _graphql_codes(errs)
                if _is_unauthorized(codes):
                    if attempt == 0:
                        continue
                    raise errors.auth_error("Whisker refused the token: " + ", ".join(codes))
                raise errors.api_error("Whisker GraphQL error: " + ", ".join(codes)[:300],
                                       code=codes[0] if codes else None)
            return value
        raise errors.auth_error("Whisker refused the token")

    # -- the robot --------------------------------------------------------------------------

    async def connect(self):
        """A token, then the account's robot. Returns the record."""
        await self.ensure_token()
        await self.load_robot()
        return self.record()

    async def load_robot(self):
        """``LitterRobot4.fetch_for_account``, then the robot by ``serial`` or the only one.
        Robots not onboarded are left out, as ``Account.get_robots`` does by default."""
        robots = await self.graphql(lr4.robots_query(self.user_id()), "getLitterRobot4ByUser")
        if not isinstance(robots, list):
            robots = []
        found = [r for r in robots if isinstance(r, dict) and r.get("serial") is not None
                 and r.get("isOnboarded") is True]
        robots = None
        self.robots_seen = len(found)
        if self.serial:
            found = [r for r in found if r.get("serial") == self.serial]
            if not found:
                raise errors.WireError(errors.NO_ROBOTS, "No Litter-Robot 4 with the chosen "
                                       "serial on this account")
        if not found:
            raise errors.WireError(errors.NO_ROBOTS,
                                   "No Litter-Robot 4 found on this account")
        if len(found) > 1:
            tails = ", ".join("..." + str(r.get("serial"))[-4:] for r in found)
            raise errors.WireError(errors.NO_ROBOTS, f"{len(found)} Litter-Robot 4s on this "
                                   f"account ({tails}); choose one by serial")
        self.data = dict(found[0])
        self.serial = self.data["serial"]
        self.robot_id = str(self.data.get("unitId"))
        return self.data

    async def refresh_robot(self):
        """``LitterRobot4.refresh``: one robot by serial, merged into what we hold."""
        robot = await self.graphql(lr4.robot_query(self.serial), "getLitterRobot4BySerial")
        if not isinstance(robot, dict):
            raise errors.api_error("Whisker did not return the robot")
        self.merge(robot)
        return self.record()

    def merge(self, robot):
        """``Robot._update_data``: keys in the update replace ours, the rest stay."""
        if self.data is None:
            self.data = {}
        for key in robot:
            self.data[key] = robot[key]

    def record(self):
        return record.build_record(self.data) if self.data is not None else None

    # -- history ----------------------------------------------------------------------------

    async def history(self, start=None, limit=100, page=25):
        """``get_activity_history(limit, start)`` in pages of ``page``. ``start`` is epoch
        seconds. A full page is followed by the next one in the direction the server
        dropped entries (newest first: older ones, by ``endTimestamp``; oldest first: newer
        ones, by ``startTimestamp``). The bounds are inclusive, so pages overlap by their edge
        second and entries that come twice are kept once; a full page all in one second is
        asked for again twice as big. The result is what one request for ``limit`` gives."""
        out = []
        seen = set()
        end = None
        n = min(page, limit)
        while len(out) < limit:
            entries = await self.graphql(lr4.activity_query(self.serial, n, start, end),
                                         "getLitterRobot4Activity")
            if entries is None:
                raise errors.api_error("Activity history could not be retrieved.")
            if not isinstance(entries, list):
                raise errors.api_error("Activity history is not a list")
            full = len(entries) >= n
            acts = lr4.parse_activities(entries)
            entries = None
            fresh = 0
            for a in acts:
                key = (a.iso, a.action.text)
                if key in seen:
                    continue
                seen.add(key)
                out.append(a)
                fresh += 1
            if not full or len(acts) < 2:
                break
            first, last = acts[0].ts, acts[-1].ts
            if first == last:
                if n >= 4 * page:
                    break  # that many entries in one second: not a robot's history
                n *= 2
                continue
            if not fresh:
                break
            if first > last:
                end = last
            else:
                start = last
            n = page
            acts = None
        return out[:limit]

    # -- the live subscription --------------------------------------------------------------

    async def live(self, on_update, on_event=None):
        """Hold the subscription until cancelled: connect, ``start``, apply every ``data``
        message for our robot and call ``on_update()``. Reconnects after a close, an error or
        ``stale_s`` of silence, waiting 1 s doubling to 300 s (pylitterbot's monitor)."""
        delay = self.ws_backoff[0]
        while True:
            began = util.mono()
            reason = None
            try:
                reason = await self.live_once(on_update, on_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure: report and reconnect
                reason = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, errors.WireError) and exc.kind == errors.AUTH:
                    self._force_token = True
            if util.mono() - began >= WS_HEALTHY_S:
                delay = self.ws_backoff[0]
            if on_event is not None:
                on_event("ws_closed", f"{reason}; reconnecting in {delay:g}s")
            await self.sleep(delay)
            delay = min(delay * 2, self.ws_backoff[1])

    _force_token = False

    async def live_once(self, on_update, on_event=None):
        """One WebSocket session. Returns why it ended."""
        force, self._force_token = self._force_token, False
        bearer = await self.bearer(force)
        user_id = self.user_id()
        self.counts["ws_connect"] += 1
        sock = await ws.WebSocket.connect(lr4.ws_url(bearer, self.lr4_url), self.tls,
                                          subprotocol="graphql-ws", timeout=self.timeout)
        sub_id = _uuid4()
        try:
            await sock.send_text(lr4.start_message(sub_id, bearer, user_id))
            bearer = None
            if on_event is not None:
                on_event("ws_open", "subscribed")
            while True:
                try:
                    opcode, payload = await asyncio.wait_for(sock.recv(), self.stale_s)
                except ws.Closed as exc:
                    return "closed by the server" + (f" ({exc.code})" if exc.code else "")
                except Exception as exc:
                    if errors.kind_of(exc) == errors.TIMEOUT:
                        return f"stale after {self.stale_s:.0f}s without a message"
                    raise
                if opcode != ws.TEXT:
                    continue
                try:
                    message = json.loads(payload)
                except ValueError:
                    continue  # pylitterbot skips a message that is not JSON
                payload = None
                kind = message.get("type") if isinstance(message, dict) else None
                if kind == "data":
                    robots = lr4.robots_in_message(message)
                    message = None
                    for item in robots or ():
                        if isinstance(item, dict) and str(item.get("unitId")) == self.robot_id:
                            self.merge(item)
                            on_update()
                            break
                elif kind == "error":
                    text = json.dumps(message.get("payload"))[:300]
                    if "Unauthorized" in text or "expired" in text:
                        self._force_token = True
                    return "server error message: " + text
                elif kind == "complete":
                    return "subscription completed by the server"
                elif kind == "start_ack" and on_event is not None:
                    on_event("ws_start_ack", "")
        finally:
            # Cancelled (a pause on the clock), MicroPython 1.29 can raise again at an await
            # in this block (``wait_for`` raised TypeError or CancelledError when probed), so
            # the polite goodbye is tried and the socket is then closed without awaiting.
            try:
                if not sock.closed:
                    try:
                        await asyncio.wait_for(sock.send_text(lr4.stop_message(sub_id)), 2)
                    except Exception:  # noqa: BLE001 - saying stop is a courtesy
                        sock.closed = True
                await sock.close()
            finally:
                sock.close_now()


def _uuid4():
    """A random UUID4 string (pylitterbot uses ``uuid.uuid4``; MicroPython has no uuid)."""
    b = bytearray(os.urandom(16))
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    h = "".join(f"{x:02x}" for x in b)
    return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32]
