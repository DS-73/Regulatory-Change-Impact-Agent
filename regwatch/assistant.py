"""RegWatch Assistant: a small read-only agent over the repository and the product guide.

Live mode: the OpenRouter model (same one as the pipeline) decides which read-only tool to call,
gets the results fenced as untrusted data, and answers with citations.
Mock mode (no key): a keyword router calls the same tools and formats the answer itself.

Safety: user questions are scanned for prompt injection and PII-redacted before reaching the model;
tool results are sanitised and fenced; tools can only read; answers are stripped of links, images and
HTML before display; sessions are rate-limited by the caller; every model call is audited.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache

import pandas as pd

from . import ROOT, db, llm, security
from .extract import clauses_for
from .impact import days_until, enrich_mapping
from .textutil import tfidf_cosine

GUIDE_PATH = ROOT / "docs" / "product_guide.md"
MAX_TOOL_ROUNDS = 4
SESSION_LIMIT = 20
REGULATORS = {"SAMA": ["sama", "saudi", "ksa"], "CBUAE": ["cbuae", "uae", "emirates"],
              "RBI": ["rbi", "india", "reserve bank"], "CBN": ["cbn", "nigeria"]}
DEPTS = ["Risk", "Compliance", "AI Governance", "Ops"]

# ---------------------------------------------------------------- product guide


@lru_cache(maxsize=1)
def guide_sections() -> list[tuple[str, str]]:
    text = GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.exists() else ""
    parts = re.split(r"(?m)^## ", text)[1:]
    out = []
    for p in parts:
        title, _, body = p.partition("\n")
        out.append((title.strip(), body.strip()))
    return out


def guide_section(title: str) -> str:
    return next((b for t, b in guide_sections() if t.lower() == title.lower()), "")


# ---------------------------------------------------------------- read-only tools

def _reg_filter(regulator):
    r = (regulator or "").strip().upper()
    return r if r in REGULATORS else None


def search_publications(conn, query: str, regulator: str | None = None, limit: int = 5) -> list[dict]:
    rows = conn.execute("""SELECT p.id, p.title, p.reference_no, p.published_on, p.doc_type, p.business_area,
                             p.version, s.short_code AS regulator, substr(p.content_text,1,4000) AS body
                           FROM publication p JOIN source s ON s.id=p.source_id""").fetchall()
    reg = _reg_filter(regulator)
    rows = [r for r in rows if not reg or r["regulator"] == reg]
    if not rows:
        return []
    sims = tfidf_cosine(query or "", [f"{r['title']} {r['title']} {r['body']}" for r in rows])
    ranked = sorted(zip(sims, rows), key=lambda x: x[0], reverse=True)[:max(1, min(limit, 10))]
    return [{"pub_id": r["id"], "regulator": r["regulator"], "title": r["title"], "reference_no": r["reference_no"],
             "published_on": r["published_on"], "doc_type": r["doc_type"], "business_area": r["business_area"],
             "version": r["version"], "relevance": round(float(s), 3)} for s, r in ranked if s > 0.02]


def list_changes(conn, pub_id: int | None = None, regulator: str | None = None, change_class: str | None = None,
                 limit: int = 10) -> list[dict]:
    df = pd.read_sql_query(db.comparisons_frame_sql(), conn)
    if pub_id:
        df = df[df["new_pub_id"] == int(pub_id)]
    if _reg_filter(regulator):
        df = df[df["regulator"] == _reg_filter(regulator)]
    if change_class:
        df = df[df["change_class"] == change_class]
    df = df.head(max(1, min(limit, 20)))
    return [{"pub_id": int(r.new_pub_id), "regulator": r.regulator, "publication": r.new_title,
             "clause": r.clause_ref, "change_class": r.change_class, "summary": r.summary,
             "prior_pub_id": None if pd.isna(r.prior_pub_id) else int(r.prior_pub_id)} for r in df.itertuples()]


def list_control_gaps(conn, status: str = "any", owner_dept: str | None = None, regulator: str | None = None,
                      limit: int = 10) -> list[dict]:
    """Level 2 impact-matrix rows: obligation -> affected internal component, with gap and draft change."""
    df = pd.read_sql_query(db.mappings_frame_sql(), conn)
    rows = [enrich_mapping(r) for r in df.astype(object).where(df.notna(), None).to_dict("records")]
    if status in ("gap", "partial", "compliant", "insufficient_evidence"):
        rows = [r for r in rows if r["verdict"] == status]
    else:
        rows = [r for r in rows if r["verdict"] != "compliant"]
    if owner_dept in DEPTS:
        rows = [r for r in rows if r.get("owner_dept") == owner_dept]
    if _reg_filter(regulator):
        rows = [r for r in rows if r["regulator"] == _reg_filter(regulator)]
    rows.sort(key=lambda r: ({"High": 0, "Medium": 1, "Low": 2}[r["priority"]], r["pub_id"]))
    return [{"mapping_id": r["id"], "pub_id": r["pub_id"], "regulator": r["regulator"], "publication": r["pub_title"], "reference_no": r.get("reference_no") or "none", "clause": r["clause_ref"],
             "status": r["verdict"], "severity": r["priority"], "workflow": r["workflow"],
             "obligation": (r["obligation"] or "")[:240], "affected_component": r["component_label"],
             "control": r.get("control_code"), "owner": r["owner_label"], "gap": r.get("compliance_gap"),
             "recommended_change": r.get("recommended_action"), "proposed_wording": r.get("proposed_wording"),
             "suggested_module": r["module"], "due": r["due"]} for r in rows[:max(1, min(limit, 20))]]


def list_deadlines(conn, days: int = 90, regulator: str | None = None, limit: int = 10) -> list[dict]:
    days = max(1, min(int(days or 90), 730))
    rows = conn.execute("""SELECT o.publication_id AS pub_id, o.clause_ref, o.text, o.deadline_on, s.short_code AS regulator,
                             p.title FROM obligation o JOIN publication p ON p.id=o.publication_id
                             JOIN source s ON s.id=p.source_id WHERE o.deadline_on IS NOT NULL""").fetchall()
    out = []
    for r in rows:
        d = days_until(r["deadline_on"])
        if d is None or d < 0 or d > days:
            continue
        if _reg_filter(regulator) and r["regulator"] != _reg_filter(regulator):
            continue
        out.append({"pub_id": r["pub_id"], "regulator": r["regulator"], "clause": r["clause_ref"],
                    "deadline": r["deadline_on"], "days_left": d, "obligation": r["text"][:200], "publication": r["title"]})
    return sorted(out, key=lambda x: x["days_left"])[:max(1, min(limit, 20))]


def get_obligation_source(conn, pub_id: int, clause_ref: str) -> dict:
    p = conn.execute("SELECT title, content_text FROM publication WHERE id=?", (int(pub_id),)).fetchone()
    if not p:
        return {"error": f"publication #{pub_id} not found"}
    want = (clause_ref or "").strip().lower()
    for c in clauses_for(p["content_text"] or ""):
        if c.ref.lower() == want or c.ref.lower().endswith(" " + want.split()[-1] if want else "~"):
            return {"pub_id": int(pub_id), "publication": p["title"], "clause": c.ref, "text": c.text[:1500]}
    return {"error": f"clause {clause_ref} not found in #{pub_id}"}


def explain_feature(conn, topic: str) -> list[dict]:
    secs = guide_sections()
    if not secs:
        return []
    q = re.sub(r"(?i)\b(how|does|do|did|is|are|can|what|which|why|when|regwatch|the|app|product|it|work|works|"
               r"happen|happens|tell|me|about|explain)\b", " ", topic or "")
    sims = tfidf_cosine(q, [f"{ALIASES.get(t, '')} {ALIASES.get(t, '')} {t} {b}" for t, b in secs])
    best = sorted(zip(sims, secs), key=lambda x: x[0], reverse=True)[:2]
    return [{"section": t, "text": b} for s, (t, b) in best if s > 0]


# Extra words that people use for each guide section (improves TF-IDF matching).
ALIASES = {
    "Monitor": "monitor monitoring scrape scraper scraping crawl crawler poll polling source sources website fetch feed",
    "Understand": "extract extraction obligation obligations clause clauses understand read parse duties",
    "Compare": "compare comparison change changes changed detect detects amend amendment amendments diff redline",
    "Control impact": "control controls gap gaps impact compliant partial vanta questionnaire matrix severity",
    "Review": "review reviews approve approval approved human reviewer workflow",
    "AI security": "security secure injection injections pii prompt prompts guardrail guardrails safe privacy attack",
}


TOOLS = {
    "search_publications": (search_publications, "Search regulatory publications by topic, title or reference.",
                            {"query": {"type": "string"}, "regulator": {"type": "string", "enum": list(REGULATORS)},
                             "limit": {"type": "integer"}}, ["query"]),
    "list_changes": (list_changes, "List clause-level changes detected between a publication and its prior version.",
                     {"pub_id": {"type": "integer"}, "regulator": {"type": "string", "enum": list(REGULATORS)},
                      "change_class": {"type": "string", "enum": ["new_obligation", "modified", "withdrawn",
                                                                   "deadline_changed", "clarification",
                                                                   "no_reliable_match", "no_material_change"]},
                      "limit": {"type": "integer"}}, []),
    "list_control_gaps": (list_control_gaps, "List impact-matrix rows: obligation, affected internal policy/process/system/control, verdict, gap, recommended change, draft wording, owner.",
                          {"status": {"type": "string", "enum": ["gap", "partial", "insufficient_evidence", "compliant", "any"]},
                           "owner_dept": {"type": "string", "enum": DEPTS},
                           "regulator": {"type": "string", "enum": list(REGULATORS)}, "limit": {"type": "integer"}}, []),
    "list_deadlines": (list_deadlines, "List obligations with a compliance deadline in the next N days.",
                       {"days": {"type": "integer"}, "regulator": {"type": "string", "enum": list(REGULATORS)},
                        "limit": {"type": "integer"}}, []),
    "get_obligation_source": (get_obligation_source, "Return the verbatim text of a clause in a publication.",
                              {"pub_id": {"type": "integer"}, "clause_ref": {"type": "string"}}, ["pub_id", "clause_ref"]),
    "explain_feature": (explain_feature, "Explain how RegWatch works, from the product guide.",
                        {"topic": {"type": "string"}}, ["topic"]),
}


def tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": {
        "type": "object", "properties": props, "required": req, "additionalProperties": False}}}
        for n, (_, d, props, req) in TOOLS.items()]


def run_tool(conn, name: str, args: dict) -> str:
    if name not in TOOLS:
        return json.dumps({"error": f"unknown tool {name}"})
    fn, _, props, _ = TOOLS[name]
    clean = {k: v for k, v in (args or {}).items() if k in props}  # drop unexpected arguments
    for k, v in list(clean.items()):
        if props[k]["type"] == "integer":
            try:
                clean[k] = int(v)
            except (TypeError, ValueError):
                clean.pop(k)
        elif isinstance(v, str):
            clean[k] = v[:300]
    try:
        result = fn(conn, **clean)
    except Exception as e:  # tools must never crash the chat
        result = {"error": f"{type(e).__name__}"}
    return json.dumps(result, ensure_ascii=False, default=str)[:7000]


# ---------------------------------------------------------------- prompts & output hygiene

SYSTEM = """You are the RegWatch Assistant inside a regulatory-intelligence product used by bank compliance teams.
RegWatch monitors SAMA (KSA), CBUAE (UAE), RBI (India) and CBN (Nigeria), extracts obligations with clause
references, compares amendments clause by clause, and checks obligations against the organisation's control
library (compliant / partial / gap).

