"""The clock's own transport: frames to AWTRIX NG 1.2.3's HTTP API on the same device.

It does what :class:`arthur.transports.awtrix_ng.AwtrixNgTransport` does on the Mac, with the
same requests (built by :mod:`arthur.awtrix_api`, so the pushed body is the same byte for
byte) and the same rules:

- one pushed app, ``litter``, whose ``draw`` is the whole frame as a bitmap, with
  ``lifetimeMs`` from ``ttl_s`` and ``lifetimeExpiry: "mark"`` (D-0003, D-0004);
- before pushing, ``enlargeApps`` is read and turned off if it is on, and nothing is pushed
  if that fails; after pushing, ours is made the only app in the rotation;
- both checks are repeated after ``settings_check_s`` (300 s) and after any failed request.

What differs, because AWTRIX is on the same device and we push 8 times a second:

- **One connection is kept open** (:class:`~arthur.onclock.httpconn.HttpConnection`) and
  opened again when AWTRIX closes it (about one request in a hundred) or after a failure.
- **Every new connection after the first repeats both checks.** A restarted AWTRIX has lost
  our app (pushed apps live in RAM) and drops our connection; the push after it recreates the
  app, and the checks make sure the setting and the rotation are right again. AWTRIX also
  drops a connection idle for 5 s, so a quiet screen costs two extra requests per push.
- **A request on a kept connection that AWTRIX had already closed** is tried once more on a
  new one (the whole show: checks, then the push) instead of failing the push.
- The request is built once per frame size and lifetime and the frame's base64 written into
  it, so a push allocates only the base64 text.
- Plain HTTP only, and only to this device (``127.0.0.1``, ``localhost``, ``::1``).
"""

import binascii
import json

from arthur import awtrix_api
from arthur.onclock import httpconn
from arthur.wire.net import Tls

LOOPBACK = ("127.0.0.1", "localhost", "::1")


