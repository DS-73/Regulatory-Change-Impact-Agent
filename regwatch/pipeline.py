"""One entry point for every document, scraped or uploaded:
validate -> store -> ingest (OCR if needed) -> extract -> version -> compare -> assess controls."""
from __future__ import annotations

import re
from pathlib import Path

from . import RAW_DIR, db
from .assess import assess_publication
from .compare import compare_publication
from .extract import extract_publication
from .ingest import detect_kind, ingest_bytes, sha256_bytes
from .security import MAX_DOWNLOAD_BYTES, UnsafeFileError, validate_upload
from .textutil import parse_date

EXT = {"pdf": ".pdf", "docx": ".docx", "html": ".html", "text": ".txt"}


class NotRegulatoryDocument(ValueError):
    """A valid, safe file that does not look like a regulatory publication."""


MODALS = re.compile(r"\b(shall|must|is required to|are required to|may not|prohibited)\b|يجب|لا يجوز|يلتزم", re.I)
REG_TERMS = re.compile(r"\b(circular|regulation|regulations|directive|directions|guideline|guidelines|guidance|notice|"
                       r"central bank|regulator|regulatory|compliance|licensed|amend\w*|clause|article|paragraph|"
                       r"rulebook|framework|supervis\w*|financial institutions?|banks?)\b|تعميم|البنك المركزي|المادة", re.I)


def regulatory_signals(text: str, quality: str = "good") -> dict:
    """Cheap, explainable check that an upload is a regulatory publication (no model call)."""
    body = re.sub(r"\[p\.\d+\]", "", text or "")
    words = len(body.split())
    modals = len(MODALS.findall(body))
    terms = len(REG_TERMS.findall(body))
    reasons = []
    if quality in ("image_only_no_ocr", "empty") or words < 30:
        reasons.append("no readable text (scanned image or empty file)")
    if modals == 0:
        reasons.append("no obligation language (shall / must / is required to)")
    if terms < 3:
        reasons.append("almost no regulatory vocabulary (circular, regulation, compliance, central bank …)")
    ok = not reasons or (modals >= 2 and terms >= 3 and words >= 30)
    return {"ok": ok, "words": words, "modal_verbs": modals, "regulatory_terms": terms, "reasons": reasons}


def add_document(conn, data: bytes, filename: str, source_code: str, meta: dict | None = None,
                 origin: str = "upload", process: bool = True) -> tuple[int, bool]:
    """Insert a document if its content hash is new. Returns (publication_id, is_new)."""
    meta = meta or {}
    if origin == "upload":
        validate_upload(data, filename)  # raises UnsafeFileError before any parsing
    elif len(data) > MAX_DOWNLOAD_BYTES:
        raise UnsafeFileError("downloaded document exceeds size limit")
    h = sha256_bytes(data)
    existing = db.find_publication_by_hash(conn, h)
    if existing:
        return existing["id"], False

    src = db.source_by_code(conn, source_code)
    if src is None:
        raise ValueError(f"Unknown source {source_code}")
    doc = ingest_bytes(data, filename)
    if origin == "upload" and not meta.get("force"):
        sig = regulatory_signals(doc.text, doc.quality)
        if not sig["ok"]:
            raise NotRegulatoryDocument("This does not look like a regulatory publication: " + "; ".join(sig["reasons"]))
    raw_path = RAW_DIR / f"{h[:16]}{EXT[detect_kind(filename, data)]}"
    raw_path.write_bytes(data)

    pub_id = db.insert_publication(
        conn,
        source_id=src["id"],
        title=meta.get("title") or None,
        reference_no=meta.get("reference_no") or None,
        doc_type=meta.get("doc_type") or None,
        business_area=meta.get("business_area") or None,
        published_on=parse_date(meta.get("published_on")),
        effective_on=parse_date(meta.get("effective_on")),
        # Only plain http(s) links are kept, so a crafted "javascript:" URL can never reach the UI.
        source_url=(meta.get("source_url") or "").strip()
        if re.match(r"^https?://[^\s<>\"']+$", (meta.get("source_url") or "").strip()) else None,
        raw_path=str(Path(raw_path).relative_to(RAW_DIR.parent.parent)).replace("\\", "/"),
        content_hash=h,
        content_text=doc.text,
        origin=origin,
        status="discovered",
        ocr_used=1 if doc.ocr_used else 0,
        extraction_quality=doc.quality,
    )
    if process:
        process_publication(conn, pub_id)
    return pub_id, True


# ---------------------------------------------------------------- versions

def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9؀-ۿ]+", "", (s or "").lower())


def group_key(pub) -> str:
    """Same instrument = same source + reference number (else source URL, else title)."""
    if _norm(pub["reference_no"]):
        return f"{pub['source_id']}|ref:{_norm(pub['reference_no'])}"
    if pub["source_url"]:
        return f"{pub['source_id']}|url:{pub['source_url']}"
    return f"{pub['source_id']}|title:{_norm(pub['title'])[:120]}"


def assign_version(conn, pub_id: int) -> dict:
    """Put the publication in its document group. A new content hash under an existing group
    becomes the next version; earlier versions are kept untouched."""
    pub = conn.execute("SELECT * FROM publication WHERE id=?", (pub_id,)).fetchone()
    if pub["document_group_id"]:
        return {"group": pub["document_group_id"], "version": pub["version"]}
    key = group_key(pub)
    grp = conn.execute("SELECT id FROM document_group WHERE group_key=?", (key,)).fetchone()
    if grp is None:
        gid = conn.execute("INSERT INTO document_group (group_key, source_id, title) VALUES (?,?,?)",
                           (key, pub["source_id"], pub["title"])).lastrowid
        version = 1
    else:
        gid = grp[0]
        version = (conn.execute("SELECT MAX(version) FROM publication WHERE document_group_id=?",
                                (gid,)).fetchone()[0] or 0) + 1
    conn.execute("UPDATE publication SET document_group_id=?, version=? WHERE id=?", (gid, version, pub_id))
    conn.commit()
    return {"group": gid, "version": version}


def process_publication(conn, pub_id: int, compare: bool = True) -> dict:
    ex = extract_publication(conn, pub_id)
    ver = assign_version(conn, pub_id)
    cmp = compare_publication(conn, pub_id) if compare else None
    asm = assess_publication(conn, pub_id)
    return {"extract": ex, "version": ver, "compare": cmp, "assess": asm}


def reprocess_all(conn) -> int:
    """Re-run extraction, comparison and assessment over stored text, oldest first (no network)."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM publication ORDER BY COALESCE(published_on,'9999'), id").fetchall()]
    for i in ids:
        process_publication(conn, i)
    return len(ids)


def recompare_all(conn) -> int:
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM publication ORDER BY COALESCE(published_on,'9999'), id").fetchall()]
    for i in ids:
        compare_publication(conn, i)
    return len(ids)
