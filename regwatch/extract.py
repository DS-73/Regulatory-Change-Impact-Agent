"""Capability 2: document text -> structured obligations, each anchored to a clause.

Live mode sends the text to OpenRouter with a strict JSON schema. Mock mode (no
key, or the call failed) uses deterministic rules over the clause map, so the
pipeline always produces structured, clause-referenced output.
"""
from __future__ import annotations

import re

import json

from . import db, llm, security
from .ingest import pages_from_text, segment_clauses
from .models import BUSINESS_AREAS, DOC_TYPES, OBLIGATION_TYPES, Clause
from .textutil import find_dates, norm_ws, parse_date

# ---------------------------------------------------------------- schema / prompt

OBLIGATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": ["string", "null"]},
        "reference_no": {"type": ["string", "null"]},
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "business_area": {"type": "string", "enum": BUSINESS_AREAS},
        "effective_on": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "obligations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "clause_ref": {"type": "string"},
                    "obligation_type": {"type": "string", "enum": OBLIGATION_TYPES},
                    "applies_to": {"type": ["string", "null"]},
                    "deadline_on": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "confidence": {"type": "number"},
                },
                "required": ["text", "clause_ref", "obligation_type", "applies_to", "deadline_on", "confidence"],
            },
        },
    },
    "required": ["title", "reference_no", "doc_type", "business_area", "effective_on", "obligations"],
}

SYSTEM_PROMPT = """You are a regulatory compliance analyst. Extract every regulatory obligation from the document.
The document may be in Arabic, English or both. Keep "text" and "clause_ref" in the original language
(e.g. "المادة 3"); write any other free text in English.
Rules:
- One item per distinct obligation, prohibition, definition or clarification. Do not summarise the document.
- clause_ref: copy the exact clause/article/paragraph identifier as it appears (e.g. "Clause 4.2", "Article 7", "Para 5").
  If the text has no formal numbering, use the page marker of the passage, e.g. "p.3". Never invent clause numbers.
- text: the obligation in close-to-verbatim wording (one or two sentences).
- obligation_type: mandatory | prohibition | clarification | definition.
- applies_to: the entities, functions or activities it applies to.
- deadline_on: compliance deadline or effective date for that obligation as YYYY-MM-DD, else null.
- confidence: 0-1, your confidence the item is accurate and correctly referenced.
- Also return document-level title, reference_no, doc_type, business_area and effective_on (YYYY-MM-DD) if stated."""

CHUNK_CHARS = 40_000

# ---------------------------------------------------------------- rule-based helpers

AREA_KEYWORDS = {
    "AI": [r"artificial intelligence", r"\bAI\b", r"machine learning", r"algorithm", r"\bmodels?\b", r"الذكاء الاصطناعي"],
    "AML": [r"غسل الأموال", r"money laundering", r"\bAML\b", r"terroris[mt]", r"sanction", r"\bKYC\b", r"\bCFT\b", r"suspicious"],
    "lending": [r"\blend", r"\bloans?\b", r"\bcredit\b", r"borrower", r"التمويل", r"القروض"],
    "payments": [r"المدفوعات", r"payment", r"wallet", r"open banking", r"\bAPIs?\b", r"remittance", r"\bcards?\b"],
    "insurance": [r"insur", r"takaful", r"policyholder"],
    "banking": [r"\bbank", r"capital", r"liquidity", r"outsourc", r"governance"],
}

ENTITY_PATTERNS = [
    r"licensed financial institutions?", r"licensed banks?", r"regulated entit(?:y|ies)", r"commercial banks?",
    r"banks?", r"payment service providers?", r"open banking participants?", r"insurers?",
    r"lending service providers?", r"digital lending apps?", r"NBFCs?", r"finance companies",
    r"financial institutions?", r"board of directors", r"senior management",
    r"compliance function", r"money laundering reporting officers?", r"service providers?",
    r"البنوك", r"المصارف", r"المؤسسات المالية", r"مجلس الإدارة",
]

