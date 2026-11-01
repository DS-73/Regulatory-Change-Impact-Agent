"""SQLite schema, connection and CRUD helpers. The dashboard only reads from here."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import yaml

from . import CONFIG_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
  id            INTEGER PRIMARY KEY,
  name          TEXT,
  short_code    TEXT UNIQUE,
  jurisdiction  TEXT,
  listing_url   TEXT,
  fetch_method  TEXT,
  enabled       INTEGER DEFAULT 1,
  interval_hours REAL DEFAULT 24,
  last_run_at   TIMESTAMP,
  last_status   TEXT
);

-- A regulatory instrument across its versions. A new content hash under an existing
-- group becomes version N+1; earlier versions are kept, never overwritten.
CREATE TABLE IF NOT EXISTS document_group (
  id          INTEGER PRIMARY KEY,
  group_key   TEXT UNIQUE,
  source_id   INTEGER REFERENCES source(id),
  title       TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publication (
  id            INTEGER PRIMARY KEY,
  source_id     INTEGER REFERENCES source(id),
  title         TEXT,
  reference_no  TEXT,
  doc_type      TEXT,
  business_area TEXT,
  published_on  DATE,
  effective_on  DATE,
  source_url    TEXT,
  raw_path      TEXT,
  content_hash  TEXT UNIQUE,
  content_text  TEXT,
  origin        TEXT DEFAULT 'scrape',      -- scrape | upload | sample
  extract_mode  TEXT,                       -- live | mock | mock (LLM failed)
  status        TEXT DEFAULT 'discovered',  -- discovered|extracted|compared|reviewed|extraction_failed
  discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  security_flags TEXT,                      -- JSON list of prompt-injection findings
  pii_redactions TEXT,                      -- JSON {kind: count} redacted before LLM calls
  document_group_id INTEGER REFERENCES document_group(id),
  version       INTEGER DEFAULT 1,
  ocr_used      INTEGER DEFAULT 0,
  extraction_quality TEXT                   -- good | ocr | low | image_only_no_ocr | empty
);

CREATE TABLE IF NOT EXISTS obligation (
  id             INTEGER PRIMARY KEY,
  publication_id INTEGER REFERENCES publication(id) ON DELETE CASCADE,
  text           TEXT,
  clause_ref     TEXT,
  obligation_type TEXT,
  applies_to     TEXT,
  deadline_on    DATE,
  confidence     REAL,
  grounding      REAL
);

-- One row per (new publication, prior publication) pair ...
CREATE TABLE IF NOT EXISTS comparison (
  id            INTEGER PRIMARY KEY,
  new_pub_id    INTEGER REFERENCES publication(id) ON DELETE CASCADE,
  prior_pub_id  INTEGER REFERENCES publication(id),
  overall_class TEXT,     -- 'changes' | no_material_change | no_reliable_match
  summary       TEXT,
  match_score   REAL,
  match_reason  TEXT,
  compare_mode  TEXT,     -- live | mock | mock (LLM failed) | retrieval
  link_type     TEXT,     -- auto | version | manual
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ... and one child row per changed clause, so an amending circular records all its changes.
CREATE TABLE IF NOT EXISTS comparison_clause (
  id            INTEGER PRIMARY KEY,
  comparison_id INTEGER REFERENCES comparison(id) ON DELETE CASCADE,
  prior_ref     TEXT,
  new_ref       TEXT,
  change_class  TEXT,
  summary       TEXT,
  old_clause    TEXT,     -- copied from the source document, never from model output
  new_clause    TEXT
);

-- Control library: the organisation's controls captured once as answered questions.
CREATE TABLE IF NOT EXISTS control (
  id            INTEGER PRIMARY KEY,
  code          TEXT UNIQUE,
  title         TEXT,
  owner_dept    TEXT,     -- Risk | Compliance | AI Governance | Ops
  answer        TEXT,     -- captured Q&A answer / policy statement
  last_reviewed DATE
);

-- LLM Q&A: does the current control set already satisfy this obligation?
CREATE TABLE IF NOT EXISTS obligation_assessment (
  id                 INTEGER PRIMARY KEY,
  obligation_id      INTEGER REFERENCES obligation(id) ON DELETE CASCADE,
  control_id         INTEGER REFERENCES control(id),
  status             TEXT,   -- compliant | partial | gap
  rationale          TEXT,
  recommended_change TEXT,
  confidence         REAL,
  mode               TEXT    -- live | mock | mock (LLM failed)
);

-- Ownership of a control gap / partial: who fixes it and by when.
CREATE TABLE IF NOT EXISTS action (
  id             INTEGER PRIMARY KEY,
  assessment_id  INTEGER UNIQUE REFERENCES obligation_assessment(id) ON DELETE CASCADE,
  owner_dept     TEXT,
  assignee       TEXT,
  due_on         DATE,
  updated_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review (
  id           INTEGER PRIMARY KEY,
  object_type  TEXT,
  object_id    INTEGER,
  state        TEXT DEFAULT 'pending',
  reviewer     TEXT,
  note         TEXT,
  decided_at   TIMESTAMP
);

-- Every monitoring cycle writes one row per source.
CREATE TABLE IF NOT EXISTS monitoring_run (
  id             INTEGER PRIMARY KEY,
  source_id      INTEGER REFERENCES source(id),
  started_at     TIMESTAMP,
  finished_at    TIMESTAMP,
  new_count      INTEGER,
  seen_count     INTEGER,
  fallback_count INTEGER DEFAULT 0,
  status         TEXT,      -- ok | partial | error | manual
  message        TEXT
);

-- Audit trail of every LLM call: hashes and sizes only, never prompt content or keys.
CREATE TABLE IF NOT EXISTS llm_call (
  id            INTEGER PRIMARY KEY,
  at            TIMESTAMP,
  purpose       TEXT,     -- extract | pair | assess
  model         TEXT,
  doc_hash      TEXT,
  prompt_sha    TEXT,
  prompt_chars  INTEGER,
  cached        INTEGER,
  status        TEXT,     -- ok | error | rate_limited
  latency_ms    INTEGER,
  detail        TEXT
);

CREATE INDEX IF NOT EXISTS ix_obl_pub ON obligation(publication_id);
CREATE INDEX IF NOT EXISTS ix_cmp_new ON comparison(new_pub_id);
CREATE INDEX IF NOT EXISTS ix_cc_cmp ON comparison_clause(comparison_id);
CREATE INDEX IF NOT EXISTS ix_assess_obl ON obligation_assessment(obligation_id);
CREATE INDEX IF NOT EXISTS ix_review_obj ON review(object_type, object_id);

-- ---------------- Level 2: internal policy & control repository ----------------
-- Policies, SOPs, process documents, AI governance policies, system inventories,
-- ownership registers (the control catalogue lives in `control`).
CREATE TABLE IF NOT EXISTS internal_document (
  id            INTEGER PRIMARY KEY,
  code          TEXT,                 -- short reference, e.g. POL-AI-01
  title         TEXT NOT NULL,
  doc_type      TEXT,                 -- policy | sop | process | ai_policy | system_inventory | ownership_register
  business_area TEXT,
  owner_dept    TEXT,
  summary       TEXT,
  file_name     TEXT,
  raw_path      TEXT,
  content_hash  TEXT UNIQUE,          -- same file uploaded twice is not duplicated
  content_text  TEXT,
  version       INTEGER DEFAULT 1,
  origin        TEXT DEFAULT 'upload', -- upload | sample
  uploaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_reviewed DATE
);

CREATE TABLE IF NOT EXISTS document_section (
  id            INTEGER PRIMARY KEY,
  document_id   INTEGER REFERENCES internal_document(id) ON DELETE CASCADE,
  section_ref   TEXT,                 -- 'Clause 4.2' / 'Row 3'
  section_title TEXT,
  section_text  TEXT
);

-- Ownership register: who is responsible for what. Loaded from an uploaded register.
CREATE TABLE IF NOT EXISTS owner (
  id             INTEGER PRIMARY KEY,
  department     TEXT,
  person         TEXT,
  role           TEXT,
  business_areas TEXT,                -- comma-separated: AI, lending, AML ...
  keywords       TEXT,                -- comma-separated topics this owner covers
  can_approve    INTEGER DEFAULT 0,   -- authorised to approve recommended changes
  source_doc_id  INTEGER REFERENCES internal_document(id) ON DELETE CASCADE
);

-- Regulatory impact matrix: one row per (obligation x affected internal component).
CREATE TABLE IF NOT EXISTS policy_mapping (
  id                    INTEGER PRIMARY KEY,
  obligation_id         INTEGER REFERENCES obligation(id) ON DELETE CASCADE,
  internal_doc_id       INTEGER REFERENCES internal_document(id) ON DELETE CASCADE,
  section_id            INTEGER REFERENCES document_section(id) ON DELETE CASCADE,
  control_id            INTEGER REFERENCES control(id) ON DELETE CASCADE,
  component_type        TEXT,         -- policy | sop | process | ai_policy | system | control | none
  component_label       TEXT,         -- 'POL-AI-01 §4.2 Model validation' / 'AIG-03 AI model validation'
  verdict               TEXT,         -- addressed | partial | gap | insufficient_evidence
  reason                TEXT,         -- why this component is considered impacted
  existing_requirement  TEXT,         -- verbatim internal text
  compliance_gap        TEXT,         -- what is missing and why it is insufficient
  recommended_action    TEXT,
  proposed_wording      TEXT,         -- draft policy / control wording
  business_area         TEXT,
  implementation_priority TEXT,       -- High | Medium | Low
  confidence            REAL,
  evidence_refs         TEXT,         -- JSON list of references (regulatory clause + internal refs)
  mode                  TEXT,         -- live | mock | mock (LLM failed)
  approval_status       TEXT DEFAULT 'draft',  -- draft | approved | rejected
  approved_by           TEXT,
  approved_at           TIMESTAMP,
  approval_note         TEXT,
  owner_dept            TEXT,
  owner_person          TEXT,
  owner_source          TEXT,         -- register | reviewer | unassigned
  due_on                DATE,
  implementation_status TEXT DEFAULT 'not_started',  -- not_started | in_progress | implemented | verified
  updated_at            TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_map_obl ON policy_mapping(obligation_id);
CREATE INDEX IF NOT EXISTS ix_map_doc ON policy_mapping(internal_doc_id);
CREATE INDEX IF NOT EXISTS ix_sec_doc ON document_section(document_id);
"""

