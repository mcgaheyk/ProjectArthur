"""A TCP relay for the emulated clock (tests only; MicroPython or CPython).

    micropython tcprelay.py LISTEN_PORT HOST PORT

The clock's transport talks to AWTRIX on 127.0.0.1 only (it refuses any other host), so in
the emulated container the fake clock on the Mac is reached through this: it listens on the
container's 127.0.0.1 and copies bytes both ways to ``HOST:PORT``.
"""

import asyncio
import sys


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:  # noqa: BLE001, S110 - a relay just stops
        pass
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:  # noqa: BLE001, S110
        pass


def main(listen, host, port):
    async def handle(r1, w1):
        try:
            r2, w2 = await asyncio.open_connection(host, port)
        except OSError:
            w1.close()
            return
        asyncio.create_task(pipe(r1, w2))
        await pipe(r2, w1)

    async def run():
        await asyncio.start_server(handle, "127.0.0.1", listen)
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run())


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
