"""Rendering: DisplayState + now + config + size → Frame. Knows nothing about AWTRIX."""

from arthur.render import idle_show  # noqa: F401  (installs the idle show's cue player)
from arthur.render.frame import Frame, overlay_stale
from arthur.render.screens import list_kinds, list_sizes, list_variants, render

__all__ = ["Frame", "list_kinds", "list_sizes", "list_variants", "overlay_stale", "render"]
