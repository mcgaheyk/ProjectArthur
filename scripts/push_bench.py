"""Measure push latency and frame rates against an AWTRIX NG clock, and show test patterns.
The logic lives in arthur/pushbench.py.

uv run python scripts/push_bench.py --clock http://HOST MODE [options]
uv run python scripts/push_bench.py --help
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arthur.pushbench import main

if __name__ == "__main__":
    sys.exit(main())
