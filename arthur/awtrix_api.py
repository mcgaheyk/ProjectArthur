"""The pure part of talking to AWTRIX NG 1.2.3, shared by both transports (D-0003, D-0029).

The Mac's transport (:mod:`arthur.transports.awtrix_ng`, aiohttp) and the clock's own
(:mod:`arthur.onclock.awtrix`, asyncio streams under MicroPython) build the same requests from
here, so what reaches the clock is the same byte for byte. Nothing here does I/O. It runs
unchanged on CPython and MicroPython (shared code: see CLAUDE.md and
``docs/reference/micropython.md``). The API itself is in ``docs/reference/awtrix-ng.md``.

- :func:`payload` / :func:`payload_text`: one pushed app whose ``draw`` is a full-frame
  bitmap, with ``lifetimeMs`` and ``lifetimeExpiry: "mark"`` (D-0004). The text is what
  ``json.dumps(payload(...), separators=(",", ":"))`` gives on CPython; it is written out by
  hand because MicroPython's dicts do not keep their order.
- :func:`rotation_fix`: the ``PUT /api/v1/apps/order`` body that leaves ours the only app in
  the rotation, or None.
- :func:`needs_native_size`, :func:`apps_ok`: reading ``GET /api/v1/settings`` and
  ``GET /api/v1/apps``.
"""

import binascii

#: Our pushed app's name.
APP_NAME = "litter"
#: How often the settings and the rotation are checked again while all is well, seconds.
SETTINGS_CHECK_S = 300

SETTINGS_PATH = "/api/v1/settings"
APPS_PATH = "/api/v1/apps"
ORDER_PATH = "/api/v1/apps/order"
SCREEN_PATH = "/api/v1/display/screen"


def pushed_path(app_name):
    """``PUT`` here creates or replaces the pushed app."""
    return "/api/v1/apps/pushed/" + app_name


def app_path(app_name):
    """``DELETE`` here removes the app."""
    return "/api/v1/apps/" + app_name


def lifetime_ms(ttl_s):
    """``lifetimeMs`` for a lifetime of ``ttl_s`` seconds (``round(ttl_s * 1000)``)."""
    return round(ttl_s * 1000)


def b64(rgb):
    """Plain base64 of the RGB888 bytes, as str (no ``data:`` prefix, no newline)."""
    return binascii.b2a_base64(rgb).decode().strip()


def payload(rgb, width, height, ttl_s):
    """The JSON body (as a dict) for one frame of ``width`` x ``height`` RGB888 bytes."""
    return {
        "draw": [["bitmap", 0, 0, width, height, b64(rgb)]],
        "lifetimeMs": lifetime_ms(ttl_s),
        "lifetimeExpiry": "mark",
    }


def payload_frame(width, height, ttl_s):
    """``(head, tail)``: the body text is ``head + base64 + tail``. They change only with the
    frame's size and the lifetime, so the clock's transport builds them once."""
    head = '{"draw":[["bitmap",0,0,' + str(int(width)) + "," + str(int(height)) + ',"'
    tail = '"]],"lifetimeMs":' + str(lifetime_ms(ttl_s)) + ',"lifetimeExpiry":"mark"}'
    return head, tail


def payload_text(rgb, width, height, ttl_s):
    """The body as sent: ``json.dumps(payload(...), separators=(",", ":"))`` on CPython."""
    head, tail = payload_frame(width, height, ttl_s)
    return head + b64(rgb) + tail


def b64_length(n_bytes):
    """Characters of base64 for ``n_bytes`` bytes."""
    return 4 * ((n_bytes + 2) // 3)


def needs_native_size(settings):
    """True when ``GET /api/v1/settings`` says pushed apps are drawn at double size
    (``enlargeApps`` true). A clock without the key is left alone."""
    return settings.get("enlargeApps") is True


#: The ``PATCH /api/v1/settings`` body that turns double size off.
NATIVE_SIZE = {"enlargeApps": False}
NATIVE_SIZE_TEXT = '{"enlargeApps":false}'


def apps_ok(data):
    """True when ``GET /api/v1/apps`` is an array of apps with names."""
    if not isinstance(data, list):
        return False
    for a in data:
        if not isinstance(a, dict) or not isinstance(a.get("name"), str):
            return False
    return True


def rotation_fix(apps, app_name):
    """The ``PUT /api/v1/apps/order`` body that leaves ``app_name`` the only app in the
    rotation, or None when it already is.

    ``apps`` is ``GET /api/v1/apps``. A fix is needed when another app (other than the
    scripts below) has ``inLoop`` true, or ours is switched off. Because ``disabled`` is the
    complete list of switched-off apps (every app it leaves out is switched on), it names every
    other app, including ones already off and names that only hold a place, except two kinds
    that never take a turn on their own and are left running: background scripts
    (``headless``) and on-demand scripts (``ondemand``). Background scripts also go into
    ``order`` after ours, since a background script runs only while ``order`` names it.
    Modules (no ``enabled``) are not apps and are left out of both lists.
    """
    others = [a for a in apps if a["name"] != app_name and "enabled" in a]
    keep = [a for a in others
            if a.get("enabled") is not False and (a.get("headless") or a.get("ondemand"))]
    keep_names = [a["name"] for a in keep]
    ours_off = False
    for a in apps:
        if a["name"] == app_name and a.get("enabled") is False:
            ours_off = True
    in_loop = False
    for a in others:
        if a["name"] not in keep_names and a.get("inLoop") is True:
            in_loop = True
    if not ours_off and not in_loop:
        return None
    order = [app_name]
    disabled = []
    for a in others:
        name = a["name"]
        if name in keep_names:
            if a.get("headless") and name not in order:
                order.append(name)
        elif name not in disabled:
            disabled.append(name)
    return {"order": order, "disabled": disabled}


def order_text(fix):
    """``json.dumps(fix, separators=(",", ":"))`` for a :func:`rotation_fix` body, keys in
    that order on both Pythons."""
    import json

    return ('{"order":' + json.dumps(fix["order"], separators=(",", ":"))
            + ',"disabled":' + json.dumps(fix["disabled"], separators=(",", ":")) + "}")
