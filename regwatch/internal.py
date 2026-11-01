"""Level 2 · Internal policy and control repository.

Organisations upload their internal documentation: policies, SOPs, business process documents,
AI governance policies, system inventories and ownership registers (the control catalogue is the
`control` table). Each document is split into referenceable sections so every impact mapping can
quote the exact internal text it relies on.

Owner resolution never invents a person: it uses the uploaded ownership register; if nothing in the
register matches, the item is flagged for manual assignment.
"""
from __future__ import annotations

import csv
import io
import re

import yaml

from . import RAW_DIR, SAMPLES_DIR, db, security
from .extract import classify_area
from .ingest import detect_kind, ingest_bytes, pages_from_text, segment_clauses, sha256_bytes

DOC_TYPES = {
    "policy": "Policy",
    "sop": "Standard operating procedure",
    "process": "Business process document",
    "ai_policy": "AI governance policy",
    "system_inventory": "System / application inventory",
    "ownership_register": "Ownership & responsibility register",
}
PREFIX = {"policy": "POL", "sop": "SOP", "process": "PRC", "ai_policy": "POL-AI", "system_inventory": "SYS",
          "ownership_register": "OWN"}
COMPONENT_TYPE = {"policy": "policy", "sop": "sop", "process": "process", "ai_policy": "ai_policy",
                  "system_inventory": "system"}
DEPTS = ["Risk", "Compliance", "AI Governance", "Ops"]
INTERNAL_SAMPLES = SAMPLES_DIR / "internal"


def _csv_rows(text: str) -> list[dict]:
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(io.StringIO(text))]


def _sections(doc_type: str, text: str, code: str) -> list[dict]:
    if doc_type == "system_inventory":
        out = []
        for i, r in enumerate(_csv_rows(text), start=1):
            name = r.get("system") or r.get("name") or f"System {i}"
            desc = r.get("description", "")
            extra = ", ".join(f"{k}: {v}" for k, v in r.items() if k not in ("system", "name", "description") and v)
            out.append({"ref": f"Row {i}", "title": name, "text": f"{name}: {desc}. {extra}".strip()})
        return out
    if doc_type == "ownership_register":
        return [{"ref": f"Row {i}", "title": r.get("person") or r.get("department", ""),
                 "text": ", ".join(f"{k}: {v}" for k, v in r.items() if v)} for i, r in enumerate(_csv_rows(text), start=1)]
    clauses = segment_clauses(pages_from_text(text.encode("utf-8")))
    return [{"ref": c.ref, "title": c.text.split(".")[0][:80], "text": c.text} for c in clauses if len(c.text) > 25]


def parse_owners(text: str) -> list[dict]:
    owners = []
    for r in _csv_rows(text):
        dept = r.get("department") or r.get("dept")
        if not dept:
            continue
        owners.append({"department": dept, "person": r.get("person") or r.get("owner") or "", "role": r.get("role", ""),
                       "business_areas": r.get("business_areas", ""), "keywords": r.get("keywords", ""),
                       "can_approve": (r.get("can_approve") or "").lower() in ("yes", "y", "true", "1")})
    return owners


def add_internal_document(conn, data: bytes, filename: str, doc_type: str, title: str = "",
                          owner_dept: str | None = None, business_area: str | None = None, code: str | None = None,
                          origin: str = "upload") -> tuple[int, bool, int]:
    """Validate, store and index an internal document. Returns (doc_id, is_new, sections)."""
    if doc_type not in DOC_TYPES:
        raise ValueError(f"Unknown document type {doc_type}")
    if origin == "upload":
        security.validate_upload(data, filename)
    h = sha256_bytes(data)
    is_csv = filename.lower().endswith(".csv")
    if is_csv:
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = ingest_bytes(data, filename).text
    title = (title or "").strip() or next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 5), filename)[:200]
    area = business_area or (None if doc_type in ("system_inventory", "ownership_register") else classify_area(text))
    if not code:
        n = conn.execute("SELECT COUNT(*) FROM internal_document WHERE doc_type=?", (doc_type,)).fetchone()[0] + 1
        code = f"{PREFIX[doc_type]}-{n:02d}"
    ext = ".csv" if is_csv else {"pdf": ".pdf", "docx": ".docx", "html": ".html", "text": ".txt"}[detect_kind(filename, data)]
    raw = RAW_DIR / f"internal_{h[:16]}{ext}"
    raw.write_bytes(data)
    summary = re.sub(r"\s+", " ", text)[:240]
    doc_id, is_new = db.upsert_internal_document(
        conn, code=code, title=title, doc_type=doc_type, business_area=area, owner_dept=owner_dept, summary=summary,
        file_name=filename, raw_path=f"data/raw/{raw.name}", content_hash=h, content_text=text, origin=origin)
    secs = _sections(doc_type, text, code)
    if is_new:
        db.replace_sections(conn, doc_id, secs)
        if doc_type == "ownership_register":
            db.replace_owners(conn, doc_id, parse_owners(text))
    return doc_id, is_new, len(secs)


def seed_internal(conn) -> int:
    manifest = yaml.safe_load((INTERNAL_SAMPLES / "manifest.yaml").read_text(encoding="utf-8"))
    n = 0
    for d in manifest["documents"]:
        _, new, _ = add_internal_document(
            conn, (INTERNAL_SAMPLES / d["file"]).read_bytes(), d["file"], d["doc_type"], d.get("title", ""),
            d.get("owner_dept"), d.get("business_area"), d.get("code"), origin="sample")
        n += new
    return n


# ---------------------------------------------------------------- ownership

def resolve_owner(conn, business_area: str | None, text: str, component_dept: str | None) -> dict:
    """Owner for a recommended change, from the ownership register only.

    1. The affected component's owning department, if the register has a person for it.
    2. Otherwise the register entry whose business areas / keywords best match the obligation.
    3. Otherwise: unassigned, flagged for manual assignment (never invented).
    """
    owners = [dict(o) for o in db.list_owners(conn)]
    if component_dept:
        hit = next((o for o in owners if o["department"].lower() == component_dept.lower()), None)
        if hit:
            return {"owner_dept": hit["department"], "owner_person": hit["person"], "owner_source": "register",
                    "owner_basis": f"owns {component_dept} documents (ownership register)"}
    low = (text or "").lower()
    best, best_score = None, 0
    for o in owners:
        areas = [a.strip().lower() for a in (o["business_areas"] or "").split(",") if a.strip()]
        kws = [k.strip().lower() for k in (o["keywords"] or "").split(",") if k.strip()]
        score = (2 if business_area and business_area.lower() in areas else 0) + sum(1 for k in kws if k in low)
        if score > best_score:
            best, best_score = o, score
    if best and best_score >= 2:
        return {"owner_dept": best["department"], "owner_person": best["person"], "owner_source": "register",
                "owner_basis": "matched business area / keywords in the ownership register"}
    return {"owner_dept": None, "owner_person": None, "owner_source": "unassigned",
            "owner_basis": "no matching entry in the ownership register: needs manual assignment"}


def approvers(conn) -> list[dict]:
    return [dict(o) for o in db.list_owners(conn) if o["can_approve"]]