MODAL_MANDATORY = re.compile(r"\b(shall|must|is required to|are required to|required to|should ensure|will be required|"
                             r"(?:are|is) (?:hereby )?(?:advised|directed) to)\b|يجب|يلتزم|تلتزم|ينبغي", re.I)
MODAL_PROHIBIT = re.compile(r"\b(shall not|must not|may not|is prohibited|are prohibited|prohibited from|not permitted|"
                            r"no \w+ shall)\b|لا يجوز|يحظر|يُحظر|لا يحق", re.I)
DEFINITION = re.compile(r"(\"[^\"]+\"|“[^”]+”|'[^']+')\s+(means|refers to|includes)\b|\bmeans\b|يقصد ب|يُقصد ب", re.I)
CLARIFY = re.compile(r"\b(for the avoidance of doubt|clarif|it is clarified|note that|for clarity)\b", re.I)


def _sentences(text: str) -> list[str]:
    clean = security.TAG_CHARS_RE.sub("", security.INVISIBLE_RE.sub(" ", text))  # hidden chars can't glue sentences
    parts = re.split(r"(?<=[.;؛])\s+(?=[A-Z(“\"؀-ۿ])", norm_ws(clean))
    return [p.strip() for p in parts if len(p.strip()) > 15]


def _applies_to(sentence: str, fallback: str | None) -> str | None:
    found = []
    for pat in ENTITY_PATTERNS:
        m = re.search(pat, sentence, re.I)
        if m and not any(m.group(0).lower() in f.lower() for f in found):
            found.append(m.group(0))
    return ", ".join(found[:3]) if found else fallback


def _deadline(sentence: str) -> str | None:
    if not re.search(r"\b(by|no later than|before|within|effective|from|until|deadline|on or before|with effect)\b"
                     r"|بحلول|قبل|اعتبارا من|اعتباراً من|حتى|في موعد أقصاه", sentence, re.I):
        return None
    ds = find_dates(sentence)
    return ds[-1][1].isoformat() if ds else None


def classify_area(text: str) -> str:
    scores = {a: sum(len(re.findall(p, text, re.I)) for p in pats) for a, pats in AREA_KEYWORDS.items()}
    # 'banking' is the generic bucket, so damp it.
    scores["banking"] = scores["banking"] * 0.4
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def classify_doc_type(title: str, text: str) -> str:
    head = f"{title}\n{text[:1500]}".lower()
    if "consultation" in head or "exposure draft" in head:
        return "consultation"
    if "amend" in head or "supersede" in head or "revised" in head:
        return "amendment"
    if "guideline" in head or "guidance" in head:
        return "guideline"
    if "notification" in head:
        return "notification"
    return "circular"


def find_reference(text: str) -> str | None:
    for line in text.split("\n")[:30]:
        line = line.strip()
        m = re.match(r"^(RBI/\d{4}-\d{2}/\d+.*)$", line)
        if m:
            return m.group(1)[:80]
        m = (re.search(r"(?i)\b(?:ref(?:erence)?|circular|notice|notification)\s*(?:no\.?|number)\s*[:.]?\s*(\S[^\n]{2,70})", line)
             or re.match(r"(?i)^ref(?:erence)?\s*[:.]\s*(\S[^\n]{2,70})", line)
             or re.match(r"^(?:الرقم|رقم التعميم|المرجع)\s*[:.]\s*(\S[^\n]{2,70})", line))
        if m:
            return re.split(r"(?i)\s+dated?\b|\s{2,}", m.group(1))[0].strip(" .,;")
    return None


def find_published(text: str) -> str | None:
    head = "\n".join(text.split("\n")[:25])
    m = re.search(r"(?im)^\s*(?:date[d]?|التاريخ)\s*[:.]?\s*(.+)$", head)
    ds = find_dates(m.group(1)) if m else []
    if not ds:
        ds = find_dates(head)
    return ds[0][1].isoformat() if ds else None


