"""Shared plumbing for source adapters: SSRF-safe fetching and the adapter contract.

Each regulator has its own adapter module. An adapter only knows how to read its
regulator's listing page and documents; the monitor decides what to do on failure.
`parse()` is a pure function of HTML, so every adapter is testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from ..security import MAX_DOWNLOAD_BYTES, is_safe_url

UA = {"User-Agent": "Mozilla/5.0 (RegWatch regulatory monitor; +https://github.com/regwatch)"}
TIMEOUT = httpx.Timeout(25.0, connect=10.0)


class SourceUnavailable(RuntimeError):
    """Live retrieval failed (HTTP 403/5xx, timeout, TLS, empty parse). Triggers the sample fallback."""


class BlockedURL(SourceUnavailable):
    pass


@dataclass
class Item:
    title: str
    url: str                 # stable URL used for dedupe (detail page)
    doc_url: str             # what to download
    published_on: str | None = None
    alt_url: str | None = None


def safe_get(client: httpx.Client, url: str, allowed_hosts: list[str], max_bytes: int = MAX_DOWNLOAD_BYTES):
    """GET with an allowlist check on every hop (redirects followed manually) and a size cap."""
    for _ in range(4):
        ok, why = is_safe_url(url, allowed_hosts)
        if not ok:
            raise BlockedURL(f"blocked {url[:80]}: {why}")
        with client.stream("GET", url) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("location", ""))
                continue
            body = bytearray()
            for chunk in r.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise BlockedURL(f"response larger than {max_bytes // (1024 * 1024)} MB")
            r._content = bytes(body)  # make .text / .content usable after streaming
            return r
    raise BlockedURL("too many redirects")


class SourceAdapter:
    code = "BASE"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        hosts = cfg.get("allowed_hosts") or [urlparse(cfg["listing_url"]).hostname]
        self.allowed = [h.lower() for h in hosts]

    # -------- pure, offline-testable
    def parse(self, html: str) -> list[Item]:
        raise NotImplementedError

    # -------- network
    def client(self) -> httpx.Client:
        return httpx.Client(headers=UA, timeout=TIMEOUT, follow_redirects=False)

    def fetch_listing(self, client: httpx.Client) -> list[Item]:
        try:
            r = safe_get(client, self.cfg["listing_url"], self.allowed)
        except (httpx.HTTPError, OSError) as e:
            raise SourceUnavailable(f"{type(e).__name__}: {str(e)[:160]}") from e
        if r.status_code >= 400:
            raise SourceUnavailable(f"HTTP {r.status_code} from listing page")
        items = self.parse(r.text)
        if not items:
            raise SourceUnavailable("listing parsed but no items found (page may be JavaScript-rendered)")
        return items

    def fetch_document(self, client: httpx.Client, item: Item) -> tuple[bytes, str]:
        last = "no URL"
        for url in filter(None, [item.doc_url, item.alt_url]):
            try:
                d = safe_get(client, url, self.allowed)
            except (httpx.HTTPError, OSError) as e:
                last = type(e).__name__
                continue
            if d.status_code < 400 and d.content:
                return d.content, url.rsplit("/", 1)[-1].split("?")[0] or "document.html"
            last = f"HTTP {d.status_code}"
        raise SourceUnavailable(f"document download failed ({last})")
