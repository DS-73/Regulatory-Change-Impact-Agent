"""End-to-end live check against the real model (uses ~6 OpenRouter requests, then the cache).

    python -m regwatch.live_check

Runs on a scratch copy of the database, so the demo data is untouched:
  1. key + quota status
  2. extraction of an amendment (SAMA outsourcing) with the live model
  3. clause pairing against the prior circular: only changed clauses are sent
  4. Level 2 impact mapping of the amendment
  5. the assistant answering a question with tools
  6. the same comparison again: served from cache (0 new requests)
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import httpx

from . import DB_PATH, db, llm


def main() -> None:
    print(f"provider={llm.provider()}  model={llm.model_name()}  fallback={llm.fallback_model()}  "
          f"mode={llm.response_mode()}  live={llm.is_live()}")
    if not llm.is_live():
        print("No API key for the active provider in .env — nothing to test live.")
        return
    if llm.provider() == "openrouter":
        r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {llm.api_key()}"}, timeout=30)
        d = r.json().get("data", {})
        print(f"key ok={r.status_code == 200}  free_tier={d.get('is_free_tier')}  (free models: 50 requests/day without credits)")

    tmp = Path(tempfile.mkdtemp()) / "live.db"
    shutil.copy(DB_PATH, tmp)
    import regwatch.db as dbm
    dbm.DB_PATH = tmp  # audit log + everything else go to the scratch copy
    conn = db.connect(tmp)
    db.init_db(conn)
    pub = conn.execute("SELECT id, content_hash FROM publication WHERE title LIKE 'Amended Rules on Outsourcing%'").fetchone()
    prior = conn.execute("SELECT content_text FROM publication WHERE title='Rules on Outsourcing for Banks'").fetchone()

    from .assess import assess_publication
    from .compare import compare_publication, prompt_budget
    from .extract import extract_publication
    from . import assistant

    def step(name, fn):
        t0 = time.time()
        before = conn.execute("SELECT COUNT(*) FROM llm_call WHERE cached=0").fetchone()[0]
        res = fn()
        calls = conn.execute("SELECT COUNT(*), SUM(prompt_chars) FROM llm_call WHERE cached=0 AND id > "
                             "(SELECT COALESCE(MAX(id),0) - 50 FROM llm_call)").fetchone()
        new_calls = conn.execute("SELECT COUNT(*) FROM llm_call WHERE cached=0").fetchone()[0] - before
        print(f"\n[{name}] {time.time() - t0:.1f}s · {new_calls} new model request(s) · result: {res}")
        if llm.quota_status():
            print("  !! daily quota exhausted:", llm.quota_status())
        return res

    step("extract", lambda: extract_publication(conn, pub["id"]))
    for r in conn.execute("SELECT clause_ref, obligation_type, substr(text,1,90) FROM obligation WHERE publication_id=? LIMIT 4",
                          (pub["id"],)):
        print("   ", tuple(r))
    new_text = conn.execute("SELECT content_text FROM publication WHERE id=?", (pub["id"],)).fetchone()[0]
    print("  comparison prompt budget:", prompt_budget(prior["content_text"], new_text))
    step("compare", lambda: compare_publication(conn, pub["id"]))
    for r in conn.execute("SELECT cc.change_class, cc.prior_ref, cc.new_ref, substr(cc.summary,1,100) FROM comparison_clause cc "
                          "JOIN comparison c ON c.id=cc.comparison_id WHERE c.new_pub_id=?", (pub["id"],)):
        print("   ", tuple(r))
    step("impact mapping", lambda: assess_publication(conn, pub["id"]))
    for r in conn.execute(db.mappings_frame_sql() + " WHERE p.id=? AND m.verdict<>'compliant' LIMIT 4", (pub["id"],)):
        print(f"    {r['clause_ref']} {r['verdict']} → {r['component_label']}: {(r['proposed_wording'] or '')[:90]}")
    step("assistant", lambda: assistant.answer(conn, "Which High priority gaps affect outsourcing?")["text"][:300])
    step("compare again (cache)", lambda: compare_publication(conn, pub["id"]))
    print("\nAudit trail:")
    for r in conn.execute("SELECT purpose, model, status, cached, prompt_chars, latency_ms FROM llm_call ORDER BY id"):
        print("   ", tuple(r))


if __name__ == "__main__":
    main()