# Level 2 tables from an earlier attempt had no usable data and an incompatible shape;
# they are dropped and recreated when found.
LEVEL2_REQUIRED = {"internal_document": "code", "policy_mapping": "verdict", "document_section": "section_ref"}

# Columns added after earlier releases; applied to existing databases on start.
MIGRATIONS = [
    ("publication", "security_flags", "TEXT"),
    ("publication", "pii_redactions", "TEXT"),
    ("publication", "document_group_id", "INTEGER"),
    ("publication", "version", "INTEGER DEFAULT 1"),
    ("publication", "ocr_used", "INTEGER DEFAULT 0"),
    ("publication", "extraction_quality", "TEXT"),
    ("obligation", "grounding", "REAL"),
    ("obligation_assessment", "proposed_wording", "TEXT"),
    ("obligation_assessment", "implementation_priority", "TEXT"),
]

ALL_TABLES = ["review", "action", "policy_mapping", "owner", "document_section", "internal_document",
              "obligation_assessment", "comparison_clause", "comparison", "obligation", "control",
              "monitoring_run", "llm_call", "publication", "document_group", "source", "compliance_gap"]


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path: Path | str = DB_PATH):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def init_db(conn: sqlite3.Connection) -> bool:
    """Create / migrate the schema. Returns True if comparisons must be recomputed."""
    recompute = False
    # v1 comparison rows held one clause each; v2 splits them into comparison + comparison_clause.
    if "change_class" in _cols(conn, "comparison"):
        conn.execute("DELETE FROM review WHERE object_type='comparison'") if _cols(conn, "review") else None
        conn.execute("DROP TABLE comparison")
        recompute = True
    if _cols(conn, "monitor_run") and not _cols(conn, "monitoring_run"):
        conn.execute("DROP TABLE monitor_run")
    for table, col in LEVEL2_REQUIRED.items():
        existing = _cols(conn, table)
        if existing and col not in existing:
            conn.execute("PRAGMA foreign_keys = OFF")
            for t in ("policy_mapping", "compliance_gap", "owner", "document_section", "internal_document"):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            conn.execute("PRAGMA foreign_keys = ON")
            recompute = True
            break
    conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in _cols(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")  # plain types only: never swallow errors
    conn.commit()
    return recompute


def log_llm_call(**fields) -> None:
    """Append to the LLM audit trail. Uses its own short-lived connection."""
    try:
        with session(globals()["DB_PATH"]) as c:  # read at call time so a scratch database can be swapped in
            cols = ",".join(fields)
            c.execute(f"INSERT INTO llm_call ({cols}) VALUES ({','.join('?' for _ in fields)})",
                      tuple(fields.values()))
    except sqlite3.Error:
        pass  # auditing must never break the pipeline


def load_sources_config() -> dict:
    with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sync_sources(conn: sqlite3.Connection) -> None:
    """Upsert sources from sources.yaml into the source table."""
    cfg = load_sources_config()
    for s in cfg["sources"]:
        conn.execute(
            """INSERT INTO source (name, short_code, jurisdiction, listing_url, fetch_method, enabled, interval_hours)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(short_code) DO UPDATE SET name=excluded.name, jurisdiction=excluded.jurisdiction,
                 listing_url=excluded.listing_url, fetch_method=excluded.fetch_method,
                 enabled=excluded.enabled, interval_hours=excluded.interval_hours""",
            (s["name"], s["short_code"], s["jurisdiction"], s["listing_url"], s.get("fetch_method", "scrape"),
             1 if s.get("enabled", True) else 0, s.get("interval_hours", 24)),
        )
    conn.commit()


def ensure_manual_source(conn, code: str, name: str, jurisdiction: str) -> str:
    """Register a regulator that has no live adapter (manual upload only). Returns its code."""
    code = re.sub(r"[^A-Za-z0-9_-]", "", (code or "").upper())[:12] or "OTHER"
    if not source_by_code(conn, code):
        conn.execute("INSERT INTO source (name, short_code, jurisdiction, listing_url, fetch_method, enabled, interval_hours) "
                     "VALUES (?,?,?,?,?,?,?)", ((name or code).strip()[:120], code, (jurisdiction or "Other").strip()[:40],
                                                "", "manual", 0, 0))
        conn.commit()
    return code


def source_by_code(conn, code: str):
    return conn.execute("SELECT * FROM source WHERE short_code=?", (code,)).fetchone()


# ---------- publications ----------

def find_publication_by_hash(conn, content_hash: str):
    return conn.execute("SELECT * FROM publication WHERE content_hash=?", (content_hash,)).fetchone()


def find_publication_by_url_or_ref(conn, url: str | None, ref: str | None):
    if url:
        row = conn.execute("SELECT id FROM publication WHERE source_url=?", (url,)).fetchone()
        if row:
            return row
    if ref:
        return conn.execute("SELECT id FROM publication WHERE reference_no=?", (ref,)).fetchone()
    return None


def insert_publication(conn, **fields) -> int:
    cols = ",".join(fields.keys())
    qs = ",".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO publication ({cols}) VALUES ({qs})", tuple(fields.values()))
    conn.commit()
    return cur.lastrowid


def update_publication(conn, pub_id: int, **fields) -> None:
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE publication SET {sets} WHERE id=?", (*fields.values(), pub_id))
    conn.commit()


def get_publication(conn, pub_id: int):
    return conn.execute(
        """SELECT p.*, s.short_code AS regulator, s.name AS regulator_name, s.jurisdiction
           FROM publication p JOIN source s ON s.id=p.source_id WHERE p.id=?""",
        (pub_id,),
    ).fetchone()


def versions_of(conn, group_id: int | None):
    if not group_id:
        return []
    return conn.execute("SELECT id, version, title, published_on, discovered_at, content_hash FROM publication "
                        "WHERE document_group_id=? ORDER BY version", (group_id,)).fetchall()


def replace_obligations(conn, pub_id: int, obligations: list[dict]) -> None:
    conn.execute("DELETE FROM obligation WHERE publication_id=?", (pub_id,))
    conn.executemany(
        """INSERT INTO obligation (publication_id, text, clause_ref, obligation_type, applies_to, deadline_on,
             confidence, grounding)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(pub_id, o.get("text"), o.get("clause_ref"), o.get("obligation_type"), o.get("applies_to"),
          o.get("deadline_on"), o.get("confidence"), o.get("grounding")) for o in obligations],
    )
    conn.commit()


def replace_comparison(conn, pub_id: int, header: dict | None, clauses: list[dict]) -> None:
    """Replace the comparison for a publication (header + clause rows). Clears stale reviews."""
    old = [r[0] for r in conn.execute("SELECT id FROM comparison WHERE new_pub_id=?", (pub_id,))]
    for cid in old:
        conn.execute("DELETE FROM review WHERE object_type='comparison_clause' AND object_id IN "
                     "(SELECT id FROM comparison_clause WHERE comparison_id=?)", (cid,))
        conn.execute("DELETE FROM review WHERE object_type='comparison' AND object_id=?", (cid,))
    conn.execute("DELETE FROM comparison WHERE new_pub_id=?", (pub_id,))
    if header is not None:
        cur = conn.execute(
            """INSERT INTO comparison (new_pub_id, prior_pub_id, overall_class, summary, match_score, match_reason,
                 compare_mode, link_type) VALUES (?,?,?,?,?,?,?,?)""",
            (pub_id, header.get("prior_pub_id"), header["overall_class"], header.get("summary"),
             header.get("match_score"), header.get("match_reason"), header.get("compare_mode"),
             header.get("link_type", "auto")))
        conn.executemany(
            """INSERT INTO comparison_clause (comparison_id, prior_ref, new_ref, change_class, summary, old_clause,
                 new_clause) VALUES (?,?,?,?,?,?,?)""",
            [(cur.lastrowid, c.get("prior_ref"), c.get("new_ref"), c["change_class"], c.get("summary"),
              c.get("old_clause"), c.get("new_clause")) for c in clauses])
    conn.commit()


# ---------- controls & assessments ----------

def list_controls(conn):
    return conn.execute("SELECT * FROM control ORDER BY code").fetchall()


def upsert_control(conn, code: str, title: str, owner_dept: str, answer: str, last_reviewed: str | None = None) -> int:
    conn.execute(
        """INSERT INTO control (code, title, owner_dept, answer, last_reviewed) VALUES (?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET title=excluded.title, owner_dept=excluded.owner_dept,
             answer=excluded.answer, last_reviewed=excluded.last_reviewed""",
        (code, title, owner_dept, answer, last_reviewed or datetime.now().date().isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM control WHERE code=?", (code,)).fetchone()[0]


def replace_assessments(conn, pub_id: int, rows: list[dict]) -> None:
    ids = [r[0] for r in conn.execute("SELECT id FROM obligation WHERE publication_id=?", (pub_id,))]
    if ids:
        q = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM review WHERE object_type='assessment' AND object_id IN "
                     f"(SELECT id FROM obligation_assessment WHERE obligation_id IN ({q}))", ids)
        conn.execute(f"DELETE FROM obligation_assessment WHERE obligation_id IN ({q})", ids)
    conn.executemany(
        """INSERT INTO obligation_assessment (obligation_id, control_id, status, rationale, recommended_change,
             confidence, mode, proposed_wording, implementation_priority) VALUES (?,?,?,?,?,?,?,?,?)""",
        [(r["obligation_id"], r.get("control_id"), r["status"], r.get("rationale"), r.get("recommended_change"),
          r.get("confidence"), r.get("mode"), r.get("proposed_wording"), r.get("implementation_priority")) for r in rows])
    conn.commit()


def upsert_action(conn, assessment_id: int, owner_dept: str, assignee: str, due_on: str | None) -> None:
    conn.execute(
        """INSERT INTO action (assessment_id, owner_dept, assignee, due_on, updated_at) VALUES (?,?,?,?,?)
           ON CONFLICT(assessment_id) DO UPDATE SET owner_dept=excluded.owner_dept, assignee=excluded.assignee,
             due_on=excluded.due_on, updated_at=excluded.updated_at""",
        (assessment_id, owner_dept, assignee, due_on, datetime.now().isoformat(timespec="seconds")))
    conn.commit()


# ---------- Level 2: internal documents ----------

def list_internal_documents(conn):
    return conn.execute("""SELECT d.*, (SELECT COUNT(*) FROM document_section s WHERE s.document_id=d.id) AS sections
                           FROM internal_document d ORDER BY d.doc_type, d.title""").fetchall()


def upsert_internal_document(conn, *, code: str, title: str, doc_type: str, business_area: str | None,
                             owner_dept: str | None, summary: str, file_name: str, raw_path: str,
                             content_hash: str, content_text: str, origin: str = "upload") -> tuple[int, bool]:
    """Insert a document unless the same content is already stored. Returns (id, is_new)."""
    row = conn.execute("SELECT id FROM internal_document WHERE content_hash=?", (content_hash,)).fetchone()
    if row:
        return row[0], False
    prev = conn.execute("SELECT MAX(version) FROM internal_document WHERE code=?", (code,)).fetchone()[0]
    cur = conn.execute(
        """INSERT INTO internal_document (code, title, doc_type, business_area, owner_dept, summary, file_name,
             raw_path, content_hash, content_text, version, origin, last_reviewed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, title, doc_type, business_area, owner_dept, summary, file_name, raw_path, content_hash, content_text,
         (prev or 0) + 1, origin, datetime.now().date().isoformat()))
    conn.commit()
    return cur.lastrowid, True


def replace_sections(conn, document_id: int, sections: list[dict]) -> None:
    conn.execute("DELETE FROM document_section WHERE document_id=?", (document_id,))
    conn.executemany("INSERT INTO document_section (document_id, section_ref, section_title, section_text) VALUES (?,?,?,?)",
                     [(document_id, s["ref"], s.get("title", ""), s["text"]) for s in sections])
    conn.commit()


def sections_with_docs(conn):
    """All indexed sections of the current version of each internal document (mapping candidates)."""
    return conn.execute("""SELECT s.id AS section_id, s.section_ref, s.section_title, s.section_text,
                             d.id AS doc_id, d.code, d.title AS doc_title, d.doc_type, d.business_area, d.owner_dept
                           FROM document_section s JOIN internal_document d ON d.id=s.document_id
                           WHERE d.doc_type <> 'ownership_register'
                             AND d.version = (SELECT MAX(version) FROM internal_document x WHERE x.code=d.code)""").fetchall()


def list_owners(conn):
    return conn.execute("SELECT * FROM owner ORDER BY department, person").fetchall()


def replace_owners(conn, source_doc_id: int, owners: list[dict]) -> None:
    conn.execute("DELETE FROM owner")  # the latest uploaded register is authoritative
    conn.executemany(
        "INSERT INTO owner (department, person, role, business_areas, keywords, can_approve, source_doc_id) VALUES (?,?,?,?,?,?,?)",
        [(o["department"], o["person"], o.get("role", ""), o.get("business_areas", ""), o.get("keywords", ""),
          1 if o.get("can_approve") else 0, source_doc_id) for o in owners])
    conn.commit()


# ---------- Level 2: impact matrix (policy mapping) ----------

REVIEWER_FIELDS = ("approval_status", "approved_by", "approved_at", "approval_note", "owner_dept", "owner_person",
                   "owner_source", "due_on", "implementation_status", "proposed_wording")


def replace_policy_mappings(conn, obligation_id: int, rows: list[dict]) -> None:
    """Replace the machine-generated mappings for an obligation, keeping any reviewer decisions
    (approval, owner, deadline, implementation status) recorded on the same component."""
    keep = {}
    for r in conn.execute("SELECT * FROM policy_mapping WHERE obligation_id=?", (obligation_id,)):
        if (r["approval_status"] != "draft" or r["owner_source"] == "reviewer"
                or r["implementation_status"] != "not_started" or r["approval_note"]):
            keep[(r["section_id"], r["control_id"])] = {k: r[k] for k in REVIEWER_FIELDS}
    conn.execute("DELETE FROM policy_mapping WHERE obligation_id=?", (obligation_id,))
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        r = {**r, **keep.get((r.get("section_id"), r.get("control_id")), {})}
        conn.execute(
            """INSERT INTO policy_mapping (obligation_id, internal_doc_id, section_id, control_id, component_type,
                 component_label, verdict, reason, existing_requirement, compliance_gap, recommended_action,
                 proposed_wording, business_area, implementation_priority, confidence, evidence_refs, mode,
                 approval_status, approved_by, approved_at, approval_note, owner_dept, owner_person, owner_source,
                 due_on, implementation_status, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (obligation_id, r.get("internal_doc_id"), r.get("section_id"), r.get("control_id"), r.get("component_type"),
             r.get("component_label"), r["verdict"], r.get("reason"), r.get("existing_requirement"),
             r.get("compliance_gap"), r.get("recommended_action"), r.get("proposed_wording"), r.get("business_area"),
             r.get("implementation_priority"), r.get("confidence"), json.dumps(r.get("evidence_refs") or []),
             r.get("mode"), r.get("approval_status", "draft"), r.get("approved_by"), r.get("approved_at"),
             r.get("approval_note"), r.get("owner_dept"), r.get("owner_person"), r.get("owner_source", "unassigned"),
             r.get("due_on"), r.get("implementation_status", "not_started"), now))
    conn.commit()


def update_mapping(conn, mapping_id: int, **fields) -> None:
    allowed = set(REVIEWER_FIELDS)
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE policy_mapping SET {sets} WHERE id=?", (*fields.values(), mapping_id))
    conn.commit()


def mappings_frame_sql() -> str:
    return """
    SELECT m.*, o.text AS obligation, o.clause_ref, o.obligation_type, o.deadline_on, o.publication_id AS pub_id,
           p.title AS pub_title, p.reference_no, p.effective_on, p.business_area AS pub_area,
           s.short_code AS regulator, s.jurisdiction,
           d.code AS doc_code, d.title AS doc_title, ds.section_ref, k.code AS control_code, k.title AS control_title
    FROM policy_mapping m
    JOIN obligation o ON o.id=m.obligation_id
    JOIN publication p ON p.id=o.publication_id
    JOIN source s ON s.id=p.source_id
    LEFT JOIN internal_document d ON d.id=m.internal_doc_id
    LEFT JOIN document_section ds ON ds.id=m.section_id
    LEFT JOIN control k ON k.id=m.control_id
    """


# ---------- review ----------

def set_review(conn, object_type: str, object_id: int, state: str, reviewer: str = "", note: str = "") -> None:
    conn.execute(
        "INSERT INTO review (object_type, object_id, state, reviewer, note, decided_at) VALUES (?,?,?,?,?,?)",
        (object_type, object_id, state, reviewer, note, datetime.now().isoformat(timespec="seconds")),
    )
    if object_type == "publication" and state == "approved":
        conn.execute("UPDATE publication SET status='reviewed' WHERE id=?", (object_id,))
    conn.commit()


def review_history(conn, object_type: str, object_id: int):
    return conn.execute(
        "SELECT * FROM review WHERE object_type=? AND object_id=? ORDER BY id DESC", (object_type, object_id)
    ).fetchall()


LATEST_REVIEW_SQL = """
COALESCE((SELECT r.state FROM review r WHERE r.object_type='{t}' AND r.object_id={col}
          ORDER BY r.id DESC LIMIT 1), 'pending')
"""


def latest_review_expr(object_type: str, id_col: str) -> str:
    return LATEST_REVIEW_SQL.format(t=object_type, col=id_col)


def publications_frame_sql() -> str:
    return f"""
    SELECT p.id, p.title, p.reference_no, p.doc_type, p.business_area, p.published_on, p.effective_on,
           p.source_url, p.raw_path, p.status, p.origin, p.extract_mode, p.discovered_at, p.security_flags,
           p.version, p.document_group_id, p.ocr_used, p.extraction_quality,
           s.short_code AS regulator, s.jurisdiction,
           {latest_review_expr('publication', 'p.id')} AS review_state,
           (SELECT COUNT(*) FROM obligation o WHERE o.publication_id=p.id) AS obligations,
           COALESCE((SELECT GROUP_CONCAT(DISTINCT cc.change_class) FROM comparison_clause cc
                     JOIN comparison c ON c.id=cc.comparison_id WHERE c.new_pub_id=p.id), '')
           || ',' ||
           COALESCE((SELECT GROUP_CONCAT(DISTINCT c.overall_class) FROM comparison c
                     WHERE c.new_pub_id=p.id AND c.overall_class<>'changes'), '') AS change_classes,
           (SELECT COUNT(*) FROM obligation_assessment a JOIN obligation o ON o.id=a.obligation_id
             WHERE o.publication_id=p.id AND a.status='gap') AS gaps,
           (SELECT COUNT(*) FROM obligation_assessment a JOIN obligation o ON o.id=a.obligation_id
             WHERE o.publication_id=p.id AND a.status='partial') AS partials,
           p.content_text
    FROM publication p JOIN source s ON s.id=p.source_id
    """


def comparisons_frame_sql() -> str:
    """One row per reviewable change: clause rows, plus clause-less comparisons (unmatched / no change)."""
    common = """
           c.id AS comparison_id, c.new_pub_id, c.prior_pub_id, c.match_score, c.match_reason, c.compare_mode,
           c.link_type, c.created_at,
           np.title AS new_title, np.reference_no AS new_ref, np.published_on, np.business_area,
           pp.title AS prior_title, pp.reference_no AS prior_ref,
           s.short_code AS regulator, s.jurisdiction"""
    joins = """
    JOIN publication np ON np.id=c.new_pub_id
    JOIN source s ON s.id=np.source_id
    LEFT JOIN publication pp ON pp.id=c.prior_pub_id"""
    return f"""
    SELECT cc.id AS id, 'comparison_clause' AS object_type, cc.change_class, cc.summary,
           COALESCE(cc.new_ref, cc.prior_ref) AS clause_ref, cc.prior_ref AS prior_clause_ref,
           cc.new_ref AS new_clause_ref, cc.old_clause, cc.new_clause,
           {latest_review_expr('comparison_clause', 'cc.id')} AS review_state, {common}
    FROM comparison_clause cc JOIN comparison c ON c.id=cc.comparison_id {joins}
    UNION ALL
    SELECT c.id AS id, 'comparison' AS object_type, c.overall_class AS change_class, c.summary,
           NULL, NULL, NULL, NULL, NULL,
           {latest_review_expr('comparison', 'c.id')} AS review_state, {common}
    FROM comparison c {joins}
    WHERE NOT EXISTS (SELECT 1 FROM comparison_clause x WHERE x.comparison_id=c.id)
    """


def assessments_frame_sql() -> str:
    return f"""
    SELECT a.id, a.status, a.rationale, a.recommended_change, a.confidence, a.mode,
           a.proposed_wording, a.implementation_priority,
           o.id AS obligation_id, o.text AS obligation, o.clause_ref, o.obligation_type, o.deadline_on,
           p.id AS pub_id, p.title AS pub_title, s.short_code AS regulator, s.jurisdiction, p.business_area,
           k.code AS control_code, k.title AS control_title, k.owner_dept,
           o.applies_to, o.grounding, p.effective_on, p.reference_no, p.doc_type,
           ac.owner_dept AS assigned_dept, ac.assignee, ac.due_on AS action_due,
           {latest_review_expr('assessment', 'a.id')} AS review_state
    FROM obligation_assessment a
    LEFT JOIN action ac ON ac.assessment_id=a.id
    JOIN obligation o ON o.id=a.obligation_id
    JOIN publication p ON p.id=o.publication_id
    JOIN source s ON s.id=p.source_id
    LEFT JOIN control k ON k.id=a.control_id
    """
