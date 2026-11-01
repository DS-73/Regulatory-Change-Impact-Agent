"""Central Bank of the UAE rulebook.

The landing page lists sector categories (Banking, Insurance, ...), which are navigation, not
regulations. The adapter crawls one level: landing -> sector pages -> regulation / standard /
guidance / notice pages. Only links whose title names an instrument are kept.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..textutil import parse_date
from .base import Item, SourceAdapter, SourceUnavailable, safe_get

INSTRUMENT = re.compile(r"(?i)\b(regulations?|standards?|guidance|notice|circular|law|rules?)\b")
MAX_SECTORS = 2


class CBUAEAdapter(SourceAdapter):
    code = "CBUAE"

    def _links(self, html: str):
        base = self.cfg.get("base_url") or self.cfg["listing_url"]
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href]"):
            href = a["href"]
            if not re.search(r"^/?en/rulebook/|rulebook\.centralbank\.ae/en/rulebook/", href) and "/notice" not in href:
                continue
            row = a.find_parent(["tr", "li"]) or a.parent
            yield a.get_text(" ", strip=True), urljoin(base, href), row

    def parse(self, html: str) -> list[Item]:
        """Instrument links on a page (sector pages, or any page that lists regulations)."""
        items, seen = [], set()
        for title, url, row in self._links(html):
            if url in seen or not INSTRUMENT.search(title) or len(title) < 8:
                continue
            seen.add(url)
            items.append(Item(title=title.rstrip(" ›")[:250], url=url, doc_url=url,
                              published_on=parse_date(row.get_text(" ", strip=True)) if row else None))
        return items

    def sector_pages(self, html: str) -> list[str]:
        out = []
        for title, url, _ in self._links(html):
            path = urlparse(url).path.rstrip("/")
            if path.count("/") == 3 and title and not INSTRUMENT.search(title) and url not in out:
                out.append(url)  # /en/rulebook/<sector>
        return out

    def fetch_listing(self, client: httpx.Client) -> list[Item]:
        try:
            r = safe_get(client, self.cfg["listing_url"], self.allowed)
            if r.status_code >= 400:
                raise SourceUnavailable(f"HTTP {r.status_code} from listing page")
            items = self.parse(r.text)
            for url in ([] if items else self.sector_pages(r.text)[:MAX_SECTORS]):
                s = safe_get(client, url, self.allowed)
                if s.status_code < 400:
                    items += [i for i in self.parse(s.text) if i.url not in {x.url for x in items}]
        except (httpx.HTTPError, OSError) as e:
            raise SourceUnavailable(f"{type(e).__name__}: {str(e)[:160]}") from e
        if not items:
            raise SourceUnavailable("no regulation links found on the rulebook pages")
        return items
