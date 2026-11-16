"""Log what the PETLIBRO Granary and fountain report, read only. The logic lives in
arthur/petlibro_spike.py.

uv run --env-file .env python scripts/spike_petlibro.py [--duration SECONDS] [--poll-seconds N]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arthur.petlibro_spike import main

if __name__ == "__main__":
    sys.exit(main())
