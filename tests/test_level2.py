"""Level 2: internal repository, regulation-to-policy mapping, gap analysis, recommendations, ownership."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from regwatch import db, internal
from regwatch.assess import reassess_all, seed_controls
from regwatch.impact import enrich_mapping

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("regwatch.pipeline.RAW_DIR", tmp_path)
    monkeypatch.setattr("regwatch.internal.RAW_DIR", tmp_path)
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    db.sync_sources(c)
    seed_controls(c)
    internal.seed_internal(c)
    from regwatch.pipeline import add_document
    for name, code, meta in [
        ("sama_outsourcing_2024.txt", "SAMA", {"published_on": "2024-03-10", "reference_no": "SAMA-ILLUS-2024-017"}),
        ("sama_outsourcing_amend_2026.txt", "SAMA", {"published_on": "2026-05-12"}),
        ("cbuae_ai_guidance_2025.txt", "CBUAE", {"published_on": "2025-02-20", "reference_no": "CBUAE/ILLUS/N/2025/04"}),
        ("cbuae_ai_guidance_amend_2026.txt", "CBUAE", {"published_on": "2026-09-15", "business_area": "AI"}),
        ("cbn_open_banking_2026.txt", "CBN", {"published_on": "2026-10-02", "business_area": "payments"}),
    ]:
        add_document(c, (SAMPLES / name).read_bytes(), name, code, meta, origin="sample")
    return c


def rows_for(conn, regulator, clause, title_like):
    return [enrich_mapping(dict(r)) for r in conn.execute(
        db.mappings_frame_sql() + " WHERE s.short_code=? AND o.clause_ref=? AND p.title LIKE ?",
        (regulator, clause, f"%{title_like}%"))]


def test_repository_holds_all_internal_document_types(conn):
    docs = {d["doc_type"] for d in db.list_internal_documents(conn)}
    assert {"policy", "sop", "process", "ai_policy", "system_inventory", "ownership_register"} <= docs
    assert len(db.list_owners(conn)) == 4 and len(internal.approvers(conn)) == 2
    assert conn.execute("SELECT COUNT(*) FROM document_section").fetchone()[0] > 30


def test_ai_credit_example_maps_to_policies_and_controls(conn):
    """The brief's example: human oversight of automated credit decisions."""
    rows = rows_for(conn, "CBUAE", "Clause 4.3", "Amendment")
    labels = " | ".join(r["component_label"] for r in rows)
    assert "POL-CR-02 §3.2" in labels and "POL-AI-01 §5.1" in labels and "AIG-05" in labels
    for r in rows:
        assert r["verdict"] == "partial"
        assert r["existing_requirement"] and r["reason"] and r["compliance_gap"] and r["recommended_action"]
        assert "human review" in r["proposed_wording"] and "audit trail" in r["proposed_wording"]
        assert any("Clause 4.3" in x for x in r["refs"]) and 0 < r["confidence"] <= 1
    cr = next(r for r in rows if "POL-CR-02" in r["component_label"])
    assert "only conditionally" in cr["compliance_gap"]


def test_partial_wording_is_concrete(conn):
    rows = rows_for(conn, "SAMA", "Clause 5.1", "Amended")
    pol = next(r for r in rows if "POL-OUT-03" in r["component_label"])
    assert pol["verdict"] == "partial" and "60 days" in pol["proposed_wording"] and "30 days" not in pol["proposed_wording"]


def test_four_gap_states_are_distinguished(conn):
    verdicts = {r[0] for r in conn.execute("SELECT status FROM obligation_assessment")}
    assert verdicts == {"compliant", "partial", "gap", "insufficient_evidence"}
    # documented area, nothing requires it -> gap
    assert rows_for(conn, "SAMA", "Clause 8.1", "Amended")[0]["verdict"] == "gap"
    # nothing documented for payments / open banking -> insufficient evidence, explained
    ob = rows_for(conn, "CBN", "Clause 2.1", "Open Banking")[0]
    assert ob["verdict"] == "insufficient_evidence" and "payments" in ob["compliance_gap"]


def test_owner_comes_from_register_or_is_flagged(conn):
    ai = rows_for(conn, "CBUAE", "Clause 4.3", "Amendment")
    assert all(r["owner_source"] == "register" and r["owner_person"] for r in ai)
    ob = rows_for(conn, "CBN", "Clause 5.1", "Open Banking")[0]
    assert ob["owner_source"] == "unassigned" and ob["owner_person"] is None and ob["needs_owner"]


def test_recommendations_stay_draft_until_approved_and_survive_remapping(conn):
    r = rows_for(conn, "SAMA", "Clause 5.1", "Amended")[0]
    assert r["approval_status"] == "draft" and r["workflow"] == "Draft · awaiting approval"
    db.update_mapping(conn, r["id"], approval_status="approved", approved_by="Layla Haddad",
                      approved_at="2026-10-31T10:00:00", owner_dept="Compliance", owner_person="Layla Haddad",
                      owner_source="reviewer", implementation_status="in_progress")
    reassess_all(conn)  # machine re-run must not wipe reviewer decisions
    again = next(x for x in rows_for(conn, "SAMA", "Clause 5.1", "Amended") if x["component_label"] == r["component_label"])
    assert again["approval_status"] == "approved" and again["workflow"] == "In progress"
    assert again["approved_by"] == "Layla Haddad" and again["owner_source"] == "reviewer"


def test_uploading_a_payments_policy_resolves_insufficient_evidence(conn, tmp_path):
    text = (b"Open Banking Policy\n1. Scope\nThis policy covers open banking and payment initiation APIs.\n"
            b"2. Registration\n2.1 The Bank shall register on the CBN open banking registry before exchanging data "
            b"through APIs.\n")
    doc_id, new, n = internal.add_internal_document(conn, text, "open_banking_policy.txt", "policy", "Open Banking Policy",
                                                    "Ops", "payments")
    assert new and n >= 2
    _, again, _ = internal.add_internal_document(conn, text, "copy.txt", "policy")
    assert not again  # same content is not stored twice
    reassess_all(conn)
    ob = rows_for(conn, "CBN", "Clause 2.1", "Open Banking")
    assert ob[0]["verdict"] in ("compliant", "partial") and "Open Banking Policy" in ob[0]["component_label"]


def test_unsafe_internal_upload_is_rejected(conn):
    from regwatch.security import UnsafeFileError
    with pytest.raises(UnsafeFileError):
        internal.add_internal_document(conn, b"MZ\x90\x00binary", "policy.txt", "policy")


def test_ownership_register_upload_replaces_owners(conn):
    csv = b"department,person,role,business_areas,keywords,can_approve\nPayments,Sam Lee,Head of Payments,payments,open banking,no\n"
    internal.add_internal_document(conn, csv, "owners_v2.csv", "ownership_register")
    assert [(o["department"], o["person"]) for o in db.list_owners(conn)] == [("Payments", "Sam Lee")]
    assert json.loads(json.dumps([dict(o) for o in db.list_owners(conn)]))
