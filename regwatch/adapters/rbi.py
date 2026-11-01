"""Reserve Bank of India: classic NotificationUser.aspx list (date header rows + a.link2 items)."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..textutil import parse_date
from .base import Item, SourceAdapter


class RBIAdapter(SourceAdapter):
    code = "RBI"

    def parse(self, html: str) -> list[Item]:
        soup = BeautifulSoup(html, "html.parser")
        items, current_date = [], None
        for tr in soup.find_all("tr"):
            hdr = tr.find("td", class_="tableheader")
            if hdr:
                current_date = parse_date(hdr.get_text(" ", strip=True))
                continue
            a = tr.find("a", class_="link2")
            if not a or not a.get("href"):
                continue
            detail = urljoin(self.cfg.get("base_url", self.cfg["listing_url"]), a["href"])
            pdf = tr.find("a", href=re.compile(r"\.pdf$", re.I))
            # The detail page holds the full notification text as HTML; the PDF host may refuse
            # automated downloads, so the PDF is only a fallback.
            items.append(Item(title=a.get_text(" ", strip=True), url=detail, doc_url=detail,
                              alt_url=pdf["href"] if pdf else None, published_on=current_date))
        return items
