"""``typing`` for MicroPython, which has none. The shared code imports only ``TYPE_CHECKING``
at run time; everything else from ``typing`` and ``collections.abc`` sits under
``if TYPE_CHECKING:``. MicroPython only."""

TYPE_CHECKING = False
