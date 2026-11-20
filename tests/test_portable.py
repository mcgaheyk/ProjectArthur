"""Structural rules the MicroPython port relies on (D-0029, docs/reference/micropython.md)."""

from __future__ import annotations

import ast
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


# ---- the shared-code list (CLAUDE.md) ------------------------------------------------------

ROOT = RENDER.parent.parent
#: Modules that must run unchanged on CPython and MicroPython (CLAUDE.md, "The shared code
#: also runs on MicroPython"). ``arthur/wire/`` is written for both too.
SHARED = sorted(
    [f"arthur/{m}.py" for m in ("compat", "model", "config", "timebase", "schedule",
                                "mapping", "zonetable", "mtrandom", "awtrix_api")]
    + ["arthur/sources/tracker.py", "arthur/sources/openmeteo.py"]
    + [str(p.relative_to(ROOT)) for p in (ROOT / "arthur" / "render").rglob("*.py")
       if p.name != "preview.py"]
    + [str(p.relative_to(ROOT)) for p in (ROOT / "arthur" / "onclock").glob("*.py")]
)
SHARED_MODULES = {s[:-3].replace("/", ".").removesuffix(".__init__") for s in SHARED}


def _module_imports(tree: ast.AST) -> list[str]:
    """``arthur.*`` names imported anywhere but under ``if TYPE_CHECKING:``."""
    out: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) \
                and node.test.id == "TYPE_CHECKING":
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
            out.extend(f"{node.module}.{a.name}" for a in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return [m for m in out if m.startswith("arthur")]


def _is_module(name: str) -> bool:
    path = ROOT / name.replace(".", "/")
    return path.with_suffix(".py").exists() or (path / "__init__.py").exists()


def test_shared_modules_import_only_shared_modules():
    """A shared module must not import a CPython-only one (the feeder, the Mac's transport
    and weather source, the Whisker source, the studio, the fixtures, the tools)."""
    for rel in SHARED:
        for name in _module_imports(ast.parse((ROOT / rel).read_text())):
            if not _is_module(name) or name == "arthur":
                continue  # a name imported from a module, not a module
            ok = (name in SHARED_MODULES or name.startswith("arthur.wire")
                  or name in ("arthur.render", "arthur.sources"))
            assert ok, (rel, name)


def test_shared_modules_have_no_star_displays():
    """``[*a, b]``, ``(*a,)`` and ``{**d}`` are syntax errors on MicroPython."""
    for rel in SHARED:
        for node in ast.walk(ast.parse((ROOT / rel).read_text())):
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                assert not any(isinstance(e, ast.Starred) for e in node.elts), (rel, node)
            if isinstance(node, ast.Dict):
                assert None not in node.keys, (rel, node)
