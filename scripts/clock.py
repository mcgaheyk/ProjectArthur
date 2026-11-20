"""The Mac's tool for the program on the clock (D-0029). The logic is in arthur/clocktool.py.

uv run python scripts/clock.py build
uv run --env-file .env python scripts/clock.py --serial SERIAL install [--dry-run]
uv run python scripts/clock.py --clock HOST push | status | logs pull | pause | resume
uv run python scripts/clock.py --help
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arthur.clocktool import main

if __name__ == "__main__":
    sys.exit(main())
