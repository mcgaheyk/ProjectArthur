"""What goes into a code slot on the clock, and its manifest (D-0029). CPython only.

The lists here are the one place the shared-code list lives as data. ``CLAUDE.md`` says the
same in words ("The shared code also runs on MicroPython"), and ``tests/test_portable.py``
checks the two agree and that every ``arthur`` import in a slot file resolves inside the slot.

A slot holds, at these paths:

- ``arthur/...``: the shared code (:data:`SHARED`), the on-clock code (:data:`ONCLOCK`) and
  the package files they import through (:data:`PACKAGES`), as ``.py`` or compiled ``.mpy``;
- ``arthur/render/fonts/*.bdf`` and ``arthur/render/icon_art/*.txt``: fonts and art;
- ``lib/*.py``: the stdlib stand-ins from ``mpy/lib/`` (MicroPython only);
- ``manifest.json``: every other file's SHA-256 and size, written last.

The config is not in a slot: it is ``<root>/config.json``, so a colour or a caption can
change without a new slot.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The shared code: runs unchanged on CPython 3.13 and MicroPython 1.29. A folder means every
#: ``.py`` in it and below, less :data:`SHARED_EXCLUDE`.
SHARED = (
    "arthur/compat.py",
    "arthur/model.py",
    "arthur/config.py",
    "arthur/timebase.py",
    "arthur/schedule.py",
    "arthur/mapping.py",
    "arthur/zonetable.py",
    "arthur/mtrandom.py",
    "arthur/sources/tracker.py",
    "arthur/render/",
)
SHARED_EXCLUDE = ("arthur/render/preview.py",)
#: Code only the clock runs (its tests run it under CPython as well). ``wire_source.py`` is
#: built on another branch; a file listed here that does not exist yet is skipped.
ONCLOCK = ("arthur/onclock/", "arthur/wire/", "arthur/sources/wire_source.py")
#: Package files the code above imports through.
PACKAGES = ("arthur/__init__.py", "arthur/sources/__init__.py")
#: Data files: (folder, suffix).
DATA = (("arthur/render/fonts", ".bdf"), ("arthur/render/icon_art", ".txt"))
#: The stdlib stand-ins, which go to ``lib/`` in the slot.
STANDINS = "mpy/lib"
MANIFEST = "manifest.json"


def _py_files(repo: Path, entry: str) -> list[str]:
    p = repo / entry
    if entry.endswith("/"):
        if not p.is_dir():
            return []
        return sorted(f.relative_to(repo).as_posix() for f in p.rglob("*.py")
                      if "__pycache__" not in f.parts)
    return [entry] if p.is_file() else []


def code_files(repo: Path = REPO) -> list[str]:
    """Every Python file of ``arthur`` a slot carries, repo-relative, sorted."""
    out: set[str] = set(PACKAGES)
    for entry in SHARED + ONCLOCK:
        out.update(_py_files(repo, entry))
    out.difference_update(SHARED_EXCLUDE)
    return sorted(f for f in out if (repo / f).is_file())


def slot_sources(repo: Path = REPO) -> dict[str, Path]:
    """``{path in the slot: source file}`` for one slot, before any compiling."""
    files = {f: repo / f for f in code_files(repo)}
    for folder, suffix in DATA:
        for p in sorted((repo / folder).glob("*" + suffix)):
            files[p.relative_to(repo).as_posix()] = p
    for p in sorted((repo / STANDINS).glob("*.py")):
        files["lib/" + p.name] = p
    return files


def mpy_cross() -> str | None:
    return shutil.which("mpy-cross")


def build_slot(out: Path, repo: Path = REPO, compiled: bool = True,
               extra: dict[str, Path] | None = None, cross: str | None = None) -> bytes:
    """Write one slot's files under ``out`` (which must not exist) and its manifest. With
    ``compiled``, every ``arthur`` module but the package files is compiled to ``.mpy`` with
    ``mpy-cross`` (the stand-ins in ``lib/`` stay source). ``extra`` adds or replaces files
    (the tests' stand-in program). Returns the manifest's bytes."""
    sources = slot_sources(repo)
    if extra:
        sources.update(extra)
    if compiled:
        cross = cross or mpy_cross()
        if cross is None:
            raise RuntimeError("mpy-cross is not on PATH (brew install micropython)")
    out.mkdir(parents=True)
    for rel, src in sorted(sources.items()):
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if compiled and rel.startswith("arthur/") and rel.endswith(".py"):
            dest = dest.with_suffix(".mpy")
            subprocess.run([cross, "-o", str(dest), "-s", rel, str(src)], check=True,
                           capture_output=True)
        else:
            shutil.copyfile(src, dest)
    data = manifest_bytes(out)
    (out / MANIFEST).write_bytes(data)
    return data


def manifest_bytes(folder: Path) -> bytes:
    """The manifest of every file under ``folder`` (but a manifest): canonical JSON, so the
    same files always give the same bytes and so the same bundle id."""
    files = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.name != MANIFEST:
            data = p.read_bytes()
            files[p.relative_to(folder).as_posix()] = [hashlib.sha256(data).hexdigest(),
                                                        len(data)]
    return json.dumps({"v": 1, "files": files}, sort_keys=True,
                      separators=(",", ":")).encode()


def bundle_id(manifest: bytes) -> str:
    """The same id the door reports for a slot (``arthur.onclock.door.bundle_id``)."""
    return hashlib.sha256(manifest).hexdigest()[:16]


def slot_size(folder: Path) -> tuple[int, int]:
    """(files, bytes) under ``folder``, manifest included."""
    files = [p for p in folder.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def temp_slot(compiled: bool = True, extra: dict[str, Path] | None = None) -> tuple[Path, bytes]:
    """A slot in a new temporary folder: ``(folder, manifest bytes)``."""
    base = Path(tempfile.mkdtemp(prefix="arthur-slot-"))
    folder = base / "slot"
    return folder, build_slot(folder, compiled=compiled, extra=extra)
