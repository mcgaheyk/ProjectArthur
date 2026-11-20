"""Structural rules the MicroPython port relies on (D-0029, docs/reference/micropython.md)."""

from __future__ import annotations

import pkgutil
from pathlib import Path

import arthur.render.scenes
from arthur.render import idle_show, screens

RENDER = Path(screens.__file__).resolve().parent


def test_scenes_are_found_as_pkgutil_would_find_them():
    """MicroPython has no pkgutil; the folder is listed instead, with the same result."""
    found = sorted(m.name for m in pkgutil.iter_modules(arthur.render.scenes.__path__)
                   if not m.name.startswith("_"))
    assert idle_show.scene_modules() == found
    assert sorted(idle_show.SCENES) == found


def test_no_folder_hides_a_module():
    """Under MicroPython a folder ``x/`` next to ``x.py`` is imported in place of the module
    (the art folder was renamed from ``icons/`` to ``icon_art/`` for this)."""
    for folder in (RENDER, RENDER.parent, RENDER / "scenes"):
        for p in folder.iterdir():
            if p.is_dir() and not p.name.startswith("__"):
                assert not (folder / f"{p.name}.py").exists(), p


def test_layout_variants_keep_their_order():
    """The first variant registered is the default: the registry keeps insertion order on
    both Pythons (an OrderedDict; MicroPython's dict does not keep it)."""
    assert screens.list_variants("ready", "52x16") == ["arthur", "vacant", "numbers"]
