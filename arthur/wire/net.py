"""URLs, verified TLS and stream handling on both Pythons.

TLS always verifies. CPython's ``ssl.create_default_context`` does so by default. MicroPython's
``ssl.SSLContext(PROTOCOL_TLS_CLIENT)`` starts with ``verify_mode = CERT_NONE`` (read in its
``extmod/modtls_mbedtls.c``; confirmed by the clock spike, ``spike/mpy/clock/device/tls.py``), so
it is set to ``CERT_REQUIRED`` here and checked. Both take the server name the same way:
``asyncio.open_connection(host, port, ssl=ctx, server_hostname=host)`` (MicroPython 1.29
``extmod/asyncio/stream.py``; it defaults ``server_hostname`` to ``host``). On MicroPython the
handshake runs on the first read or write, not inside ``open_connection``, so a certificate
failure surfaces there.

Plain ``http://`` and ``ws://`` are refused unless the caller passes ``allow_plain=True``,
which only the tests do.
"""

import asyncio
import ssl

from arthur.wire import errors
from arthur.wire.util import IS_MPY


class Url:
    """scheme://host[:port]/path?query, split. ``secure`` for https and wss."""

    def __init__(self, text):
        scheme, sep, rest = text.partition("://")
        if not sep:
            raise ValueError("not a URL: " + text)
        scheme = scheme.lower()
        if scheme not in ("http", "https", "ws", "wss"):
            raise ValueError("unsupported scheme: " + scheme)
        self.scheme = scheme
        self.secure = scheme in ("https", "wss")
        slash = rest.find("/")
        query = rest.find("?")
        cut = len(rest)
        for i in (slash, query):
            if 0 <= i < cut:
                cut = i
        hostport, path = rest[:cut], rest[cut:]
        if not path.startswith("/"):
            path = "/" + path
        self.path = path
        host, colon, port = hostport.rpartition(":")
        if colon and port.isdigit() and "]" not in port:
            self.host, self.port = host, int(port)
        else:
            self.host, self.port = hostport, (443 if self.secure else 80)
        if self.host.startswith("[") and self.host.endswith("]"):
            self.host = self.host[1:-1]
        if not self.host:
            raise ValueError("no host in URL: " + text)

    def host_header(self):
        default = 443 if self.secure else 80
        host = "[" + self.host + "]" if ":" in self.host else self.host
        return host if self.port == default else host + ":" + str(self.port)


class Tls:
    """How to open connections: a CA file for verification (on the clock
    ``/tmp/awtrix-release/share/ca-certificates.crt``), or CPython's system store when none is
    given. ``allow_plain`` lets tests use http:// and ws://. The context is built once and kept:
    loading the clock's bundle takes 0.4 s and 435 KB of heap there."""

    def __init__(self, ca_file=None, allow_plain=False, server_name=None):
        self.ca_file = ca_file
        self.allow_plain = allow_plain
        #: Tests only: check the certificate against this name instead of the URL's host.
        self.server_name = server_name
        self._ctx = None

    def context(self):
        if self._ctx is not None:
            return self._ctx
        if IS_MPY:
            if not self.ca_file:
                raise errors.api_error("TLS needs a CA file on MicroPython")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.verify_mode = ssl.CERT_REQUIRED
            if ctx.verify_mode != ssl.CERT_REQUIRED:
                raise errors.api_error("TLS verification could not be switched on")
            ctx.load_verify_locations(cafile=self.ca_file)
        else:
            ctx = ssl.create_default_context(cafile=self.ca_file)
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
        self._ctx = ctx
        return ctx


async def open_stream(url, tls, timeout):
    """(reader, writer) for a Url. Plain only if ``tls.allow_plain``."""
    if url.secure:
        ctx = tls.context()
        name = tls.server_name or url.host
        coro = asyncio.open_connection(url.host, url.port, ssl=ctx, server_hostname=name)
    else:
        if not tls.allow_plain:
            raise errors.api_error("refusing a plain connection to " + url.host
                                   + " (TLS is required outside tests)")
        coro = asyncio.open_connection(url.host, url.port)
    return await asyncio.wait_for(coro, timeout)


def close_now(writer):
    """Close a stream's socket without awaiting, never raising. (MicroPython's
    ``Stream.close()`` does nothing; its socket is ``writer.s``.)"""
    try:
        if IS_MPY:
            writer.s.close()
        else:
            writer.close()
    except Exception:  # noqa: BLE001, S110 - closing must never raise
        pass


async def close_stream(writer):
    """Close on either Python, never raising. (MicroPython's ``close()`` does nothing and
    ``wait_closed()`` closes the socket; CPython needs both.) On MicroPython the socket is
    closed first without awaiting: in a cancelled task's ``finally`` an await can raise
    again, which would leave the socket open."""
    if IS_MPY:
        close_now(writer)
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:  # noqa: BLE001, S110 - closing must never raise; no contextlib on MicroPython
        pass


async def write_all(writer, data):
    writer.write(data)
    await writer.drain()
