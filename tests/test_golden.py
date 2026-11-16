"""Golden text snapshots of every fixture in every variant.

Each file in ``tests/golden/`` is ``<fixture>__<WxH>__<variant>.txt``: the preview legend line
followed by the frame, one character per pixel (see ``arthur.render.preview``).

When a screen changes ON PURPOSE, regenerate them and review the diff:

    ARTHUR_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py

Without that variable a mismatch fails the test and shows both renders.
"""

import os
from pathlib import Path

import pytest

from arthur.config import default_config, format_size
from arthur.fixtures import FIXTURES
from arthur.render import list_sizes, list_variants
from arthur.render.preview import frame_text, legend_text, render_fixture

CFG = default_config()
GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("ARTHUR_UPDATE_GOLDEN") == "1"


def _cases():
    for name, fx in FIXTURES.items():
        for size in list_sizes():
            for v in list_variants(fx.kind, size):
                yield name, size, v


CASES = list(_cases())


def _path(name, size, variant):
    return GOLDEN / f"{name}__{format_size(size)}__{variant}.txt"


@pytest.mark.parametrize(
    ("name", "size", "variant"), CASES, ids=[_path(*c).stem for c in CASES]
)
def test_golden(name, size, variant):
    frame = render_fixture(FIXTURES[name], CFG, size, variant, stale=False)
    text = legend_text(CFG) + "\n" + frame_text(frame, CFG) + "\n"
    p = _path(name, size, variant)
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        p.write_text(text)
        return
    assert p.exists(), f"missing {p.name}; run ARTHUR_UPDATE_GOLDEN=1 uv run pytest {__file__}"
    expected = p.read_text()
    assert text == expected, f"{p.name} changed.\n--- golden\n{expected}\n--- now\n{text}"


def test_no_orphan_goldens():
    expected = {_path(*c).name for c in CASES}
    if UPDATE:
        for p in GOLDEN.glob("*.txt"):
            if p.name not in expected:
                p.unlink()
        return
    actual = {p.name for p in GOLDEN.glob("*.txt")}
    assert actual <= expected, f"orphan golden files: {sorted(actual - expected)}"