def find_effective(text: str) -> str | None:
    m = re.search(r"(?i)(effective|come[s]? into (?:force|effect)|with effect)([^\n]{0,120})", text)
    if m:
        ds = find_dates(m.group(2))
        if ds:
            return ds[0][1].isoformat()
    return None


def find_title(text: str) -> str | None:
    for line in text.split("\n")[:20]:
        s = line.strip()
        if (len(s) > 15 and not s.startswith("[p.") and not s.isupper() and not s.startswith(("(", "["))
                and not re.search(r"(?i)^(ref|date|to:|from:|rbi/|الرقم|التاريخ|المرجع|إلى)", s)
                and not re.fullmatch(r"(?i)(the )?(saudi )?central bank.*|reserve bank of india|"
                                     r"البنك المركزي.*|مصرف .*المركزي", s)):
            return s[:200]
    return None


def rule_based_extract(text: str, clauses: list[Clause]) -> dict:
    doc_fallback = _applies_to(text[:3000], None)
    obligations = []
    for c in clauses:
        for s in _sentences(c.text):
            if security.is_injection_sentence(s):
                continue  # instructions aimed at an AI are not regulatory obligations
            if MODAL_PROHIBIT.search(s):
                otype = "prohibition"
            elif CLARIFY.search(s):
                otype = "clarification"
            elif DEFINITION.search(s) and not MODAL_MANDATORY.search(s):
                otype = "definition"
            elif MODAL_MANDATORY.search(s):
                otype = "mandatory"
            else:
                continue
            obligations.append({
                "text": s[:600],
                "clause_ref": c.ref,
                "obligation_type": otype,
                "applies_to": _applies_to(s, doc_fallback),
                "deadline_on": _deadline(s),
                "confidence": 0.55 if not c.ref.startswith("p.") else 0.4,
            })
    title = find_title(text) or ""
    return {
        "title": title,
        "reference_no": find_reference(text),
        "doc_type": classify_doc_type(title, text),
        "business_area": classify_area(text),
        "effective_on": find_effective(text),
        "published_on": find_published(text),
        "obligations": obligations,
    }


# ---------------------------------------------------------------- live path

def _chunks(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    out, buf = [], ""
    for para in re.split(r"(?=\n\[p\.\d+\]\n)|\n\n", text):
        if len(buf) + len(para) > CHUNK_CHARS and buf:
            out.append(buf)
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        out.append(buf)
    return out


def llm_extract(text: str, clauses: list[Clause], doc_hash: str = "") -> dict:
    merged: dict | None = None
    # Untrusted text is sanitised (hidden chars, delimiter/chat tokens, size cap) before it is prompted.
    safe = security.sanitize_for_prompt(text)
    for i, chunk in enumerate(_chunks(safe)[: security.MAX_LLM_CALLS_PER_DOC]):
        res = llm.chat_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + security.GUARDRAIL},
                {"role": "user", "content": f"Document part {i + 1} (with [p.N] page markers):\n"
                                            + security.wrap_untrusted(chunk)},
            ],
            OBLIGATION_SCHEMA,
            "obligations",
            purpose="extract",
            doc_hash=doc_hash,
        )
        if merged is None:
            merged = res
        else:
            merged["obligations"].extend(res.get("obligations", []))
    assert merged is not None
    # Grounding check: references the clause map doesn't know are flagged via low confidence.
    known = {c.ref.lower() for c in clauses}
    for o in merged.get("obligations", []):
        ref = (o.get("clause_ref") or "").strip()
        num = re.search(r"\d+(?:\.\d+)*", ref)
        ok = ref.lower() in known or ref.lower().startswith("p.") or (
            num and any(k.endswith(" " + num.group(0)) for k in known))
        if not ok:
            o["confidence"] = min(float(o.get("confidence") or 0.5), 0.4)
    return merged


# ---------------------------------------------------------------- entry point

