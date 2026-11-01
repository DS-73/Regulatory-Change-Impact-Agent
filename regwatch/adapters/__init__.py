"""One adapter per regulator, each built and tested independently (see tests/test_adapters.py)."""
from .base import Item, SourceAdapter, SourceUnavailable
from .cbn import CBNAdapter
from .cbuae import CBUAEAdapter
from .rbi import RBIAdapter
from .sama import SAMAAdapter

ADAPTERS = {a.code: a for a in (SAMAAdapter, CBUAEAdapter, RBIAdapter, CBNAdapter)}


def adapter_for(cfg: dict) -> SourceAdapter:
    return ADAPTERS[cfg["short_code"]](cfg)


__all__ = ["ADAPTERS", "Item", "SourceAdapter", "SourceUnavailable", "adapter_for"]
