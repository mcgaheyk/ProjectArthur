"""The simulator's HTTP server: one fake AWTRIX NG clock (a 52x16 Ulanzi TC002), the studio
API and the studio page.

Routes (the clock answers at the bare paths, like a real one, so
``CLOCK_URL=http://localhost:7001`` works):

Clock (AWTRIX NG 1.2.3 subset)
  GET     /api/v1/apps                 the rotation: built-ins Time and Status, pushed apps
  PUT     /api/v1/apps/order           {"order"?, "disabled"}; other methods → 405
  PUT     /api/v1/apps/pushed/{name}   other methods → 405 methodNotAllowed
  DELETE  /api/v1/apps/{name}
  PUT     /api/v1/indicators/{id}
  DELETE  /api/v1/indicators/{id}
  GET     /api/v1/display/screen
  GET     /api/v1/settings             only {"enlargeApps"}; other methods → 405
  PATCH   /api/v1/settings             only enlargeApps; other keys → 422 notSimulated
  *       /api/v1/...                  → 501 notSimulated (including settings/reset)

Studio
  GET  /                      the page
  GET  /static/{path}         page scripts and styles
  GET  /studio/meta           kinds, sizes, variants, fixtures, scenarios, palette, timings
  GET  /studio/gallery        every fixture x variant, each a loop of up to LOOP_S one-second
                              frames (?stale=1 overlays the border on every frame)
  GET  /studio/state          what is showing, feeder pushes and errors, the clock's lifetime
  POST /studio/control        change the source, playback, variants, feeder switches, and the
                              robot log ({"mode": "log", "log": {"sub_mode": "follow"|"replay",
                              "visit", "position", "speed", "playing"}})
  GET  /studio/png            ?fixture=&size=&variant=&scale=&gap=&stale=
  GET  /studio/ws             WebSocket: the clock's pixels and the state when they change

The server binds to 127.0.0.1 only (see ``__main__``).

Some studio plumbing (the ``clocks`` map, ``variants``, gallery ``tiles``) is still keyed by the
size string, "52x16", with that one entry.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from arthur.sim.clock import ClockError, FakeClock, not_simulated
from arthur.sim.feeder import (
    FAST_HEARTBEAT_S,
    FAST_TTL_S,
    LITTER_STATES,
    LOG_SUB_MODES,
    MANUAL_DEFAULTS,
    MODES,
    POWER_TYPES,
    SPEEDS,
    WAIT_MINUTES,
    ControlError,
    StudioFeeder,
)
from arthur.sim.pipeline import LOOP_S, ReloadingPipeline, size_key

log = logging.getLogger("arthur.sim")

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 3 * 1024 * 1024  # above the clock's limit (2 MiB) so the clock decides

PIPE = web.AppKey("pipe", ReloadingPipeline)
CLOCKS = web.AppKey("clocks", dict)
FEEDER = web.AppKey("feeder", StudioFeeder)
SOCKETS = web.AppKey("sockets", set)
GALLERY_CACHE = web.AppKey("gallery_cache", dict)
OPTIONS = web.AppKey("options", dict)

NO_CACHE = {"Cache-Control": "no-store"}
CORS = {"Access-Control-Allow-Origin": "*"}
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


def _json(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> web.Response:
    return web.json_response(data, status=status, headers=headers,
                             dumps=lambda d: json.dumps(d, separators=(",", ":")))


def _clock_error(err: ClockError) -> web.Response:
    return _json(err.body(), err.status, CORS)


# =========================================================================================
# Clock routes
# =========================================================================================


def _clock_for(request: web.Request) -> FakeClock:
    """The one simulated clock."""
    clocks: dict[str, FakeClock] = request.app[CLOCKS]
    return next(iter(clocks.values()))


async def _read_body(request: web.Request, clock: FakeClock) -> bytes:
    if request.content_length is not None and request.content_length > clock.body_limit:
        raise ClockError(413, "payloadTooLarge",
                         f"body is {request.content_length} bytes; this clock takes at most "
                         f"{clock.body_limit}")
    try:
        return await request.read()
    except web.HTTPRequestEntityTooLarge as exc:
        raise ClockError(413, "payloadTooLarge",
                         f"this clock takes at most {clock.body_limit} bytes") from exc


def _preflight(request: web.Request) -> web.Response | None:
    if request.method == "OPTIONS":  # CORS preflight on any path answers 204
        return web.Response(status=204, headers={
            **CORS, "Access-Control-Allow-Methods": "GET, PUT, POST, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type"})
    return None


def _method_not_allowed(allowed: str) -> ClockError:
    return ClockError(405, "methodNotAllowed", f"this route takes {allowed} only")


async def h_pushed(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        if request.method != "PUT":
            raise _method_not_allowed("PUT")
        name = request.match_info["name"]
        clock.check_name(name)  # checked before the body is read
        raw = await _read_body(request, clock)
        ctype = request.headers.get("Content-Type")
        clock.push(name, raw, ctype)
    except ClockError as err:
        return _clock_error(err)
    return _json({"ok": True}, headers=CORS)


async def h_apps(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        if request.method != "GET":
            raise _method_not_allowed("GET")
    except ClockError as err:
        return _clock_error(err)
    return _json(clock.apps_list(), headers=CORS)


async def h_order(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        if request.method != "PUT":
            raise _method_not_allowed("PUT")
        raw = await _read_body(request, clock)
        clock.set_order(raw, request.headers.get("Content-Type"))
    except ClockError as err:
        return _clock_error(err)
    return _json({"ok": True}, headers=CORS)


async def h_app(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        if request.method != "DELETE":
            raise not_simulated(f"{request.method} {request.path}", status=501)
        clock.delete_app(request.match_info["name"])
    except ClockError as err:
        return _clock_error(err)
    return _json({"ok": True}, headers=CORS)


async def h_indicator(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        ident = request.match_info["id"]
        if request.method == "PUT":
            clock._indicator_id(ident)
            raw = await _read_body(request, clock)
            clock.set_indicator(ident, raw, request.headers.get("Content-Type"))
        elif request.method == "DELETE":
            clock.clear_indicator(ident)
        else:
            raise _method_not_allowed("PUT and DELETE")
    except ClockError as err:
        return _clock_error(err)
    return _json({"ok": True}, headers=CORS)


async def h_screen(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        if request.method != "GET":
            raise _method_not_allowed("GET")
    except ClockError as err:
        return _clock_error(err)
    return _json(clock.screen_json(), headers=CORS)


async def h_settings(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    try:
        clock = _clock_for(request)
        if request.method == "GET":
            return _json(clock.settings(), headers=CORS)
        if request.method != "PATCH":
            raise _method_not_allowed("GET and PATCH")
        raw = await _read_body(request, clock)
        result = clock.patch_settings(raw, request.headers.get("Content-Type"))
    except ClockError as err:
        return _clock_error(err)
    return _json(result, headers=CORS)


async def h_not_simulated(request: web.Request) -> web.Response:
    if (r := _preflight(request)) is not None:
        return r
    err = ClockError(
        501, "notSimulated",
        f"{request.method} {request.path} is NOT SIMULATED by the ProjectArthur fake clock "
        "(it may or may not exist on a real AWTRIX NG 1.2.3 clock). Simulated: GET "
        "/api/v1/apps, PUT /api/v1/apps/order, PUT /api/v1/apps/pushed/{name}, DELETE /api/v1/apps/{name}, PUT|DELETE "
        "/api/v1/indicators/{id}, GET /api/v1/display/screen, GET|PATCH /api/v1/settings "
        "(enlargeApps only).",
    )
    return _clock_error(err)


def _add_clock_routes(app: web.Application) -> None:
    r = app.router
    r.add_route("*", "/api/v1/apps", h_apps)
    r.add_route("*", "/api/v1/apps/order", h_order)  # before /apps/{name}, which would match
    r.add_route("*", "/api/v1/apps/pushed/{name}", h_pushed)
    r.add_route("*", "/api/v1/apps/{name}", h_app)
    r.add_route("*", "/api/v1/indicators/{id}", h_indicator)
    r.add_route("*", "/api/v1/display/screen", h_screen)
    r.add_route("*", "/api/v1/settings", h_settings)
    r.add_route("*", "/api/v1", h_not_simulated)
    r.add_route("*", "/api/v1/{tail:.*}", h_not_simulated)


# =========================================================================================
# Studio
# =========================================================================================


async def h_index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB_DIR / "index.html", headers={
        **NO_CACHE, "Content-Type": "text/html; charset=utf-8"})


async def h_static(request: web.Request) -> web.StreamResponse:
    rel = request.match_info["path"]
    p = (WEB_DIR / rel).resolve()
    if WEB_DIR not in p.parents or not p.is_file():
        raise web.HTTPNotFound(text="not found")
    ctype = CONTENT_TYPES.get(p.suffix, "application/octet-stream")
    if ctype.startswith("text/"):
        ctype += "; charset=utf-8"
    return web.FileResponse(p, headers={**NO_CACHE, "Content-Type": ctype})


def _meta(app: web.Application) -> dict[str, Any]:
    pipe = app[PIPE]
    meta = pipe.current.meta()
    meta.update(
        modes=list(MODES),
        speeds=list(SPEEDS),
        fast_expiry={"heartbeat_s": FAST_HEARTBEAT_S, "ttl_s": FAST_TTL_S},
        manual_defaults=MANUAL_DEFAULTS,
        litter_states=list(LITTER_STATES),
        power_types=list(POWER_TYPES),
        wait_minutes=list(WAIT_MINUTES),
        log_sub_modes=list(LOG_SUB_MODES),
        reload_error=pipe.error,
    )
    return meta


async def h_meta(request: web.Request) -> web.Response:
    return _json(_meta(request.app), headers=NO_CACHE)


def _encode_loop(frames: list[Any]) -> dict[str, Any]:
    """A tile's loop, compactly: each distinct frame once as base64 RGB888 (as the transport
    sends it), and ``seq``, the index of the frame to show at each second of the loop."""
    distinct: dict[bytes, int] = {}
    seq = [distinct.setdefault(f.to_rgb888(), len(distinct)) for f in frames]
    return {"frames": [base64.b64encode(b).decode("ascii") for b in distinct], "seq": seq}


def build_gallery(app: web.Application, stale: bool) -> dict[str, Any]:
    pipe = app[PIPE].current
    key = (pipe.version, stale)
    cache: dict = app[GALLERY_CACHE]
    if key in cache:
        return cache[key]
    rows = []
    for fx in pipe.fixtures.FIXTURES.values():
        tiles: dict[str, list[dict[str, Any]]] = {}
        try:
            kind = pipe.state(fx.snapshot, fx.health, fx.now).kind
        except Exception as exc:  # noqa: BLE001 - show the error in the page
            rows.append({"fixture": fx.name, "kind": fx.kind, "description": fx.description,
                         "error": f"{exc.__class__.__name__}: {exc}", "tiles": {}})
            continue
        for size in pipe.sizes():
            sk = size_key(size)
            out = []
            names = pipe.variants(kind, size)
            for i, v in enumerate(names):
                tile: dict[str, Any] = {"variant": v, "default": i == 0}
                try:
                    tile.update(_encode_loop(pipe.fixture_loop(fx.name, size, v, stale)))
                except Exception as exc:  # noqa: BLE001 - show the error on the tile
                    tile["error"] = f"{exc.__class__.__name__}: {exc}"
                out.append(tile)
            tiles[sk] = out
        rows.append({"fixture": fx.name, "kind": kind, "description": fx.description,
                     "tiles": tiles})
    result = {"version": pipe.version, "stale": stale, "loop_s": LOOP_S,
              "encoding": "rgb888-base64",
              "sizes": [size_key(s) for s in pipe.sizes()], "rows": rows}
    if len(cache) > 4:
        cache.clear()
    cache[key] = result
    return result


def _flag(request: web.Request, name: str) -> bool:
    return request.query.get(name, "0").lower() in ("1", "true", "yes", "on")


async def h_gallery(request: web.Request) -> web.Response:
    resp = _json(build_gallery(request.app, _flag(request, "stale")), headers=NO_CACHE)
    resp.enable_compression()  # the loops repeat a lot; gzip when the browser accepts it
    return resp


async def h_png(request: web.Request) -> web.Response:
    pipe = request.app[PIPE].current
    q = request.query
    name = q.get("fixture", "")
    if name not in pipe.fixtures.FIXTURES:
        raise web.HTTPBadRequest(text=f"unknown fixture {name!r}")
    sizes = {size_key(s): s for s in pipe.sizes()}
    sk = q.get("size", size_key(pipe.cfg.size))
    if sk not in sizes:
        raise web.HTTPBadRequest(text=f"size must be one of {', '.join(sizes)}")
    try:
        scale = int(q.get("scale", "10"))
        gap = int(q.get("gap", "1"))
    except ValueError as exc:
        raise web.HTTPBadRequest(text="scale and gap must be integers") from exc
    if not 1 <= scale <= 40 or gap < 0:
        raise web.HTTPBadRequest(text="scale must be 1..40 and gap >= 0")
    frame = pipe.fixture_frame(name, sizes[sk], q.get("variant") or None)
    if _flag(request, "stale"):
        frame = pipe.overlay_stale(frame)
    return web.Response(body=frame.to_png(scale, gap=gap), content_type="image/png",
                        headers=NO_CACHE)


def studio_state(app: web.Application) -> dict[str, Any]:
    pipe = app[PIPE]
    feeder = app[FEEDER]
    st = feeder.state()
    clocks = {}
    for sk, clock in app[CLOCKS].items():
        cs = clock.status()
        push = st["pushes"].get(sk, {})
        cs["kind"] = push.get("kind")
        cs["variant"] = push.get("variant")
        cs["feeder_push_count"] = push.get("push_count", 0)
        cs["feeder_error"] = push.get("last_error")
        clocks[sk] = cs
    st.update(
        version=pipe.current.version,
        reload_error=pipe.error,
        default_size=size_key(pipe.current.cfg.size),
        clocks=clocks,
        server_time=time.time(),
    )
    return st


async def h_state(request: web.Request) -> web.Response:
    return _json(studio_state(request.app), headers=NO_CACHE)


async def h_control(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return _json({"error": "body must be JSON"}, 400)
    feeder = request.app[FEEDER]
    try:
        feeder.apply(body)
    except ControlError as exc:
        return _json({"error": str(exc)}, 400)
    await feeder.tick()  # push straight away so the page reacts at once
    await broadcast(request.app, force_state=True)
    return _json(studio_state(request.app), headers=NO_CACHE)


# --- WebSocket --------------------------------------------------------------------------


def _clock_pixels(app: web.Application) -> dict[str, dict[str, Any]]:
    return {sk: c.screen_json() for sk, c in app[CLOCKS].items()}


class _Broadcast:
    def __init__(self) -> None:
        self.last_pixels: dict[str, list[int]] = {}
        self.last_state_t = 0.0


async def _send(ws: web.WebSocketResponse, msg: dict[str, Any]) -> bool:
    try:
        await ws.send_str(json.dumps(msg, separators=(",", ":")))
        return True
    except (ConnectionError, RuntimeError):
        return False


async def broadcast(app: web.Application, *, force_state: bool = False,
                    extra: dict[str, Any] | None = None) -> None:
    """Send changed clock pixels, and the state at most ~4 times a second (or when forced)."""
    sockets: set = app[SOCKETS]
    if not sockets:
        return
    b: _Broadcast = app[OPTIONS]["broadcast"]
    screens = _clock_pixels(app)
    changed = {sk: s for sk, s in screens.items() if b.last_pixels.get(sk) != s["pixels"]}
    now = time.monotonic()
    send_state = force_state or bool(changed) or now - b.last_state_t >= 0.25
    if not send_state and extra is None:
        return
    msg: dict[str, Any] = {"type": "tick", "clocks": changed}
    if send_state:
        msg["state"] = studio_state(app)
        b.last_state_t = now
    if extra:
        msg.update(extra)
    for sk, s in changed.items():
        b.last_pixels[sk] = s["pixels"]
    dead = [ws for ws in list(sockets) if not await _send(ws, msg)]
    for ws in dead:
        sockets.discard(ws)


async def h_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    app = request.app
    await _send(ws, {"type": "hello", "version": app[PIPE].current.version,
                     "clocks": _clock_pixels(app), "state": studio_state(app)})
    app[SOCKETS].add(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT and msg.data == "ping":
                await _send(ws, {"type": "pong"})
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        app[SOCKETS].discard(ws)
    return ws


# =========================================================================================
# Background tasks
# =========================================================================================


async def _wait_until_listening(base_url: str | None, timeout_s: float = 10) -> None:
    """aiohttp runs the startup context before it binds the socket; wait for the bind so the
    feeder's first pushes to its own clock do not fail."""
    if not base_url:
        return
    from urllib.parse import urlsplit

    u = urlsplit(base_url)
    host, port = u.hostname or "127.0.0.1", u.port or 80
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.open_connection(host, port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return


async def _feeder_loop(app: web.Application) -> None:
    feeder = app[FEEDER]
    await _wait_until_listening(feeder.base_url)
    while True:
        try:
            await feeder.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("feeder tick failed")
        await asyncio.sleep(0.25)


async def _broadcast_loop(app: web.Application) -> None:
    while True:
        try:
            await broadcast(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("broadcast failed")
        await asyncio.sleep(0.1)


async def check_reload(app: web.Application) -> bool | None:
    """Reload if watched files changed. None when nothing changed, else success."""
    pipe = app[PIPE]
    if not pipe.changed():
        return None
    # Wait for the files to settle (editors write in several steps).
    for _ in range(10):
        await asyncio.sleep(0.25)
        if not pipe.changed():
            break
    ok = pipe.reload()
    if ok:
        log.info("reloaded (version %s)", pipe.current.version)
        app[GALLERY_CACHE].clear()
        app[FEEDER].force_push()
    else:
        log.warning("reload failed; keeping the last good version:\n%s", pipe.error)
    await broadcast(app, force_state=True, extra={
        "reload": {"ok": ok, "version": pipe.current.version, "error": pipe.error}})
    return ok


async def _watch_loop(app: web.Application) -> None:
    while True:
        try:
            await check_reload(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reload check failed")
        await asyncio.sleep(0.4)


async def _stop_tasks(app: web.Application) -> None:
    tasks = app[OPTIONS].get("tasks", [])
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    app[OPTIONS]["tasks"] = []


async def _on_shutdown(app: web.Application) -> None:
    """Runs right after the listener closes. Stop the loops first (so the feeder never pushes
    to whatever server takes the port next), then close WebSockets: aiohttp waits for open
    handlers before cleanup, so leaving them open would hold the process up for a minute."""
    await _stop_tasks(app)
    for ws in list(app[SOCKETS]):
        with contextlib.suppress(Exception):
            await ws.close(code=1001, message=b"server shutting down")


async def _background(app: web.Application) -> AsyncIterator[None]:
    if app[OPTIONS]["start_tasks"]:
        app[OPTIONS]["tasks"] = [asyncio.create_task(_feeder_loop(app)),
                                 asyncio.create_task(_broadcast_loop(app)),
                                 asyncio.create_task(_watch_loop(app))]
    yield
    await _stop_tasks(app)
    await app[FEEDER].close()


# =========================================================================================
# App factory
# =========================================================================================


def create_app(
    config_path: str | Path | None = None,
    *,
    time_fn: Callable[[], float] = time.monotonic,
    feeder_base_url: str | None = None,
    start_tasks: bool = True,
    log_dir: str | Path | None = None,
) -> web.Application:
    """Build the simulator app.

    ``feeder_base_url`` is this server's own address (``http://127.0.0.1:7001``); the feeder
    pushes to the clock there (``{feeder_base_url}/api/v1/...``) and does nothing until it is
    set.
    ``time_fn`` drives the fake clock's lifetime and the feeder's heartbeat (tests inject it).
    ``log_dir`` is where the "Robot log" source reads the spike's logs (default ``logs``). It
    is only read, and only once that source is chosen.
    """
    app = web.Application(client_max_size=MAX_BODY)
    pipe = ReloadingPipeline(Path(config_path) if config_path else None)
    app[PIPE] = pipe
    app[CLOCKS] = {size_key(s): FakeClock(s[0], s[1], time_fn=time_fn)
                   for s in pipe.current.sizes()}
    app[FEEDER] = StudioFeeder(pipe, time_fn=time_fn, base_url=feeder_base_url,
                               log_dir=log_dir)
    app[SOCKETS] = set()
    app[GALLERY_CACHE] = {}
    app[OPTIONS] = {"start_tasks": start_tasks, "broadcast": _Broadcast()}

    _add_clock_routes(app)
    r = app.router
    r.add_get("/", h_index)
    r.add_get("/static/{path:.+}", h_static)
    r.add_get("/studio/meta", h_meta)
    r.add_get("/studio/gallery", h_gallery)
    r.add_get("/studio/state", h_state)
    r.add_post("/studio/control", h_control)
    r.add_get("/studio/png", h_png)
    r.add_get("/studio/ws", h_ws)
    app.cleanup_ctx.append(_background)
    app.on_shutdown.append(_on_shutdown)
    return app