class TransportError(Exception):
    """AWTRIX refused the request or could not be reached. ``status`` is the HTTP status
    (None without an answer) and ``body`` the answer's text, when there was one."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body

    def __str__(self):
        msg = self.message
        if self.status is not None:
            msg = msg + " (HTTP " + str(self.status) + ")"
        if self.body:
            msg = msg + ": " + self.body[:300]
        return msg


class _Stale(Exception):
    """A kept connection was already closed when we used it."""


class ClockTransport:
    def __init__(self, base_url="http://127.0.0.1", app_name=awtrix_api.APP_NAME, *,
                 time_fn, timeout_s=5.0, settings_check_s=awtrix_api.SETTINGS_CHECK_S,
                 only_app=True):
        tls = Tls(allow_plain=True)
        self.conn = httpconn.HttpConnection(base_url, tls, timeout_s=timeout_s)
        if self.conn.url.secure or self.conn.url.host not in LOOPBACK:
            raise ValueError("the clock's transport talks plain HTTP to this device only, "
                             "not " + base_url)
        self.app_name = app_name
        self.time_fn = time_fn
        self.settings_check_s = settings_check_s
        self.only_app = only_app
        #: ``time_fn()`` of the last good check of each kind; None means check.
        self._settings_ok_at = None
        self._rotation_ok_at = None
        self._push_key = None  # (width, height, ttl_s) the request below was built for
        self._push_req = None  # bytearray: head, then the body with room for the base64
        self._b64_at = 0
        self._b64_len = 0
        self._rgb = None  # bytearray, the frame as RGB888
        #: Checks and pushes repeated because a kept connection was found closed.
        self.stale_retries = 0
        #: Times both checks were forgotten because a new connection was opened.
        self.reconnect_checks = 0

    # -- requests ----------------------------------------------------------------------------

    def forget_checks(self):
        self._settings_ok_at = None
        self._rotation_ok_at = None

    async def _send(self, data, label):
        """Send one whole request; the answer's text if it is a 2xx. Any failure forgets
        both checks."""
        try:
            answer = await self.conn.send(data, label)
        except httpconn.ConnectionError as exc:
            self.forget_checks()
            if exc.stale:
                raise _Stale(str(exc)) from None
            raise TransportError(str(exc)) from None
        text = answer.body.decode()
        if not 200 <= answer.status < 300:
            self.forget_checks()
            raise TransportError(label + " failed", answer.status, text)
        return text

    async def _request(self, method, path, body_text=None):
        headers = {"Content-Type": "application/json"} if body_text is not None else None
        body = body_text.encode() if body_text is not None else None
        head = self.conn.head(method, path, None if body is None else len(body), headers)
        return await self._send(head + body if body is not None else head,
                                method + " " + path)

    async def _json(self, method, path, body_text=None):
        text = await self._request(method, path, body_text)
        try:
            return json.loads(text)
        except ValueError:
            raise TransportError(method + " " + path + ": not JSON", None, text) from None

    # -- the checks --------------------------------------------------------------------------

    def _due(self, ok_at):
        return ok_at is None or self.time_fn() - ok_at > self.settings_check_s

    async def ensure_native_size(self):
        """Turn ``enlargeApps`` off if the clock has it on. Skipped while the last good check
        is younger than ``settings_check_s``. A clock without the key is left alone."""
        if not self._due(self._settings_ok_at):
            return
        self._settings_ok_at = None
        path = awtrix_api.SETTINGS_PATH
        settings = await self._json("GET", path)
        if not isinstance(settings, dict):
            raise TransportError("GET " + path + ": not a JSON object", None, str(settings))
        if awtrix_api.needs_native_size(settings):
            updated = await self._json("PATCH", path, awtrix_api.NATIVE_SIZE_TEXT)
            if not isinstance(updated, dict):
                raise TransportError("PATCH " + path + ": not a JSON object", None,
                                     str(updated))
            if updated.get("enlargeApps") is not False:
                raise TransportError("PATCH " + path + " did not turn enlargeApps off", None,
                                     json.dumps(updated))
        self._settings_ok_at = self.time_fn()

    async def ensure_only_app(self):
        """Take every other app out of the rotation if any is in it. Skipped while the last
        good check is younger than ``settings_check_s``."""
        if not self._due(self._rotation_ok_at):
            return
        self._rotation_ok_at = None
        path = awtrix_api.APPS_PATH
        apps = await self._json("GET", path)
        if not awtrix_api.apps_ok(apps):
            raise TransportError("GET " + path + ": not an array of apps with names", None,
                                 str(apps))
        fix = awtrix_api.rotation_fix(apps, self.app_name)
        if fix is not None:
            await self._request("PUT", awtrix_api.ORDER_PATH, awtrix_api.order_text(fix))
        self._rotation_ok_at = self.time_fn()

    # -- the push ----------------------------------------------------------------------------

    def push_request(self, frame, ttl_s):
        """The whole ``PUT`` for ``frame`` (head and body) in a buffer kept between pushes;
        valid until the next call. Its body is :func:`arthur.awtrix_api.payload_text`."""
        w, h = frame.width, frame.height
        key = (w, h, ttl_s)
        if key != self._push_key:
            head, tail = awtrix_api.payload_frame(w, h, ttl_s)
            n64 = awtrix_api.b64_length(w * h * 3)
            body_len = len(head) + n64 + len(tail)
            req = self.conn.head("PUT", awtrix_api.pushed_path(self.app_name), body_len,
                                 {"Content-Type": "application/json"})
            self._b64_at = len(req) + len(head)
            self._b64_len = n64
            self._push_req = bytearray(req + head.encode() + bytes(n64) + tail.encode())
            self._rgb = bytearray(w * h * 3)
            self._push_key = key
        rgb = self._rgb
        i = 0
        for px in frame.pixels:
            rgb[i] = px[0]
            rgb[i + 1] = px[1]
            rgb[i + 2] = px[2]
            i += 3
        enc = binascii.b2a_base64(rgb)  # with a newline at the end, left out below
        at = self._b64_at
        self._push_req[at:at + self._b64_len] = memoryview(enc)[:self._b64_len]
        return self._push_req

    async def _show_once(self, frame, ttl_s):
        if await self._open():
            self.forget_checks()
            self.reconnect_checks += 1
        await self.ensure_native_size()
        await self._send(self.push_request(frame, ttl_s),
                         "PUT " + awtrix_api.pushed_path(self.app_name))
        # After the push: a pushed app lives in RAM and may not exist before the first one.
        if self.only_app:
            await self.ensure_only_app()

    async def _open(self):
        """Open a connection if none is held. True when it is a new one after the first
        (AWTRIX may have restarted since the last)."""
        try:
            opened = await self.conn.open()
        except httpconn.ConnectionError as exc:
            self.forget_checks()
            raise TransportError(str(exc)) from None
        return opened and self.conn.opened > 1

    async def show(self, frame, ttl_s):
        """Push ``frame``; the clock marks it stale if nothing new arrives within ``ttl_s``.
        Raises :class:`TransportError`."""
        try:
            await self._show_once(frame, ttl_s)
            return
        except _Stale:
            self.stale_retries += 1  # the kept connection was gone: once more, on a new one
        try:
            await self._show_once(frame, ttl_s)
        except _Stale as exc:
            raise TransportError(str(exc)) from None

    async def check(self):
        """Is AWTRIX answering? Runs the settings check (forced), so it also turns
        ``enlargeApps`` off. Raises :class:`TransportError`."""
        self._settings_ok_at = None
        try:
            await self._open()
            await self.ensure_native_size()
        except _Stale:
            await self._open()
            try:
                await self.ensure_native_size()
            except _Stale as exc:
                raise TransportError(str(exc)) from None

    async def clear(self):
        try:
            await self._request("DELETE", awtrix_api.app_path(self.app_name))
        except _Stale as exc:
            raise TransportError(str(exc)) from None

    async def close(self):
        await self.conn.close()
