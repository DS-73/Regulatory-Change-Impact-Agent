"""Central Bank of Nigeria: circulars page; most items are direct PDF links."""
from __future__ import annotations

from .generic import LinkListAdapter


class CBNAdapter(LinkListAdapter):
    code = "CBN"
    link_pattern = r"(?i)\.pdf$"
