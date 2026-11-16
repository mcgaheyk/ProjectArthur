"""Log every state change of the real Litter-Robot 4. The logic lives in arthur/spikelog.py.

uv run --env-file .env python scripts/spike_log.py [--duration SECONDS] [--poll-seconds N]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arthur.spikelog import main

if __name__ == "__main__":
    sys.exit(main())
