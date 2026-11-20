"""One kept HTTP/1.1 connection to one server, over asyncio streams, for both Pythons.

AWTRIX takes about 8 pushes a second from us, so the connection is kept open between requests
(``arthur.wire.http`` opens one per request, which suits Whisker's few calls a minute). The
answer is read with ``arthur.wire.http``'s reader. The connection is closed and opened again
when the server says ``Connection: close`` (AWTRIX does about once in a hundred requests), when
a body runs to the end of the stream, and after any failure.

A request on a kept connection that the server has already closed (AWTRIX drops one idle for
5 s, and a restarted AWTRIX has forgotten it) fails before any answer arrives; that is raised
as :class:`ConnectionError` with ``stale`` set, so the caller can try once more on a new
connection. Nothing is retried here.
"""

import asyncio

from arthur.wire import http, net

USER_AGENT = "arthur-clock"


class ConnectionError(Exception):
    """The request failed without an answer. ``stale``: it was sent on a kept connection
    that turned out to be closed, before any byte of an answer came back. ``timeout``: it ran
    past the time limit."""

    def __init__(self, message, stale=False, timeout=False):
        super().__init__(message)
        self.message = message
        self.stale = stale
        self.timeout = timeout

    def __str__(self):
        return self.message


class Answer:
    """``status`` and ``body`` (bytes) of one answer; ``headers`` with lower-case names."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def text(self):
        return self.body.decode()


class HttpConnection:
    """Requests to ``base_url`` (``http://host[:port]`` or ``https://...``) over one kept
    connection. ``tls`` is an :class:`arthur.wire.net.Tls`; plain ``http://`` needs its
    ``allow_plain`` (the clock's own API on 127.0.0.1)."""

    def __init__(self, base_url, tls, timeout_s=5.0, user_agent=USER_AGENT,
                 max_body=http.MAX_BODY):
        self.url = net.Url(base_url.rstrip("/"))
        self.base = base_url.rstrip("/")
        self.tls = tls
        self.timeout_s = timeout_s
        self.user_agent = user_agent
        self.max_body = max_body
        self._reader = None
        self._writer = None
        #: Requests sent on the current connection.
        self._used = 0
        #: Connections opened, and how many the server ended with ``Connection: close``.
        self.opened = 0
        self.server_closed = 0

    @property
    def is_open(self):
        return self._writer is not None

    def head(self, method, path, length=None, headers=None):
        """The request line and headers, as bytes, ending with the blank line."""
        lines = [method + " " + path + " HTTP/1.1",
                 "Host: " + self.url.host_header(),
                 "User-Agent: " + self.user_agent]
        if headers:
            for name in headers:
                lines.append(name + ": " + headers[name])
        if length is not None:
            lines.append("Content-Length: " + str(length))
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    async def open(self):
        """Open a connection if none is held. True if one was opened."""
        if self._writer is not None:
            return False
        try:
            self._reader, self._writer = await net.open_stream(self.url, self.tls,
                                                               self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - every failure is a ConnectionError
            self._reader = self._writer = None
            raise ConnectionError(self.base + ": cannot connect: " + _describe(exc),
                                  timeout=_is_timeout(exc)) from None
        self._used = 0
        self.opened += 1
        return True

    async def close(self):
        writer = self._writer
        self._reader = self._writer = None
        if writer is not None:
            await net.close_stream(writer)

    async def send(self, data, label):
        """Send ``data`` (a whole request, head and body) and read the answer. ``label``
        names the request in errors (``"PUT /api/v1/..."``)."""
        await self.open()
        reused = self._used > 0
        self._used += 1
        state = [False]  # an answer's status line has arrived
        try:
            answer = await asyncio.wait_for(self._exchange(data, state), self.timeout_s)
        except Exception as exc:
            await self.close()
            if isinstance(exc, ConnectionError):
                raise
            timeout = _is_timeout(exc)
            if timeout:
                raise ConnectionError(label + " timed out after " + str(self.timeout_s) + " s",
                                      timeout=True) from None
            stale = reused and not state[0]
            raise ConnectionError(label + ": " + _describe(exc), stale=stale) from None
        if _closes(answer.headers):
            self.server_closed += 1
            await self.close()
        return answer

    async def _exchange(self, data, state):
        await net.write_all(self._writer, data)
        status, _reason, headers = await http.read_head(self._reader)
        state[0] = True
        body = await http.read_body(self._reader, headers, self.max_body)
        return Answer(status, headers, body)

    async def request(self, method, path, body=None, headers=None):
        """One request with an optional ``body`` (str or bytes); returns the :class:`Answer`
        for any status."""
        if isinstance(body, str):
            body = body.encode()
        head = self.head(method, path, None if body is None else len(body), headers)
        return await self.send(head + body if body is not None else head,
                               method + " " + self.base + path.split("?")[0])


def _closes(headers):
    """The server will close after this answer: ``Connection: close``, or a body that ran
    to the end of the stream."""
    if "close" in headers.get("connection", "").lower():
        return True
    return "content-length" not in headers and \
        "chunked" not in headers.get("transfer-encoding", "").lower()


def _is_timeout(exc):
    return type(exc).__name__ in ("TimeoutError", "CancelledTimeout")


def _describe(exc):
    text = " ".join(str(exc).split())
    name = type(exc).__name__
    return name + ": " + text if text else name
