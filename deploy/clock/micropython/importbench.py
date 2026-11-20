"""How long a slot takes to load, and what it leaves on the heap: for the .mpy decision.

    micropython -X heapsize=2M importbench.py SLOT_DIR [repeat]

Imports the shared modules and the on-clock code from the slot as the program will at start
(mapping, render with the idle show's pages and scenes, the tracker, the wire client, the
door), loads both fonts, then reports the time taken and the heap in use after a collection.
Run it once over a slot of sources and once over a compiled one (``scripts/clock.py bundle``
writes either). Prints ``R key=value`` lines.
"""

import gc
import sys
import time

slot = sys.argv[1]
sys.path.insert(0, slot + "/lib")
sys.path.insert(0, slot)
gc.collect()
base = gc.mem_alloc()
t0 = time.ticks_ms()
import arthur.mapping
import arthur.onclock.door
import arthur.render
import arthur.sources.tracker
import arthur.wire.client  # noqa: F401
from arthur.render.idle_show import load_plugins

load_plugins()
t1 = time.ticks_ms()
from arthur.render.font import condensed_small_font, large_font

condensed_small_font()
large_font()
t2 = time.ticks_ms()
gc.collect()
print("R import_ms=" + str(time.ticks_diff(t1, t0)))
print("R fonts_ms=" + str(time.ticks_diff(t2, t1)))
print("R heap_after_kb=" + str((gc.mem_alloc() - base) // 1024))
print("R heap_free_kb=" + str(gc.mem_free() // 1024))
