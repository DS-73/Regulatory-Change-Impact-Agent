"""End-to-end pipeline checks in mock mode on a throwaway database."""
from __future__ import annotations

from pathlib import Path

import pytest

from regwatch import db
from regwatch.assess import seed_controls
from regwatch.compare import compare_publication

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)  # mock mode: deterministic
    monkeypatch.setattr("regwatch.pipeline.RAW_DIR", tmp_path)
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    db.sync_sources(c)
    seed_controls(c)
    from regwatch.internal import seed_internal
    monkeypatch.setattr("regwatch.internal.RAW_DIR", tmp_path)
    seed_internal(c)  # Level 2: internal policies, system inventory, ownership register
    return c


def add(conn, name, code, **meta):
    from regwatch.pipeline import add_document
    p = SAMPLES / name
    return add_document(conn, p.read_bytes(), p.name, code, meta, origin="sample")[0]


def test_amendment_records_every_clause_change(conn):
    add(conn, "sama_outsourcing_2024.txt", "SAMA", published_on="2024-03-10", reference_no="SAMA-ILLUS-2024-017")
    new = add(conn, "sama_outsourcing_amend_2026.txt", "SAMA", published_on="2026-05-12")
    cmp_ = conn.execute("SELECT * FROM comparison WHERE new_pub_id=?", (new,)).fetchone()
    classes = sorted(r[0] for r in conn.execute(
        "SELECT change_class FROM comparison_clause WHERE comparison_id=?", (cmp_["id"],)))
    assert cmp_["overall_class"] == "changes"
    assert classes == ["deadline_changed", "modified", "modified", "new_obligation", "withdrawn"]


def test_control_qa_flags_partial_and_gap(conn):
    new = add(conn, "sama_outsourcing_amend_2026.txt", "SAMA", published_on="2026-05-12")
    rows = {r["clause_ref"]: r for r in conn.execute(
        """SELECT o.clause_ref, a.status, a.recommended_change, k.code FROM obligation_assessment a
           JOIN obligation o ON o.id=a.obligation_id LEFT JOIN control k ON k.id=a.control_id
           WHERE o.publication_id=?""", (new,))}
    assert rows["Clause 5.1"]["status"] == "partial" and rows["Clause 5.1"]["code"] == "OUT-04"
    assert "30 days to 60 days" in rows["Clause 5.1"]["recommended_change"]
    assert rows["Clause 8.1"]["status"] == "gap" and rows["Clause 8.1"]["code"] is None
    assert rows["Clause 7.1"]["status"] == "compliant"


def test_new_hash_same_instrument_is_new_version_not_overwrite(conn):
    v1 = add(conn, "cbn_open_banking_2026.txt", "CBN", published_on="2026-10-02")
    v2 = add(conn, "upload_demo/cbn_open_banking_2026_v2.txt", "CBN")
    p1, p2 = db.get_publication(conn, v1), db.get_publication(conn, v2)
    assert p1["document_group_id"] == p2["document_group_id"] and (p1["version"], p2["version"]) == (1, 2)
    assert p1["content_hash"] != p2["content_hash"]  # v1 untouched
    c = conn.execute("SELECT * FROM comparison WHERE new_pub_id=?", (v2,)).fetchone()
    assert c["link_type"] == "version" and c["prior_pub_id"] == v1
    assert conn.execute("SELECT change_class FROM comparison_clause WHERE comparison_id=?",
                        (c["id"],)).fetchone()[0] == "deadline_changed"


def test_force_link_replaces_no_reliable_match(conn):
    add(conn, "cbn_aml_2025.txt", "CBN", published_on="2025-07-14")
    ob = add(conn, "cbn_open_banking_2026.txt", "CBN", published_on="2026-10-02")
    assert conn.execute("SELECT overall_class FROM comparison WHERE new_pub_id=?", (ob,)).fetchone()[0] == "no_reliable_match"
    prior = conn.execute("SELECT id FROM publication WHERE id<>?", (ob,)).fetchone()[0]
    res = compare_publication(conn, ob, forced_prior_id=prior)
    row = conn.execute("SELECT link_type, overall_class FROM comparison WHERE new_pub_id=?", (ob,)).fetchone()
    assert res["link"] == "manual" and row["link_type"] == "manual" and row["overall_class"] != "no_reliable_match"


def test_arabic_circular_is_understood(conn):
    pid = add(conn, "upload_demo/sama_ai_circular_ar.txt", "SAMA")
    p = db.get_publication(conn, pid)
    assert p["reference_no"] == "SAMA-ILLUS-AR-2026-090" and p["published_on"] == "2026-10-28"
    assert p["business_area"] == "AI"
    types = [r[0] for r in conn.execute("SELECT obligation_type FROM obligation WHERE publication_id=?", (pid,))]
    assert types.count("prohibition") == 1 and len(types) == 5
