"""Each source adapter is tested on its own, offline, plus the monitor's sample fallback.

Run:  .venv/Scripts/python -m pytest -q
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from regwatch import db
from regwatch.adapters import ADAPTERS, SourceUnavailable, adapter_for
from regwatch.adapters.base import safe_get

FIX = Path(__file__).parent / "fixtures"
CFG = {s["short_code"]: s for s in db.load_sources_config()["sources"]}

from fixtures import generic_listings as G  # noqa: E402


def test_every_configured_source_has_its_own_adapter():
    assert set(CFG) == set(ADAPTERS)
    assert len({type(adapter_for(c)) for c in CFG.values()}) == 4


def test_rbi_adapter_parses_real_listing():
    items = adapter_for(CFG["RBI"]).parse((FIX / "rbi_notifications.html").read_text(encoding="utf-8"))
    assert len(items) >= 5
    first = items[0]
    assert first.url.startswith("https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=")
    assert first.doc_url == first.url              # HTML detail page is the primary document
    assert first.alt_url and first.alt_url.lower().endswith(".pdf")
    assert first.published_on and first.published_on.startswith("2026-")
    assert all(i.title for i in items)


def test_sama_adapter_keeps_circulars_and_pdfs_only():
    items = adapter_for(CFG["SAMA"]).parse(G.SAMA)
    urls = [i.url for i in items]
    assert urls == ["https://sama.gov.sa/en-US/Laws/Circulars/Circular-2026-41.aspx",
                    "https://sama.gov.sa/en-US/Laws/Documents/AI-Principles.pdf"]
    assert items[0].published_on == "2026-05-12"


def test_cbuae_adapter_keeps_rulebook_and_notices():
    items = adapter_for(CFG["CBUAE"]).parse(G.CBUAE)
    assert [i.title for i in items] == ["Outsourcing Regulation", "Notice 2026/19 on AI"]
    assert items[1].published_on == "2026-09-15"


def test_cbuae_adapter_skips_sector_menu_pages_and_finds_them_for_crawling():
    a = adapter_for(CFG["CBUAE"])
    assert a.parse(G.CBUAE_LANDING) == []                      # menu categories are not regulations
    assert a.sector_pages(G.CBUAE_LANDING) == ["https://rulebook.centralbank.ae/en/rulebook/banking",
                                               "https://rulebook.centralbank.ae/en/rulebook/insurance"]


def test_cbn_adapter_keeps_pdf_links_with_dates():
    items = adapter_for(CFG["CBN"]).parse(G.CBN)
    assert len(items) == 2 and all(i.url.endswith(".pdf") for i in items)
    assert items[1].published_on == "2026-10-02"


def test_empty_listing_is_reported_as_unavailable(monkeypatch):
    a = adapter_for(CFG["CBN"])

    class R:
        status_code, text = 200, "<html><body>loading…</body></html>"

    monkeypatch.setattr("regwatch.adapters.base.safe_get", lambda *a, **k: R())
    with pytest.raises(SourceUnavailable):
        a.fetch_listing(None)


def test_http_403_is_reported_as_unavailable(monkeypatch):
    a = adapter_for(CFG["CBUAE"])

    class R:
        status_code, text = 403, "Forbidden"

    monkeypatch.setattr("regwatch.adapters.base.safe_get", lambda *a, **k: R())
    monkeypatch.setattr("regwatch.adapters.cbuae.safe_get", lambda *a, **k: R())
    with pytest.raises(SourceUnavailable, match="403"):
        a.fetch_listing(None)


def test_ssrf_guard_blocks_foreign_and_internal_hosts():
    for url in ("https://evil.example/x", "http://127.0.0.1/admin", "file:///etc/passwd"):
        with pytest.raises(SourceUnavailable):
            safe_get(None, url, ["rbi.org.in"])


def test_monitor_falls_back_to_samples_and_marks_partial(tmp_path, monkeypatch):
    """A failing source serves its seeded copies and writes a `partial` monitoring_run row."""
    import regwatch.monitor as mon
    from regwatch.assess import seed_controls

    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    db.sync_sources(conn)
    seed_controls(conn)

    def boom(self, client):
        raise SourceUnavailable("HTTP 403 from listing page")

    monkeypatch.setattr("regwatch.adapters.base.SourceAdapter.fetch_listing", boom)
    monkeypatch.setattr("regwatch.adapters.cbuae.CBUAEAdapter.fetch_listing", boom)
    monkeypatch.setattr("regwatch.pipeline.RAW_DIR", tmp_path)
    res = mon.run_source(conn, CFG["CBUAE"])
    assert res["status"] == "partial" and res["new"] == 2
    row = conn.execute("SELECT status, fallback_count, message FROM monitoring_run").fetchone()
    assert row["status"] == "partial" and row["fallback_count"] == 2 and "403" in row["message"]
    # second run: same copies are recognised by content hash, nothing duplicated
    res2 = mon.run_source(conn, CFG["CBUAE"])
    assert res2["new"] == 0 and res2["seen"] == 2
    assert conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0] == 2
    assert isinstance(conn, sqlite3.Connection)
