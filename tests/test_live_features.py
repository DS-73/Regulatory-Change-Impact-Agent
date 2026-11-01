"""Hashing, token budget, quota fallback, scheduler and wrong-upload handling."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from regwatch import db, llm, security
from regwatch.compare import prompt_budget
from regwatch.pipeline import NotRegulatoryDocument, add_document, regulatory_signals

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
PDFS = SAMPLES / "upload_demo" / "test_pdfs"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("regwatch.pipeline.RAW_DIR", tmp_path)
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    db.sync_sources(c)
    return c


def test_same_content_is_hashed_once(conn):
    data = (SAMPLES / "cbn_aml_2025.txt").read_bytes()
    a, new_a = add_document(conn, data, "a.txt", "CBN", {}, origin="sample")
    b, new_b = add_document(conn, data, "renamed.txt", "CBN", {}, origin="sample")
    assert new_a and not new_b and a == b
    assert conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0] == 1


def test_comparison_sends_only_changed_clauses():
    old = (SAMPLES / "sama_outsourcing_2024.txt").read_text(encoding="utf-8")
    new = (SAMPLES / "sama_outsourcing_amend_2026.txt").read_text(encoding="utf-8")
    b = prompt_budget(old, new)
    assert b["clauses_sent"] < b["clauses_total"] / 2 and b["sent_chars"] < b["full_documents_chars"] * 0.6
    assert prompt_budget(old, old)["sent_chars"] == 0  # identical text: no model call at all


def test_exhausted_quota_skips_calls_but_serves_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    llm._QUOTA.update(until=time.time() + 600, message="free-models-per-day")
    called = []
    monkeypatch.setattr(llm, "_post", lambda p: called.append(p))
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    msgs = [{"role": "user", "content": "hi"}]
    with pytest.raises(llm.LLMError):
        llm.chat_json(msgs, schema, "t", purpose="test")
    assert called == []  # no HTTP request while the quota is exhausted
    import json
    (tmp_path / f"{llm._cache_key(llm.model_name(), msgs, schema)}.json").write_text(json.dumps({"parsed": {"x": "cached"}}))
    assert llm.chat_json(msgs, schema, "t", purpose="test") == {"x": "cached"}
    llm._QUOTA.update(until=0.0, message="")


def test_scheduler_polls_only_due_sources(conn, monkeypatch):
    from regwatch import monitor, scheduler
    conn.execute("UPDATE source SET last_run_at=?", (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    conn.execute("UPDATE source SET last_run_at='2020-01-01T00:00:00' WHERE short_code='RBI'")
    conn.commit()
    assert monitor.due_sources(conn) == ["RBI"]
    seen = []
    monkeypatch.setattr(scheduler.db, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(scheduler.monitor, "run_monitoring", lambda c, codes=None: seen.append(codes) or [])
    scheduler.cycle()
    assert seen == [["RBI"]]


@pytest.mark.parametrize("name,error", [
    ("fake_pdf_actually_text.pdf", security.UnsafeFileError),
    ("corrupt.pdf", security.UnsafeFileError),
    ("pdf_with_embedded_file.pdf", security.UnsafeFileError),
    ("scanned_image_only.pdf", NotRegulatoryDocument),
    ("not_a_regulation_menu.pdf", NotRegulatoryDocument),
])
def test_wrong_pdfs_are_rejected_with_a_reason(conn, name, error):
    with pytest.raises(error):
        add_document(conn, (PDFS / name).read_bytes(), name, "CBN", {}, origin="upload")
    assert conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0] == 0


def test_good_pdf_is_compared_with_the_circular_it_amends(conn):
    add_document(conn, (SAMPLES / "cbn_aml_2025.txt").read_bytes(), "cbn_aml_2025.txt", "CBN",
                 {"reference_no": "FPR/DIR/ILLUS/CIR/2025/007", "published_on": "2025-07-14"}, origin="sample")
    pid, new = add_document(conn, (PDFS / "good_cbn_aml_amendment_2026.pdf").read_bytes(), "amend.pdf", "CBN", {},
                            origin="upload")
    classes = {r[0] for r in conn.execute("SELECT cc.change_class FROM comparison_clause cc JOIN comparison c "
                                          "ON c.id=cc.comparison_id WHERE c.new_pub_id=?", (pid,))}
    assert new and {"modified", "deadline_changed"} <= classes


def test_not_regulatory_can_be_forced_after_review(conn):
    pid, new = add_document(conn, (PDFS / "not_a_regulation_menu.pdf").read_bytes(), "menu.pdf", "CBN", {"force": True},
                            origin="upload")
    assert new and pid
    assert regulatory_signals("Banks shall comply with this circular issued by the central bank regulator. " * 5)["ok"]
