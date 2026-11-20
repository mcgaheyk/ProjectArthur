"""Structural rules the MicroPython port relies on (D-0029, docs/reference/micropython.md)."""

from __future__ import annotations

import ast
import pkgutil
import re
from pathlib import Path

import arthur.render.scenes
from arthur.render import idle_show, screens
from arthur.tools import onclock_files as slotfiles

RENDER = Path(screens.__file__).resolve().parent
REPO = RENDER.parent.parent


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


# -- what goes on the clock (arthur/tools/onclock_files.py, deploy/clock/boot.py) ------------

#: Modules the on-clock program will have once the other branches land (D-0029); until then
#: an import of one is allowed to point at nothing. (The program, ``onclock/app.py``,
#: ``main.py`` and ``clocklog.py``, has landed.)
TO_COME: set[str] = set()
#: CPython-only modules a slot file may import lazily, inside a branch MicroPython never
#: takes (``wire_source`` builds its Mac defaults, the token file and the daily log files, from
#: ``spikelog``). The guard itself is not checked here: ``tests/test_wire_source_micropython.py``
#: imports and runs the module under MicroPython, where an unguarded import would fail.
CPYTHON_ONLY = ("arthur.spikelog",)


def _claude_md_shared() -> tuple[set[str], set[str], str]:
    text = (REPO / "CLAUDE.md").read_text()
    para = text[text.index("**The shared code also runs on MicroPython**"):]
    para = para[: para.index("\n- ")]
    head = para[: para.index("In them:")]
    shared, exclude = set(), set()
    for name in re.findall(r"`([^`]+)`", head):
        if name == "preview.py":
            exclude.add("arthur/render/preview.py")
        elif name.endswith(("/", ".py")):
            shared.add(name if name.startswith("arthur/") else "arthur/" + name)
        else:
            shared.add(f"arthur/{name}.py")
    return shared, exclude, para


def test_the_shared_list_matches_claude_md():
    shared, exclude, para = _claude_md_shared()
    assert shared == set(slotfiles.SHARED)
    assert exclude == set(slotfiles.SHARED_EXCLUDE)
    for entry in slotfiles.ONCLOCK:
        if entry.endswith("/"):
            assert f"`{entry}`" in para, entry


def test_a_slot_carries_the_shared_and_on_clock_code_and_nothing_else():
    code = set(slotfiles.code_files())
    assert "arthur/render/preview.py" not in code
    for must in ("arthur/compat.py", "arthur/render/scenes/bird.py", "arthur/sources/tracker.py",
                 "arthur/onclock/door.py", "arthur/wire/client.py", "arthur/__init__.py"):
        assert must in code, must
    cpython_only = ("arthur/feeder.py", "arthur/pusher.py", "arthur/spikelog.py",
                    "arthur/fixtures.py", "arthur/clocktool.py", "arthur/sources/whisker.py",
                    "arthur/sources/weather.py", "arthur/sources/logfile.py")
    for f in code:
        assert f not in cpython_only, f
        assert not f.startswith(("arthur/sim/", "arthur/tools/", "arthur/transports/")), f
    sources = slotfiles.slot_sources()
    assert "arthur/render/fonts/awtrix.bdf" in sources
    assert "lib/typing.py" in sources
    assert sum(1 for p in sources if p.startswith("arthur/render/icon_art/")) == len(
        list((RENDER / "icon_art").glob("*.txt")))


def _arthur_imports(tree: ast.AST) -> set[str]:
    """``arthur`` modules a file imports at run time (not under ``if TYPE_CHECKING:``)."""
    out: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for n in node.orelse:
                visit(n)
            return
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.split(".")[0] == "arthur")
        elif isinstance(node, ast.ImportFrom) and node.module \
                and node.module.split(".")[0] == "arthur":
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "__import__" \
                and node.args and isinstance(node.args[0], ast.Constant) \
                and str(node.args[0].value).startswith("arthur"):
            out.add(node.args[0].value)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return out


def test_every_arthur_import_in_a_slot_resolves_inside_the_slot():
    code = set(slotfiles.code_files())
    for f in sorted(code):
        for mod in _arthur_imports(ast.parse((REPO / f).read_text())):
            path = mod.replace(".", "/")
            if f"{path}.py" in code or f"{path}/__init__.py" in code:
                continue
            parent = mod.rsplit(".", 1)[0].replace(".", "/")
            if f"{parent}.py" in code or f"{parent}/__init__.py" in code:
                continue  # ``from arthur.x import name``: a name, not a module
            if any(mod == m or mod.startswith(m + ".") for m in CPYTHON_ONLY):
                continue
            assert mod in TO_COME, f"{f} imports {mod}, which a slot does not carry"


def test_boot_imports_only_micropythons_own_modules():
    tree = ast.parse((REPO / "deploy" / "clock" / "boot.py").read_text())
    allowed = {"asyncio", "binascii", "hashlib", "json", "os", "sys", "time"}
    for node in ast.walk(tree):
        assert not isinstance(node, ast.ImportFrom), f"boot.py: from {node.module} import"
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name in allowed, a.name
    assert _arthur_imports(tree) == {"arthur.onclock.main", "arthur.sources.wire_source"}


def test_slot_code_has_no_star_displays():
    """``[*a, b]``, ``(*a,)`` and ``{**d}`` are syntax errors on MicroPython."""
    for f in slotfiles.code_files():
        for node in ast.walk(ast.parse((REPO / f).read_text())):
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                assert not any(isinstance(e, ast.Starred) for e in node.elts), (f, node.lineno)
            if isinstance(node, ast.Dict):
                assert None not in node.keys, (f, node.lineno)