def canonical_ref(ref: str, clauses: list[Clause]) -> str:
    """Map a model-written reference ('3.1', 'Art. 3.1', 'clause 3.1') onto the document's own clause
    label ('Clause 3.1' / 'المادة 3') so obligations link back to their source text."""
    r = (ref or "").strip()
    known = {c.ref.lower(): c.ref for c in clauses}
    if r.lower() in known:
        return known[r.lower()]
    num = re.search(r"\d+(?:\.\d+)*", r)
    if num and not r.lower().startswith("p."):
        hits = [c.ref for c in clauses if re.search(rf"(^|\s){re.escape(num.group(0))}$", c.ref)]
        if len(hits) == 1:
            return hits[0]
    return r


def clauses_for(text: str) -> list[Clause]:
    return segment_clauses(pages_from_text(text.encode("utf-8")))


def extract_publication(conn, pub_id: int) -> dict:
    pub = db.get_publication(conn, pub_id)
    text = pub["content_text"] or ""
    clauses = clauses_for(text)

    # Security pass on the untrusted document before any model sees it.
    findings = security.scan_injection(text)
    llm_text, pii_counts = (security.redact_pii(text) if security.redaction_enabled() else (text, {}))

    mode = "mock"
    if llm.is_live():
        try:
            result = llm_extract(llm_text, clauses, pub["content_hash"] or "")
            mode = "live"
        except llm.LLMError:
            result = rule_based_extract(text, clauses)
            mode = "mock (LLM failed)"
    else:
        result = rule_based_extract(text, clauses)

    # Validate and ground every obligation the model (or the rules) returned.
    clause_text = {c.ref.lower(): c.text for c in clauses}
    obligations, dropped = [], 0
    for o in result.get("obligations", []) or []:
        if not isinstance(o, dict):
            continue
        txt = security.clamp_text(o.get("text"), 1000)
        if not txt:
            continue
        if security.is_injection_sentence(txt):
            dropped += 1
            continue
        o["text"] = txt
        o["clause_ref"] = canonical_ref(security.clamp_text(o.get("clause_ref"), 60) or "unreferenced", clauses)
        o["applies_to"] = security.clamp_text(o.get("applies_to"), 300)
        o["deadline_on"] = parse_date(o.get("deadline_on"))
        if o.get("obligation_type") not in OBLIGATION_TYPES:
            o["obligation_type"] = "mandatory"
        try:
            conf = float(o.get("confidence") if o.get("confidence") is not None else 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        source = clause_text.get(o["clause_ref"].lower()) or text
        o["grounding"] = security.grounding_score(txt, source)
        if o["grounding"] < security.GROUNDING_THRESHOLD:
            conf = min(conf, 0.35)  # possible hallucination: force human check
        if findings:
            conf = min(conf, 0.5)   # document carried injection attempts: never auto-trust
        o["confidence"] = round(conf, 2)
        obligations.append(o)
    db.replace_obligations(conn, pub_id, obligations)
    if dropped:
        findings.append({"type": "injected_obligation_dropped", "snippet": f"{dropped} model output item(s) "
                         "matched attack patterns and were discarded"})

    updates = {"status": "extracted", "extract_mode": mode,
               "security_flags": json.dumps(findings) if findings else None,
               "pii_redactions": json.dumps(pii_counts) if pii_counts else None}
    if not pub["title"] and result.get("title"):
        updates["title"] = result["title"]
    if not pub["reference_no"] and result.get("reference_no"):
        updates["reference_no"] = result["reference_no"]
    if not pub["published_on"]:
        pubd = find_published(text)
        if pubd:
            updates["published_on"] = pubd
    if not pub["effective_on"] and result.get("effective_on"):
        updates["effective_on"] = parse_date(result["effective_on"])
    if not pub["business_area"] and result.get("business_area") in BUSINESS_AREAS:
        updates["business_area"] = result["business_area"]
    if not pub["doc_type"] and result.get("doc_type") in DOC_TYPES:
        updates["doc_type"] = result["doc_type"]
    if not obligations:
        updates["status"] = "extraction_failed" if mode != "mock" else "extracted"
    db.update_publication(conn, pub_id, **updates)
    return {"obligations": len(obligations), "mode": mode}
