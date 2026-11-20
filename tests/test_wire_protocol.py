"""The HTTP and WebSocket pieces of the wire client against hand-written servers that send
exactly the bytes each case needs: chunked and close-delimited bodies, fragmented messages
with control frames between them, 16- and 64-bit lengths, close frames, bad handshakes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct

import pytest

from arthur.wire import errors, http, ws
from arthur.wire.net import Tls

pytestmark = pytest.mark.usefixtures("loopback_only")
PLAIN = Tls(allow_plain=True)


class RawServer:
    """A one-handler TCP server on 127.0.0.1."""

    def __init__(self, handler):
        self.handler = handler
        self.server = None
        self.port = 0
        self.received = bytearray()

    async def __aenter__(self):
        async def on_conn(reader, writer):
            try:
                await self.handler(reader, writer, self)
            finally:
                writer.close()

        self.server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()

    def url(self, scheme="http", path="/x"):
        return f"{scheme}://127.0.0.1:{self.port}{path}"


async def read_request(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    body = b""
    if "content-length" in headers:
        body = await reader.readexactly(int(headers["content-length"]))
    return lines[0], headers, body


def answer(*parts: bytes):
    async def handler(reader, writer, srv):
        line, headers, body = await read_request(reader)
        srv.request = (line, headers, body)
        for p in parts:
            writer.write(p)
            await writer.drain()
    return handler


# -- HTTP ------------------------------------------------------------------------------------


async def test_content_length_body_and_request_shape():
    async with RawServer(answer(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n"
                                b"X-A: 1\r\nX-A: 2\r\n\r\n{\"a\": true}")) as srv:
        resp = await http.request("POST", srv.url(), PLAIN, headers={"Content-Type": "a/b"},
                                  body='{"q": 1}')
    assert resp.status == 200 and resp.json() == {"a": True}
    assert resp.headers["x-a"] == "1, 2"
    line, headers, body = srv.request
    assert line == "POST /x HTTP/1.1"
    assert headers["host"] == f"127.0.0.1:{srv.port}"
    assert headers["connection"] == "close"
    assert headers["content-length"] == "8" and body == b'{"q": 1}'


async def test_chunked_body_with_extensions_and_trailers():
    async with RawServer(answer(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"4;ext=1\r\n{\"a\"\r\n", b"5\r\n: 12}\r\n", b"0\r\nTrailer: x\r\n\r\n")) as srv:
        resp = await http.request("GET", srv.url(), PLAIN)
    assert resp.json() == {"a": 12}


async def test_body_until_close():
    async with RawServer(answer(b"HTTP/1.1 200 OK\r\n\r\n", b"[1,", b"2]")) as srv:
        resp = await http.request("GET", srv.url(), PLAIN)
    assert resp.json() == [1, 2]


@pytest.mark.parametrize("reply, text", [
    (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 2\r\n\r\nxx", "compressed"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n", "too large"),
    (b"SSH-2.0-OpenSSH\r\n\r\n", "not an HTTP answer"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc", "closed inside the HTTP body"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n", "bad chunk size"),
    (b"", "closed before"),
])
async def test_broken_answers_are_api(reply, text):
    async with RawServer(answer(reply)) as srv:
        with pytest.raises(errors.WireError) as info:
            await http.request("GET", srv.url(), PLAIN)
    assert info.value.kind == errors.API
    assert text in str(info.value)


async def test_a_silent_server_is_a_timeout():
    async def handler(reader, writer, srv):
        await asyncio.sleep(5)

    async with RawServer(handler) as srv:
        with pytest.raises(errors.WireError) as info:
            await http.request("GET", srv.url(), PLAIN, timeout=0.2)
    assert info.value.kind == errors.TIMEOUT


# -- WebSocket -------------------------------------------------------------------------------


def server_frame(opcode, payload, fin=True):
    first = (0x80 if fin else 0) | opcode
    n = len(payload)
    if n < 126:
        head = struct.pack(">BB", first, n)
    elif n < 65536:
        head = struct.pack(">BBH", first, 126, n)
    else:
        head = struct.pack(">BBQ", first, 127, n)
    return head + payload


async def read_client_frame(reader):
    b0, b1 = struct.unpack(">BB", await reader.readexactly(2))
    assert b1 & 0x80, "client frames must be masked"
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    key = await reader.readexactly(4)
    data = bytes(ws.mask(await reader.readexactly(n), key))
    return b0 & 0x0F, data


def upgrade(accept=None, extra=b"", status=b"101 Switching Protocols"):
    async def handshake(reader, writer):
        _, headers, _ = await read_request(reader)
        key = headers["sec-websocket-key"]
        good = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        writer.write(b"HTTP/1.1 " + status + b"\r\nUpgrade: websocket\r\n"
                     b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
                     + (accept or good).encode() + b"\r\nSec-WebSocket-Protocol: graphql-ws\r\n"
                     + extra + b"\r\n")
        await writer.drain()
        return headers
    return handshake


def test_mask_matches_rfc_example():
    """RFC 6455 section 5.7: a masked 'Hello'."""
    frame = ws.encode_frame(ws.TEXT, b"Hello", mask_key=b"\x37\xfa\x21\x3d")
    assert frame == bytes.fromhex("818537fa213d7f9f4d5158")


async def test_fragments_with_a_ping_between_and_long_lengths():
    big = json.dumps({"x": "y" * 70_000}).encode()
    mid = b"z" * 300

    async def handler(reader, writer, srv):
        srv.headers = await upgrade()(reader, writer)
        writer.write(server_frame(ws.TEXT, b'{"part": ', fin=False)
                     + server_frame(ws.PING, b"are you there")
                     + server_frame(ws.CONT, b'"one"', fin=False)
                     + server_frame(ws.CONT, b"}")
                     + server_frame(ws.TEXT, mid) + server_frame(ws.BINARY, big))
        await writer.drain()
        srv.pong = await read_client_frame(reader)
        srv.sent = await read_client_frame(reader)
        srv.sent_big = await read_client_frame(reader)
        writer.write(server_frame(ws.CLOSE, struct.pack(">H", 1001) + b"bye"))
        await writer.drain()
        srv.close = await read_client_frame(reader)

    async with RawServer(handler) as srv:
        sock = await ws.WebSocket.connect(srv.url("ws"), PLAIN, subprotocol="graphql-ws")
        assert await sock.recv() == (ws.TEXT, b'{"part": "one"}')
        assert await sock.recv() == (ws.TEXT, mid)
        assert await sock.recv() == (ws.BINARY, big)
        await sock.send_text("x" * 10)
        await sock.send_text("y" * 70_000)
        with pytest.raises(ws.Closed) as info:
            await sock.recv()
        assert info.value.code == 1001
        await asyncio.sleep(0.05)
    assert srv.headers["sec-websocket-version"] == "13"
    assert srv.headers["sec-websocket-protocol"] == "graphql-ws"
    assert srv.pong == (ws.PONG, b"are you there")
    assert srv.sent == (ws.TEXT, b"x" * 10)
    assert srv.sent_big == (ws.TEXT, b"y" * 70_000)
    assert srv.close == (ws.CLOSE, struct.pack(">H", 1001))


@pytest.mark.parametrize("hs, text", [
    (upgrade(accept="wrongwrongwrongwrongwrongwr="), "wrong accept key"),
    (upgrade(extra=b"Sec-WebSocket-Extensions: permessage-deflate\r\n"), "extension"),
    (upgrade(status=b"200 OK"), "upgrade refused"),
])
async def test_bad_handshakes_are_api(hs, text):
    async def handler(reader, writer, srv):
        await hs(reader, writer)
        await asyncio.sleep(0.1)

    async with RawServer(handler) as srv:
        with pytest.raises(errors.WireError) as info:
            await ws.WebSocket.connect(srv.url("ws"), PLAIN, subprotocol="graphql-ws")
    assert info.value.kind == errors.API
    assert text in str(info.value)


async def test_a_subprotocol_we_did_not_ask_for_is_api():
    async def handler(reader, writer, srv):
        await upgrade()(reader, writer)
        await asyncio.sleep(0.1)

    async with RawServer(handler) as srv:
        with pytest.raises(errors.WireError, match="chose subprotocol"):
            await ws.WebSocket.connect(srv.url("ws"), PLAIN)


async def test_a_401_upgrade_is_auth():
    async with RawServer(answer(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 2\r\n\r\n{}")) \
            as srv:
        with pytest.raises(errors.WireError) as info:
            await ws.WebSocket.connect(srv.url("ws"), PLAIN)
    assert info.value.kind == errors.AUTH


async def test_a_dropped_connection_is_closed():
    async def handler(reader, writer, srv):
        await upgrade()(reader, writer)
        writer.write(server_frame(ws.TEXT, b"hello")[:3])
        await writer.drain()

    async with RawServer(handler) as srv:
        sock = await ws.WebSocket.connect(srv.url("ws"), PLAIN, subprotocol="graphql-ws")
        with pytest.raises(ws.Closed):
            await sock.recv()
