"""Import bridge for the implementation stored in .graph_engine/."""

from pathlib import Path

_IMPLEMENTATION = Path(__file__).resolve().parent.parent / ".graph_engine"
__path__.append(str(_IMPLEMENTATION))

__version__ = "0.1.0"
