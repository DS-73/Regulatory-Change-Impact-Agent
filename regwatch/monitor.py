"""Capability 1: run each regulator's adapter, detect new items, hand them to the pipeline.

Reliability rules:
- Every cycle writes one monitoring_run row per source (ok | partial | error).
- If live retrieval fails (403, timeout, TLS error, empty parse) the source's seeded copies
  in data/samples/ are served instead and the run is marked `partial`, so a demo never
  stalls on a scraper.
- One bad item never stops a run. Dedupe: URL before download, SHA-256 after.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import yaml

from . import SAMPLES_DIR, db
from .adapters import SourceUnavailable, adapter_for
from .pipeline import add_document


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record(conn, src, started, new, seen, fallback, status, message):
    conn.execute(
        """INSERT INTO monitoring_run (source_id, started_at, finished_at, new_count, seen_count, fallback_count,
             status, message) VALUES (?,?,?,?,?,?,?,?)""",
        (src["id"], started, _now(), new, seen, fallback, status, message[:500]))
    conn.execute("UPDATE source SET last_run_at=?, last_status=? WHERE id=?",
                 (started, f"{status}: {message}"[:300], src["id"]))
    conn.commit()


def serve_samples(conn, code: str) -> tuple[int, int]:
    """Load this source's seeded documents (offline copies). Returns (new, already_present)."""
    manifest = yaml.safe_load((SAMPLES_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    new = seen = 0
    for s in sorted((x for x in manifest["samples"] if x["source"] == code), key=lambda x: str(x["published_on"])):
        meta = {k: s.get(k) for k in ("title", "reference_no", "doc_type", "business_area", "published_on", "effective_on")}
        _, is_new = add_document(conn, (SAMPLES_DIR / s["file"]).read_bytes(), s["file"], code, meta, origin="sample")
        new += is_new
        seen += not is_new
    return new, seen


def run_source(conn, cfg: dict, max_new: int = 3) -> dict:
    code = cfg["short_code"]
    src = db.source_by_code(conn, code)
    started = _now()
    new = seen = failed = 0
    adapter = adapter_for(cfg)
    try:
        with adapter.client() as client:
            items = adapter.fetch_listing(client)
            last_err = ""
            for it in items:
                if new >= max_new:
                    break
                if db.find_publication_by_url_or_ref(conn, it.url, None):
                    seen += 1
                    continue
                try:
                    content, fname = adapter.fetch_document(client, it)
                    _, is_new = add_document(conn, content, fname, code, {
                        "title": it.title, "published_on": it.published_on, "source_url": it.url}, origin="scrape")
                    new += is_new
                    seen += not is_new
                except Exception as e:  # one bad item never stops the run
                    failed += 1
                    last_err = f"{type(e).__name__}: {str(e)[:100]}"
        msg = f"live: {new} new, {seen} already seen ({len(items)} listed)"
        if failed:
            msg += f"; {failed} item(s) failed ({last_err})"
        status = "ok" if not failed or new else "partial"
        _record(conn, src, started, new, seen, 0, status, msg)
        return {"source": code, "status": status, "new": new, "seen": seen, "fallback": 0, "message": msg}
    except Exception as e:
        reason = str(e) if isinstance(e, SourceUnavailable) else f"{type(e).__name__}: {str(e)[:160]}" if e else "unknown error"
        try:
            f_new, f_seen = serve_samples(conn, code)
            msg = (f"live fetch failed ({reason[:140]}) → served seeded copies: "
                   f"{f_new} new, {f_seen} already present")
            _record(conn, src, started, f_new, f_seen, f_new + f_seen, "partial", msg)
            return {"source": code, "status": "partial", "new": f_new, "seen": f_seen,
                    "fallback": f_new + f_seen, "message": msg}
        except Exception as e2:
            msg = f"live fetch failed ({reason[:140]}); sample fallback failed: {type(e2).__name__}"
            _record(conn, src, started, 0, 0, 0, "error", msg)
            return {"source": code, "status": "error", "new": 0, "seen": 0, "fallback": 0, "message": msg}


def due_sources(conn) -> list[str]:
    out = []
    for s in conn.execute("SELECT * FROM source WHERE enabled=1").fetchall():
        if not s["last_run_at"]:
            out.append(s["short_code"])
            continue
        if datetime.now() - datetime.fromisoformat(s["last_run_at"]) >= timedelta(hours=s["interval_hours"] or 24):
            out.append(s["short_code"])
    return out


def run_monitoring(conn, codes: list[str] | None = None) -> list[dict]:
    cfg = db.load_sources_config()
    max_new = cfg.get("max_new_per_run", 3)
    return [run_source(conn, s, max_new) for s in cfg["sources"]
            if s.get("enabled", True) and (not codes or s["short_code"] in codes)]
