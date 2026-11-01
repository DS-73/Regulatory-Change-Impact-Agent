"""Build data/regwatch.db from config + seeded samples.

    python -m regwatch.seed           # create/refresh (keeps existing rows)
    python -m regwatch.seed --reset   # wipe the DB and rebuild from samples
"""
from __future__ import annotations

import sys

import yaml

from . import RAW_DIR, SAMPLES_DIR, db
from .assess import reassess_all, seed_controls
from .internal import seed_internal
from .pipeline import add_document, reprocess_all

UPLOAD_DEMO = SAMPLES_DIR / "upload_demo" / "cbn_aml_amendment_2026.docx"

UPLOAD_DEMO_PARAS = [
    "CENTRAL BANK OF NIGERIA",
    "Amendment to the Circular on Enhanced Customer Due Diligence (AML/CFT)",
    "Ref: FPR/DIR/ILLUS/CIR/2026/021",
    "Date: 20 October 2026",
    "[Illustrative sample prepared for the RegWatch demo; not an official CBN publication.]",
    "To: All Banks and Other Financial Institutions",
    "This Circular amends Circular Ref: FPR/DIR/ILLUS/CIR/2025/007 and restates it as amended.",
    "1. Background",
    "The Central Bank of Nigeria issues this Circular to strengthen customer due diligence and combat money "
    "laundering and terrorist financing.",
    "2. Customer due diligence",
    "2.1 Financial institutions shall verify the identity of every customer using the Bank Verification Number "
    "or the National Identification Number before establishing a business relationship.",
    "2.2 Financial institutions shall apply enhanced due diligence to politically exposed persons and high-risk customers.",
    "3. Suspicious transactions",
    "3.1 Financial institutions shall file suspicious transaction reports with the Nigerian Financial Intelligence "
    "Unit within 24 hours of detection.",
    "3.2 Financial institutions shall not disclose to a customer that a suspicious transaction report has been filed.",
    "4. Record keeping",
    "4.1 Financial institutions shall retain customer due diligence and transaction records for at least ten years.",
    "5. Compliance date",
    "5.1 Financial institutions shall comply with this Circular by 30 June 2027.",
    "6. Virtual assets",
    "6.1 Financial institutions shall apply enhanced due diligence to transactions with virtual asset service providers.",
]


def build_upload_demo() -> None:
    import docx

    UPLOAD_DEMO.parent.mkdir(parents=True, exist_ok=True)
    if UPLOAD_DEMO.exists():
        return
    d = docx.Document()
    for p in UPLOAD_DEMO_PARAS:
        d.add_paragraph(p)
    d.save(UPLOAD_DEMO)


def seed(reset: bool = False, verbose: bool = True) -> int:
    conn = db.connect()
    if reset:
        # Drop tables rather than deleting the file: a running app may hold it open (Windows).
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in db.ALL_TABLES + ["monitor_run"]:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        for f in RAW_DIR.glob("*"):
            f.unlink()
    db.init_db(conn)
    db.sync_sources(conn)
    seed_controls(conn)  # control catalogue + internal repository must exist before obligations are mapped
    n_int = seed_internal(conn)
    if verbose:
        print(f"internal repository: {n_int} document(s) loaded")
    build_upload_demo()
    manifest = yaml.safe_load((SAMPLES_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    added = 0
    for s in sorted(manifest["samples"], key=lambda x: str(x["published_on"])):
        data = (SAMPLES_DIR / s["file"]).read_bytes()
        meta = {k: s.get(k) for k in ("title", "reference_no", "doc_type", "business_area", "published_on", "effective_on")}
        pid, is_new = add_document(conn, data, s["file"], s["source"], meta, origin="sample")
        added += is_new
        if verbose:
            print(f"{'added' if is_new else 'exists'}  #{pid}  {s['source']:6} {s['title']}")
    conn.close()
    return added


def ensure_seeded() -> None:
    """Called by the app on start: build the DB if it doesn't exist yet."""
    conn = db.connect()
    recompute = db.init_db(conn)
    db.sync_sources(conn)
    if conn.execute("SELECT COUNT(*) FROM control").fetchone()[0] == 0:
        seed_controls(conn)
    new_internal = 0
    if conn.execute("SELECT COUNT(*) FROM internal_document").fetchone()[0] == 0:
        new_internal = seed_internal(conn)
    empty = conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0] == 0
    if not empty and conn.execute("SELECT COUNT(*) FROM comparison").fetchone()[0] == 0 and recompute:
        reprocess_all(conn)  # database from an older version: rebuild comparisons + mappings
    elif not empty and (new_internal or conn.execute("SELECT COUNT(*) FROM policy_mapping").fetchone()[0] == 0):
        reassess_all(conn)   # (re)build the Level 2 impact matrix
    conn.close()
    if empty:
        seed(verbose=False)
    conn.close()
    if empty:
        seed(verbose=False)


if __name__ == "__main__":
    n = seed(reset="--reset" in sys.argv)
    print(f"Done. {n} new publication(s).")
