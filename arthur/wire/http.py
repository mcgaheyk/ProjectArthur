"""A small HTTP/1.1 client over asyncio streams, for both Pythons.

One request per connection (``Connection: close``): our requests are minutes apart, so a kept
connection would be closed by the server long before the next one. The reader still handles
every way a body can be framed: ``Content-Length``, ``Transfer-Encoding: chunked``, and a body
that ends when the server closes. We ask for ``Accept-Encoding: identity`` and refuse a
compressed answer rather than carry a decompressor.
"""

import asyncio
import json

from arthur.wire import errors, net
from arthur.wire.const import REQUEST_TIMEOUT_S, USER_AGENT

#: The largest body we will read. A robot answer is about 4 KB and a history page of 25 about
#: 8 KB; anything near this is not what we asked for.
MAX_BODY = 256 * 1024
MAX_HEADER_LINE = 8192
MAX_HEADERS = 100


class Response:
    def __init__(self, status, reason, headers, body):
        self.status = status
        self.reason = reason
        self.headers = headers  # lower-case names
        self.body = body

    def json(self):
        """The body as JSON, or an ``api`` WireError naming the status."""
        try:
            return json.loads(self.body)
        except (ValueError, TypeError) as exc:
            raise errors.api_error(f"HTTP {self.status} with a body that is not JSON "
                                   f"({type(exc).__name__})", self.status) from None


async def _readline(reader):
    line = await reader.readline()
    if len(line) > MAX_HEADER_LINE:
        raise errors.api_error("HTTP header line too long")
    return line


async def read_head(reader):
    """Status line and headers. Returns (status, reason, headers)."""
    line = await _readline(reader)
    if not line:
        raise errors.api_error("connection closed before an HTTP answer")
    parts = line.decode().strip().split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1.") or not parts[1].isdigit():
        raise errors.api_error("not an HTTP answer")
    status = int(parts[1])
    reason = parts[2] if len(parts) > 2 else ""
    headers = {}
    count = 0
    while True:
        line = await _readline(reader)
        if not line:
            raise errors.api_error("connection closed inside the HTTP headers")
        if line in (b"\r\n", b"\n"):
            break
        count += 1
        if count > MAX_HEADERS:
            raise errors.api_error("too many HTTP headers")
        name, sep, value = line.decode().partition(":")
        if not sep:
            raise errors.api_error("bad HTTP header line")
        name = name.strip().lower()
        value = value.strip()
        if name in headers:
            headers[name] = headers[name] + ", " + value
        else:
            headers[name] = value
    return status, reason, headers


async def _read_exactly(reader, n):
    try:
        return await reader.readexactly(n)
    except EOFError:
        raise errors.api_error("connection closed inside the HTTP body") from None
    except Exception as exc:  # CPython raises asyncio.IncompleteReadError
        if type(exc).__name__ == "IncompleteReadError":
            raise errors.api_error("connection closed inside the HTTP body") from None
        raise


async def read_body(reader, headers, max_body=MAX_BODY):
    encoding = headers.get("content-encoding", "identity").lower()
    if encoding not in ("identity", ""):
        raise errors.api_error("compressed HTTP answer (" + encoding + ") not supported")
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = bytearray()
        while True:
            line = await _readline(reader)
            if not line:
                raise errors.api_error("connection closed inside a chunked body")
            size_text = line.decode().split(";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError:
                raise errors.api_error("bad chunk size") from None
            if size == 0:
                while True:  # trailers, then the blank line
                    line = await _readline(reader)
                    if line in (b"\r\n", b"\n", b""):
                        break
                return bytes(body)
            if len(body) + size > max_body:
                raise errors.api_error("HTTP body too large")
            body.extend(await _read_exactly(reader, size))
            await _read_exactly(reader, 2)  # CRLF after each chunk
    length = headers.get("content-length")
    if length is not None:
        try:
            n = int(length)
        except ValueError:
            raise errors.api_error("bad Content-Length") from None
        if n < 0 or n > max_body:
            raise errors.api_error("HTTP body too large")
        return await _read_exactly(reader, n) if n else b""
    body = bytearray()  # ends when the server closes
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            return bytes(body)
        if len(body) + len(chunk) > max_body:
            raise errors.api_error("HTTP body too large")
        body.extend(chunk)


async def request(method, url, tls, headers=None, body=None, timeout=REQUEST_TIMEOUT_S,
                  max_body=MAX_BODY):
    """One request; returns a :class:`Response` for any status. Network failures raise
    ``api``; running past ``timeout`` (connect to last byte) raises ``timeout``."""
    try:
        return await asyncio.wait_for(
            _request(method, url, tls, headers, body, timeout, max_body), timeout)
    except errors.WireError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure becomes a classified WireError
        where = method + " " + url.split("?")[0]
        if errors.kind_of(exc) == errors.TIMEOUT:
            raise errors.WireError(errors.TIMEOUT, f"{where} took longer than {timeout}s") \
                from None
        raise errors.api_error(f"{where} failed: {type(exc).__name__}: {exc}") from None


async def _request(method, url_text, tls, headers, body, timeout, max_body):
    url = net.Url(url_text)
    if isinstance(body, str):
        body = body.encode()
    lines = [
        method + " " + url.path + " HTTP/1.1",
        "Host: " + url.host_header(),
        "User-Agent: " + USER_AGENT,
        "Accept: application/json",
        "Accept-Encoding: identity",
        "Connection: close",
    ]
    for name in headers or {}:
        lines.append(name + ": " + headers[name])
    if body is not None:
        lines.append("Content-Length: " + str(len(body)))
    head = ("\r\n".join(lines) + "\r\n\r\n").encode()
    reader, writer = await net.open_stream(url, tls, timeout)
    try:
        if body is not None and len(body) < 16384:
            await net.write_all(writer, head + body)
        else:
            await net.write_all(writer, head)
            if body is not None:
                await net.write_all(writer, body)
        status, reason, rheaders = await read_head(reader)
        data = await read_body(reader, rheaders, max_body)
        return Response(status, reason, rheaders, data)
    finally:
        await net.close_stream(writer)
