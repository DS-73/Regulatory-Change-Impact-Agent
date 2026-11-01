"""Saudi Central Bank: SharePoint circulars list; items link to circular pages or PDFs."""
from __future__ import annotations

from .generic import LinkListAdapter


class SAMAAdapter(LinkListAdapter):
    code = "SAMA"
    link_pattern = r"(?i)(circular|\.pdf)"