How to answer:
- Use the tools to look things up. Never invent publications, clauses, dates, controls or numbers.
- Cite what you used inline: publications as [#5 · Clause 5.1], controls as [OUT-04]. Use reference numbers
  exactly as returned by the tools; if a tool returns none, write the publication title instead.
- If the tools return nothing relevant, say you could not find it.
- Be concise: at most about 150 words, short bullets where useful.
- Reply in the same language as the user's question (Arabic or English).
- Only discuss regulations, compliance and RegWatch itself. Politely decline anything else.
- You are read-only: you cannot approve, edit, delete or send anything."""

LINK_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\((?:https?|javascript|data):[^)]*\)", re.I)
TAG = re.compile(r"<[^>]{1,200}>")


def clean_answer(text: str) -> str:
    """Strip images (data exfiltration via URLs), external links and raw HTML from model output."""
    t = LINK_IMG.sub("", text or "")
    t = LINK.sub(r"\1", t)
    t = TAG.sub("", t)
    t = re.sub(r"https?://\S+", "[link removed]", t)
    return t.strip()[:4000]


def citations(text: str) -> dict:
    pubs = sorted({int(x) for x in re.findall(r"#(\d{1,5})\b", text or "")})
    ctls = sorted(set(re.findall(r"\b([A-Z]{2,4}-\d{2})\b", text or "")))
    return {"pubs": pubs, "controls": ctls}


# ---------------------------------------------------------------- mock-mode router

GUIDE_INTENT = re.compile(r"\b(regwatch|the app|this app|product|feature|how does|how do|how is|how are|why|"
                          r"what happens|can (it|regwatch)|does (it|regwatch)|ocr|scanned|model|mock|tour|assistant|"
                          r"security|injection|version|arabic|fallback|work)\b", re.I)


def _regulator_in(q: str) -> str | None:
    ql = q.lower()
    for code, words in REGULATORS.items():
        if any(re.search(rf"\b{w}\b", ql) for w in words):
            return code
    return None


def mock_answer(conn, q: str) -> tuple[str, list[str]]:
    ql = q.lower()
    reg = _regulator_in(q)
    data_words = re.search(r"\b(gap|gaps|partial|control|owner|due|deadline|upcoming|changed|changes|amend\w*|"
                           r"withdrawn|circular|obligation|clause|publication)\b", ql)
    if GUIDE_INTENT.search(q) and not (reg and data_words):
        secs = explain_feature(conn, q)
        if secs:
            s = secs[0]
            body = s["text"].split("\n\n")
            return f"**{s['section']}**\n\n" + "\n\n".join(body[:2]), ["explain_feature"]
    if re.search(r"\b(due|deadline|upcoming|next \d+ days)\b", ql):
        m = re.search(r"(\d{1,3})\s*days", ql)
        items = list_deadlines(conn, int(m.group(1)) if m else 90, reg)
        if not items:
            return "No obligations with a deadline in that window.", ["list_deadlines"]
        return "Upcoming deadlines:\n\n" + "\n".join(
            f"- **{i['deadline']}** (in {i['days_left']} days) · {i['regulator']} [#{i['pub_id']} · {i['clause']}] — "
            f"{i['obligation'][:120]}" for i in items), ["list_deadlines"]
    if re.search(r"\b(gap|gaps|partial|control|controls|owner|impact|compliant)\b", ql):
        status = "partial" if "partial" in ql else ("gap" if "gap" in ql else "any")
        dept = next((d for d in DEPTS if d.lower() in ql), None)
        items = list_control_gaps(conn, status, dept, reg, 8)
        if not items:
            return "No matching control gaps found.", ["list_control_gaps"]
        return f"{len(items)} item(s) need attention:\n\n" + "\n".join(
            f"- **{i['severity']} · {i['status']}** {i['regulator']} [#{i['pub_id']} · {i['clause']}]"
            + (f" vs [{i['control']}]" if i["control"] else "") + f" — {i['recommended_change'] or i['obligation'][:100]}"
            for i in items), ["list_control_gaps"]
    pubs = search_publications(conn, q, reg, 3)
    if re.search(r"\b(change|changed|changes|amend\w*|withdrawn|new obligation|differ\w*)\b", ql):
        target = next((p for p in pubs if list_changes(conn, pub_id=p["pub_id"], limit=1)), None)
        items = list_changes(conn, pub_id=target["pub_id"]) if target else list_changes(conn, regulator=reg)
        if not items:
            return "I couldn't find recorded changes for that.", ["search_publications", "list_changes"]
        head = f"Changes in **{target['title']}** [#{target['pub_id']}]:" if target else "Recorded changes:"
        return head + "\n\n" + "\n".join(
            f"- **{i['change_class'].replace('_', ' ')}** [#{i['pub_id']} · {i['clause'] or '—'}] — {i['summary']}"
            for i in items), ["search_publications", "list_changes"]
    if not pubs:
        return ("I can help with regulations in the library (SAMA, CBUAE, RBI, CBN), what changed, control gaps, "
                "deadlines, and how RegWatch works. I couldn't find anything for that question."), ["search_publications"]
    return "Most relevant publications:\n\n" + "\n".join(
        f"- {p['regulator']} [#{p['pub_id']}] **{p['title']}** · {p['reference_no'] or 'no ref'} · {p['published_on'] or '—'}"
        for p in pubs), ["search_publications"]


# ---------------------------------------------------------------- entry point

def answer(conn, question: str, history: list[dict] | None = None) -> dict:
    """Return {text, citations, mode, tools, blocked}."""
    question = (question or "").strip()[:1000]
    if not question:
        return {"text": "Ask me about regulations, changes, control gaps or how RegWatch works.", "citations": {},
                "mode": "-", "tools": [], "blocked": False}
    findings = security.scan_injection(question)
    if findings:
        db.log_llm_call(at=datetime.now().isoformat(timespec="seconds"), purpose="assistant", model="-",
                        doc_hash="", prompt_sha="", prompt_chars=len(question), cached=0, status="blocked",
                        latency_ms=0, detail=f"injection: {', '.join(f['type'] for f in findings)}")
        return {"text": "I can't follow instructions that try to change how I work. Ask me about regulations, "
                        "changes, control gaps or how RegWatch works.",
                "citations": {}, "mode": "blocked", "tools": [], "blocked": True}
    safe_q = security.redact_pii(question)[0] if security.redaction_enabled() else question

    if not llm.is_live():
        text, tools = mock_answer(conn, safe_q)
        return {"text": clean_answer(text), "citations": citations(text), "mode": "mock", "tools": tools, "blocked": False}

    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + security.GUARDRAIL}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": security.redact_pii(str(h["content"])[:1500])[0]})
    msgs.append({"role": "user", "content": safe_q})
    used: list[str] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            msg = llm.chat_with_tools(msgs, tool_schemas())
            calls = msg.get("tool_calls") or []
            if not calls:
                text = clean_answer(msg.get("content") or "")
                if not text:
                    break
                return {"text": text, "citations": citations(text), "mode": "live", "tools": used, "blocked": False}
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls[:4]:
                fn = c.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = fn.get("name", "")
                used.append(name)
                result = security.redact_pii(security.sanitize_for_prompt(run_tool(conn, name, args)))[0]
                msgs.append({"role": "tool", "tool_call_id": c.get("id", name), "name": name,
                             "content": security.wrap_untrusted(result, "tool_result")})
        raise llm.LLMError("no final answer")
    except llm.LLMError:
        text, tools = mock_answer(conn, safe_q)
        text = "_The AI model is unavailable right now, so this answer comes straight from the database._\n\n" + text
        return {"text": clean_answer(text), "citations": citations(text), "mode": "mock (LLM failed)",
                "tools": used + tools, "blocked": False}
