"""Push rendered screens to a real AWTRIX NG clock (a hardware test, not the feeder).
The logic lives in arthur/pushtest.py.

uv run python scripts/push_test.py --clock http://HOST [NAME] [--seconds N] [--readback] [--clear]
uv run python scripts/push_test.py --list
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arthur.pushtest import main

if __name__ == "__main__":
    sys.exit(main())
