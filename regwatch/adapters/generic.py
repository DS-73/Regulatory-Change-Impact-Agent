"""Link-list parsing shared by regulators whose listing is a page of document links."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..textutil import parse_date
from .base import Item, SourceAdapter


class LinkListAdapter(SourceAdapter):
    link_selector = "a[href]"
    link_pattern = r"\.pdf$"

    def parse(self, html: str) -> list[Item]:
        soup = BeautifulSoup(html, "html.parser")
        pat = re.compile(self.cfg.get("link_pattern", self.link_pattern))
        base = self.cfg.get("base_url") or self.cfg["listing_url"]
        items, seen = [], set()
        for a in soup.select(self.cfg.get("link_selector", self.link_selector)):
            href = a.get("href", "")
            if not href or href.startswith(("#", "javascript:", "mailto:")) or not pat.search(href):
                continue
            url = urljoin(base, href)
            if url in seen:
                continue
            seen.add(url)
            title = a.get_text(" ", strip=True) or url.rsplit("/", 1)[-1]
            # Date usually sits next to the link: same element, or elsewhere in the same row / list item.
            row = a.find_parent(["tr", "li"]) or a.parent
            date = parse_date(a.parent.get_text(" ", strip=True) if a.parent else "") or \
                (parse_date(row.get_text(" ", strip=True)) if row else None)
            items.append(Item(title=title[:250], url=url, doc_url=url, published_on=date))
        return items
