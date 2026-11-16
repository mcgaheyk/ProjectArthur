"""The reloadable half of the pipeline (model, mapping, fixtures, render) plus the config.

The studio reaches mapping and rendering only through a :class:`Pipeline`, never through
module-level imports, so a live reload can swap in freshly imported modules as one unit.

Reload strategy: the watched modules are removed from ``sys.modules`` and imported again as
*new* module objects. The candidate is then checked by rendering every fixture in every
variant. If the import, the config or any render fails, the old module objects are put
back in ``sys.modules`` and the old :class:`Pipeline` keeps serving; the error is reported.
Old module objects are never mutated, so a half-finished reload cannot leave a mix behind.
"""

from __future__ import annotations

import importlib
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import arthur
from arthur.config import DEFAULT_PATH, Config, load_config

ROOT = Path(arthur.__file__).resolve().parent

#: Watched source modules, imported in this order.
MODULES = ("arthur.model", "arthur.mapping", "arthur.fixtures", "arthur.render")


def _is_reloadable(name: str) -> bool:
    return name in MODULES or name.startswith("arthur.render.")


def watched_files(config_path: Path) -> list[Path]:
    """Every file whose change triggers a reload."""
    files = [config_path, ROOT / "mapping.py", ROOT / "model.py", ROOT / "fixtures.py"]
    render_dir = ROOT / "render"
    for p in sorted(render_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            files.append(p)
    return files


def snapshot_mtimes(files: list[Path]) -> dict[str, tuple[float, int] | None]:
    out: dict[str, tuple[float, int] | None] = {}
    for p in files:
        try:
            st = p.stat()
            out[str(p)] = (st.st_mtime, st.st_size)
        except OSError:
            out[str(p)] = None
    return out


def size_key(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


@dataclass
class Pipeline:
    """One consistent set of modules and config."""

    cfg: Config
    model: ModuleType
    mapping: ModuleType
    fixtures: ModuleType
    render_mod: ModuleType
    version: int = 1

    # --- the pipeline ---------------------------------------------------------------

    def state(self, snapshot: Any, health: Any, now: Any) -> Any:
        return self.mapping.to_display_state(snapshot, health, now, self.cfg)

    def render(self, state: Any, now: Any, size: tuple[int, int], variant: str | None) -> Any:
        return self.render_mod.render(state, now, self.cfg, size, variant)

    def overlay_stale(self, frame: Any) -> Any:
        return self.render_mod.overlay_stale(frame, self.cfg.rgb("stale"))

    def sizes(self) -> list[tuple[int, int]]:
        return [tuple(s) for s in self.render_mod.list_sizes()]  # type: ignore[misc]

    def variants(self, kind: str, size: tuple[int, int]) -> list[str]:
        try:
            return list(self.render_mod.list_variants(kind, size))
        except KeyError:
            return []

    def resolve_variant(self, kind: str, size: tuple[int, int], wanted: str | None) -> str:
        names = self.variants(kind, size)
        if wanted in names:
            return wanted  # type: ignore[return-value]
        return names[0] if names else "default"

    def fixture_frame(
        self, name: str, size: tuple[int, int], variant: str | None, offset_s: float = 0
    ) -> Any:
        from datetime import timedelta

        fx = self.fixtures.FIXTURES[name]
        now = fx.now + timedelta(seconds=offset_s)
        st = self.state(fx.snapshot, fx.health, now)
        return self.render(st, now, size, variant)

    def validate(self) -> None:
        """Render every fixture in every variant; raise on the first failure."""
        for name, fx in self.fixtures.FIXTURES.items():
            for size in self.sizes():
                st = self.state(fx.snapshot, fx.health, fx.now)
                for v in self.variants(st.kind, size) or [None]:
                    try:
                        self.render(st, fx.now, size, v)
                    except Exception as exc:
                        raise RuntimeError(
                            f"rendering fixture {name!r} at {size_key(size)} variant {v!r} "
                            f"failed: {exc.__class__.__name__}: {exc}"
                        ) from exc

    # --- metadata -------------------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        cfg = self.cfg
        kinds = list(self.render_mod.list_kinds())
        sizes = [size_key(s) for s in self.sizes()]
        variants = {
            size_key(s): {k: self.variants(k, s) for k in kinds} for s in self.sizes()
        }
        fixtures = [
            {"name": f.name, "kind": f.kind, "description": f.description}
            for f in self.fixtures.FIXTURES.values()
        ]
        scenarios = [
            {
                "name": s.name,
                "description": s.description,
                "duration_s": s.duration_s,
                "steps": [{"at_s": st.at_s, "label": st.label} for st in s.steps],
            }
            for s in self.fixtures.SCENARIOS.values()
        ]
        palette = {role: "#{:02X}{:02X}{:02X}".format(*rgb) for role, rgb in cfg.palette.colors.items()}
        t = cfg.timings
        return {
            "version": self.version,
            "kinds": kinds,
            "sizes": sizes,
            "default_size": size_key(cfg.size),
            "variants": variants,
            "fixtures": fixtures,
            "scenarios": scenarios,
            "palette": palette,
            "timings": {
                "heartbeat_s": t.heartbeat_s,
                "ttl_s": t.ttl_s,
                "just_cleaned_s": t.just_cleaned_s,
                "clean_cycle_estimate_s": t.clean_cycle_estimate_s,
                "no_data_after_s": t.no_data_after_s,
            },
            "thresholds": {"drawer_warn_pct": cfg.thresholds.drawer_warn_pct},
            "statuses": sorted(self.mapping.KNOWN_STATUSES),
            "health_errors": list(self.model.HEALTH_ERRORS),
            "config_source": cfg.source,
        }


def load_pipeline(config_path: Path | None = None, version: int = 1) -> Pipeline:
    """Build a pipeline from the modules already imported (first start)."""
    cfg = load_config(config_path)
    p = Pipeline(
        cfg=cfg,
        model=importlib.import_module("arthur.model"),
        mapping=importlib.import_module("arthur.mapping"),
        fixtures=importlib.import_module("arthur.fixtures"),
        render_mod=importlib.import_module("arthur.render"),
        version=version,
    )
    p.validate()
    return p


def fresh_pipeline(config_path: Path | None, version: int) -> Pipeline:
    """Import the watched modules afresh and build a validated pipeline.

    On any failure the previous modules are restored and the exception propagates.
    """
    cfg = load_config(config_path)  # config errors fail before modules are touched
    saved = {n: m for n, m in sys.modules.items() if _is_reloadable(n)}
    saved_attrs = {a: getattr(arthur, a) for a in ("model", "mapping", "fixtures", "render")
                   if hasattr(arthur, a)}
    for n in saved:
        del sys.modules[n]
    importlib.invalidate_caches()
    try:
        mods = [importlib.import_module(n) for n in MODULES]
        p = Pipeline(cfg, mods[0], mods[1], mods[2], mods[3], version)
        p.validate()
        return p
    except BaseException:
        for n in [n for n in sys.modules if _is_reloadable(n)]:
            del sys.modules[n]
        sys.modules.update(saved)
        for a, m in saved_attrs.items():
            setattr(arthur, a, m)
        raise


def format_error(exc: BaseException) -> str:
    """A short, readable error for the page: the location and message, then the tail."""
    if isinstance(exc, SyntaxError):
        loc = f"{exc.filename}:{exc.lineno}" if exc.filename else "?"
        text = (exc.text or "").rstrip()
        return f"SyntaxError in {loc}: {exc.msg}\n    {text}".rstrip()
    tb = traceback.format_exception(exc)
    tail = "".join(tb[-6:])
    return tail.strip()


class ReloadingPipeline:
    """Holds the current good :class:`Pipeline`, watches files and reloads on change."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_PATH
        self.current = load_pipeline(self.config_path)
        self.error: str | None = None
        self.error_at: float | None = None
        self.reloaded_at: float = time.time()
        self._mtimes = snapshot_mtimes(watched_files(self.config_path))

    def changed(self) -> bool:
        m = snapshot_mtimes(watched_files(self.config_path))
        if m != self._mtimes:
            self._mtimes = m
            return True
        return False

    def reload(self) -> bool:
        """Try to load the current files. True on success; on failure keep the old one."""
        try:
            self.current = fresh_pipeline(self.config_path, self.current.version + 1)
        except Exception as exc:  # noqa: BLE001 - any failure keeps the last good version
            self.error = format_error(exc)
            self.error_at = time.time()
            return False
        self.error = None
        self.error_at = None
        self.reloaded_at = time.time()
        return True
