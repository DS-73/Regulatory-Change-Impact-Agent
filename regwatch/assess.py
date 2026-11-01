"""Level 2 · Regulatory-to-internal mapping, compliance gap analysis and recommended changes.

For every new obligation the agent finds which internal components are potentially affected:
policies, SOPs, business processes, AI governance policies, systems (from the system inventory)
and controls (from the control catalogue). For each affected component it records:

  the regulatory obligation · the affected component · why it is impacted · the existing internal
  requirement (verbatim) · the identified gap · the recommended action · proposed policy / control
  wording · business area · implementation priority · confidence · supporting references · owner.

Gap analysis distinguishes four outcomes per obligation:
  compliant (addressed)  – an internal requirement fully covers it, including numbers and deadlines
  partial                – a component covers the topic but misses part of it
  gap                    – related documentation exists, but nothing requires this
  insufficient_evidence  – the repository holds nothing on this topic (or the obligation cannot be
                           compared reliably), so no determination is made

Recommendations are drafts until an authorised user approves them. Owners come only from the
ownership register; when no owner can be determined the item is flagged for manual assignment.
Live mode asks the model (via function calling); mock mode uses deterministic rules.
"""
from __future__ import annotations

import re

import yaml

from . import CONFIG_DIR, db, llm, security
from .internal import COMPONENT_TYPE, resolve_owner
from .textutil import find_dates, tfidf_cosine, tokens

STATUSES = ["compliant", "partial", "gap", "insufficient_evidence"]
VERDICT_LABEL = {"compliant": "Addressed", "partial": "Partially addressed", "gap": "No internal control",
                 "insufficient_evidence": "Insufficient evidence"}
TOP_K = 4            # candidate components shown to the model per obligation
MAX_IMPACTED = 3     # components kept per obligation
RELATED = 0.10       # similarity for a component to count as related to the obligation
NOTHING = 0.10       # nothing at least this related in the repository -> insufficient evidence
BATCH = 15

META_RE = re.compile(r"\b(comply with (this|these)|these (rules|directions|guidelines|guidance) (apply|set)|"
                     r"this (circular|guidance|notice) (applies|sets))\b", re.I)
QTY_RE = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|thirty|sixty|ninety)\s+"
                    r"(working days?|business days?|days?|months?|years?|hours?|per cent|percent)\b", re.I)
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "twelve": 12, "fifteen": 15, "thirty": 30, "sixty": 60, "ninety": 90}
CONDITIONAL = re.compile(r"\b(only if|only when|only on|on appeal|if the customer appeals|on a sample basis|"
                         r"sample basis|where practicable|unless)\b", re.I)
SUBJECT = re.compile(r"^(all )?(licensed financial institutions|regulated entities|financial institutions|banks|"
                     r"open banking participants|participants|api providers|digital lending apps|"
                     r"senior management|the board of directors|borrowers)\b", re.I)
AREA_DEPT = {"AI": "AI Governance", "AML": "Compliance", "banking": "Risk", "lending": "Ops",
             "payments": "Ops", "insurance": "Compliance"}

# Words that say who/what generically and carry no requirement content.
GENERIC = set("""licensed financial institution institutions bank banks banking regulated entity entities
participant participants provider providers customer customers shall must ensure provide conduct maintain
apply every each before after such being able through their which where other within using made make
given give take taking whether there these those this that into upon also only already least
thereafter implement services entering execution""".split())


def _stem(t: str) -> str:
    for suf in ("ings", "ing", "ies", "es", "ed", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


# ---------------------------------------------------------------- control catalogue

def load_controls_yaml() -> list[dict]:
    p = CONFIG_DIR / "controls.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("controls", []) if p.exists() else []


def seed_controls(conn) -> int:
    n = 0
    for c in load_controls_yaml():
        db.upsert_control(conn, c["code"], c["title"], c["owner_dept"], c["answer"])
        n += 1
    return n


def assessable(o) -> bool:
    return o["obligation_type"] in ("mandatory", "prohibition") and not META_RE.search(o["text"] or "")


# ---------------------------------------------------------------- internal components

def components(conn) -> list[dict]:
    """Everything an obligation can map to: document sections and catalogue controls."""
    out = []
    for s in db.sections_with_docs(conn):
        dept = s["owner_dept"]
        m = re.search(r"owner_dept:\s*([^,]+)", s["section_text"] or "")
        if s["doc_type"] == "system_inventory" and m:
            dept = m.group(1).strip()
        label = (f"{s['code']} · {s['section_title']}" if s["doc_type"] == "system_inventory"
                 else f"{s['code']} {s['section_ref'].replace('Clause ', '§')} · {s['doc_title']}")
        out.append({"key": f"S{s['section_id']}", "component_type": COMPONENT_TYPE.get(s["doc_type"], "policy"),
                    "internal_doc_id": s["doc_id"], "section_id": s["section_id"], "control_id": None,
                    "label": label, "ref": f"{s['code']} {s['section_ref']}", "text": s["section_text"] or "",
                    "owner_dept": dept, "business_area": s["business_area"]})
    for c in db.list_controls(conn):
        out.append({"key": f"C{c['id']}", "component_type": "control", "internal_doc_id": None, "section_id": None,
                    "control_id": c["id"], "label": f"{c['code']} · {c['title']}", "ref": c["code"],
                    "text": c["answer"] or "", "owner_dept": c["owner_dept"], "business_area": None})
    return out


def _qty(text: str) -> dict[str, set[float]]:
    out: dict[str, set[float]] = {}
    for n, unit in QTY_RE.findall(text or ""):
        v = float(n) if n.isdigit() else float(WORDNUM.get(n.lower(), 0))
        u = re.sub(r"s$", "", unit.lower().replace("business", "working"))
        out.setdefault(u, set()).add(v)
    return out


def _salient(text: str) -> list[str]:
    return [t for t in dict.fromkeys(tokens(text)) if len(t) > 3 and t not in GENERIC]


def adapt(obligation: str) -> str:
    """Turn the regulatory sentence into internal policy wording ("The Bank shall ...")."""
    t = re.sub(r"\s+", " ", obligation or "").strip()
    t = SUBJECT.sub("The Bank", t, count=1)
    t = re.sub(r"\bBanks\b", "The Bank", t)
    return t[0].upper() + t[1:] if t else t


def _evidence_sentence(text: str) -> str:
    return (" The decision, the reviewer's identity and the outcome of the review shall be recorded and retained "
            "in the audit trail." if re.search(r"\b(review|approv\w*|decision|declin\w*)\b", text, re.I) else "")


def _sentence_with(text: str, needle: str) -> str:
    for s in re.split(r"(?<=[.;])\s+", text):
        if needle.lower() in s.lower():
            return s.strip()
    return text[:300]


# ---------------------------------------------------------------- rule-based evaluation

def evaluate(o: dict, comp: dict, sim: float, reg_ref: str) -> dict:
    """Verdict of one component against one obligation, with gap, action and draft wording."""
    text, ctext = o["text"], comp["text"]
    shared = [t for t in _salient(text) if _stem(t) in {_stem(x) for x in tokens(ctext)}][:4]
    kind = {"control": "control", "system": "system", "process": "process", "sop": "procedure"}.get(comp["component_type"], "policy")
    reason = (f"The {kind} {comp['label']} governs the same activity ({', '.join(shared) or 'related topic'}); "
              f"text similarity {sim:.2f}.")
    if comp["component_type"] == "system":
        reason = f"System {comp['label']} performs this activity, so its configuration and controls are in scope."

    # 1. Numbers with units must match (30 days vs 60 days).
    oq, cq = _qty(text), _qty(ctext)
    for unit, vals in oq.items():
        if unit in cq and not (vals & cq[unit]):
            old = ", ".join(f"{v:g} {unit}s" for v in sorted(cq[unit]))
            new = ", ".join(f"{v:g} {unit}s" for v in sorted(vals))
            sent = _sentence_with(ctext, next(iter(re.findall(rf"\b\w+\s+{unit}s?\b", ctext, re.I)), ""))
            num_old, num_new = int(sorted(cq[unit])[0]), int(sorted(vals)[0])
            revised = re.sub(rf"\b({num_old}|{_word(num_old)})(\s+{unit}s?)\b", f"{num_new}\\2", sent, flags=re.I)
            return {"verdict": "partial", "reason": reason,
                    "compliance_gap": f"{comp['label']} specifies {old}, but {reg_ref} requires {new}; the current "
                                      f"internal requirement is therefore insufficient.",
                    "recommended_action": f"Amend {comp['ref']}: change {old} to {new} and communicate the change to the "
                                          f"teams that apply it.",
                    "proposed_wording": f"Replace {comp['ref']} with: “{revised}”"}
    # 2. Deadlines / dates must match.
    od, cd = {d for _, d in find_dates(text)}, {d for _, d in find_dates(ctext)}
    if od and cd and not (od & cd):
        o_s = ", ".join(f"{d:%d %B %Y}" for d in sorted(od))
        c_s = ", ".join(f"{d:%d %B %Y}" for d in sorted(cd))
        sent = _sentence_with(ctext, f"{sorted(cd)[0].year}")
        revised = sent
        for _, d in find_dates(sent):
            revised = re.sub(rf"{d.day:d}\s+{d:%B}\s+{d.year}", f"{sorted(od)[-1]:%d %B %Y}".lstrip("0"), revised)
        return {"verdict": "partial", "reason": reason,
                "compliance_gap": f"{comp['label']} works to {c_s}; {reg_ref} now requires {o_s}.",
                "recommended_action": f"Amend {comp['ref']}: move the date from {c_s} to {o_s} and re-plan the "
                                      f"implementation milestones.",
                "proposed_wording": f"Replace {comp['ref']} with: “{revised}”"}
    # 3. Conditional internal requirement vs unconditional regulation.
    cond = CONDITIONAL.search(ctext)
    if cond and not CONDITIONAL.search(text) and len(shared) >= 2:
        return {"verdict": "partial", "reason": reason,
                "compliance_gap": f"{comp['label']} applies the requirement only conditionally (“{cond.group(0)}”), "
                                  f"whereas {reg_ref} requires it in every case.",
                "recommended_action": f"Amend {comp['ref']} so the requirement applies to every case in scope of "
                                      f"{reg_ref}, and add evidence of compliance to the audit trail.",
                "proposed_wording": f"Replace {comp['ref']} with: “{adapt(text)}{_evidence_sentence(text)}”"}
    # 4. Salient terms of the obligation should appear in the internal text.
    ot = _salient(text)
    have = {_stem(t) for t in tokens(ctext)}
    missing = [t for t in ot if _stem(t) not in have]
    coverage = 1 - len(missing) / max(len(ot), 1)
    if coverage >= 0.7:
        return {"verdict": "compliant", "reason": reason, "coverage": coverage,
                "compliance_gap": "None identified: the existing requirement covers the obligation.",
                "recommended_action": "No change needed. Keep evidence of operation for audit.", "proposed_wording": None}
    if coverage >= 0.4 or comp["component_type"] == "system":
        return {"verdict": "partial", "reason": reason, "coverage": coverage,
                "compliance_gap": f"{comp['label']} covers the topic but does not address: {', '.join(missing[:6])}.",
                "recommended_action": f"Extend {comp['ref']} to cover {', '.join(missing[:4])} as required by {reg_ref}.",
                "proposed_wording": f"Add to {comp['ref']}: “{adapt(text)}{_evidence_sentence(text)}”"}
    return {"verdict": "gap", "reason": reason, "coverage": coverage,
            "compliance_gap": f"{comp['label']} is related but contains no requirement on: {', '.join(missing[:6])}.",
            "recommended_action": f"Add a new requirement to {comp['ref']} implementing {reg_ref}.",
            "proposed_wording": f"Add to {comp['ref']}: “{adapt(text)}{_evidence_sentence(text)}”"}


def _word(n: int) -> str:
    return next((w for w, v in WORDNUM.items() if v == n), str(n))


def priority(verdict: str, days_left: int | None) -> str:
    soon = days_left is not None and days_left <= 90
    if verdict == "gap":
        return "High"
    if verdict == "partial":
        return "High" if soon else "Medium"
    if verdict == "insufficient_evidence":
        return "High" if soon else "Medium"
    return "Low"


# ---------------------------------------------------------------- live (model) evaluation

MAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "index": {"type": "integer"},
            "verdict": {"type": "string", "enum": STATUSES},
            "components": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["compliant", "partial", "gap"]},
                    "reason": {"type": "string"},
                    "compliance_gap": {"type": "string"},
                    "recommended_action": {"type": "string"},
                    "proposed_wording": {"type": ["string", "null"]},
                    "confidence": {"type": "number"}},
                "required": ["key", "verdict", "reason", "compliance_gap", "recommended_action", "proposed_wording",
                             "confidence"]}},
            "missing_evidence": {"type": ["string", "null"]}},
        "required": ["index", "verdict", "components", "missing_evidence"]}}},
    "required": ["results"],
}

MAP_PROMPT = """You are a compliance analyst mapping new regulatory obligations to an organisation's internal
documentation (policies, SOPs, process documents, AI governance policies, system inventory rows and controls).
For each numbered obligation you get candidate internal components, each with a key and its exact text.
1. List only components that are genuinely affected (max 3), using their keys. For each give:
   verdict (compliant = fully meets it incl. numbers/deadlines/scope; partial = covers the topic but misses part;
   gap = related but contains no such requirement), why it is impacted, what exactly is missing and why the
   existing text is insufficient, an actionable recommended action (not generic), and proposed_wording: the exact
   replacement or additional policy/control text to adopt (null if compliant).
2. Overall verdict: compliant / partial / gap, or insufficient_evidence when the candidates do not contain enough
   information to decide; then explain in missing_evidence what document is needed.
Only use the text provided. Obligations may be Arabic or English; answer in English."""


def llm_map(items: list[tuple], doc_hash: str) -> list[dict | None]:
    blocks = []
    for i, (o, cands, reg_ref) in enumerate(items):
        ctext = "\n".join(f"  [{c['key']}] {c['label']}: {security.clamp_text(c['text'], 500)}" for _, c in cands) \
            or "  (no candidate components)"
        blocks.append(f"### Obligation {i} ({reg_ref})\n{security.sanitize_for_prompt(o['text'])}\nCandidates:\n{ctext}")
    res = llm.chat_json(
        [{"role": "system", "content": MAP_PROMPT + "\n\n" + security.GUARDRAIL},
         {"role": "user", "content": security.wrap_untrusted("\n\n".join(blocks), "untrusted_obligations")}],
        MAP_SCHEMA, "impact_mapping", purpose="assess", doc_hash=doc_hash, max_tokens=4000)
    out: list[dict | None] = [None] * len(items)
    for r in res.get("results", []) or []:
        if not isinstance(r, dict) or not isinstance(r.get("index"), int) or not 0 <= r["index"] < len(items):
            continue
        if r.get("verdict") not in STATUSES:
            continue
        offered = {c["key"]: (s, c) for s, c in items[r["index"]][1]}
        comps = []
        for c in (r.get("components") or [])[:MAX_IMPACTED]:
            if not isinstance(c, dict) or c.get("key") not in offered or c.get("verdict") not in ("compliant", "partial", "gap"):
                continue  # the model may only cite components we offered
            fields = {k: security.clamp_text(c.get(k), 900) for k in ("reason", "compliance_gap", "recommended_action",
                                                                       "proposed_wording")}
            if any(v and security.is_injection_sentence(v) for v in fields.values()):
                continue
            try:
                conf = max(0.0, min(1.0, float(c.get("confidence", 0.6))))
            except (TypeError, ValueError):
                conf = 0.6
            comps.append({"key": c["key"], "verdict": c["verdict"], "confidence": round(conf, 2), **fields})
        out[r["index"]] = {"verdict": r["verdict"], "components": comps,
                           "missing_evidence": security.clamp_text(r.get("missing_evidence"), 400)}
    return out


# ---------------------------------------------------------------- per-obligation mapping

def documented_areas(conn) -> set[str]:
    """Business areas covered by at least one uploaded policy / SOP / process document."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT business_area FROM internal_document WHERE business_area IS NOT NULL "
        "AND doc_type IN ('policy','sop','process','ai_policy')")}


def _days_left(d: str | None) -> int | None:
    from .impact import days_until
    return days_until(d)


def map_obligation(conn, o: dict, pub, comps: list[dict], sims: list[float], live: dict | None, mode: str) -> tuple[dict, list[dict]]:
    """Return (overall assessment row, policy_mapping rows) for one obligation."""
    reg_ref = f"{pub['regulator']} {pub['reference_no'] or '#' + str(pub['id'])} {o['clause_ref']}"
    area = pub["business_area"]
    due = o["deadline_on"] or pub["effective_on"]
    days = _days_left(due)
    ranked = sorted(zip(sims, comps), key=lambda x: x[0], reverse=True)
    best_sim = ranked[0][0] if ranked else 0.0
    base = {"business_area": area, "mode": mode}
    rows: list[dict] = []

    def row(comp, ev, sim, conf=None):
        owner = resolve_owner(conn, area, o["text"], comp.get("owner_dept") if comp else None)
        return {**base, **owner,
                "internal_doc_id": comp and comp["internal_doc_id"], "section_id": comp and comp["section_id"],
                "control_id": comp and comp["control_id"], "component_type": comp["component_type"] if comp else "none",
                "component_label": comp["label"] if comp else "— no internal component —",
                "verdict": ev["verdict"], "reason": ev.get("reason"),
                "existing_requirement": comp["text"][:700] if comp else None,
                "compliance_gap": ev.get("compliance_gap"), "recommended_action": ev.get("recommended_action"),
                "proposed_wording": ev.get("proposed_wording"),
                "implementation_priority": priority(ev["verdict"], days),
                "confidence": conf if conf is not None else round(min(0.9, 0.35 + sim + 0.3 * ev.get("coverage", 0.5)), 2),
                "evidence_refs": [reg_ref] + ([comp["ref"]] if comp else []), "due_on": due}

    salient = _salient(o["text"])
    arabic_only = bool(re.search(r"[؀-ۿ]", o["text"])) and not re.search(r"[A-Za-z]{4,}", o["text"])
    vague = len(salient) < 2

    if live is not None:
        offered = {c["key"]: (s, c) for s, c in ranked[:TOP_K]}
        for lc in live["components"]:
            s, comp = offered[lc["key"]]
            rows.append(row(comp, lc, s, lc["confidence"]))
        verdict = live["verdict"]
        if not rows and verdict in ("gap", "insufficient_evidence"):
            rows.append(row(None, {"verdict": verdict, "reason": "No internal component addresses this obligation.",
                                   "compliance_gap": live.get("missing_evidence") or "No internal requirement found.",
                                   "recommended_action": "Provide the governing policy or create a new control."},
                            best_sim, 0.5))
    elif (arabic_only and mode != "live") or vague:
        verdict = "insufficient_evidence"
        why = ("the obligation is in Arabic and the internal repository is in English; rule-based matching cannot "
               "compare them reliably (live model mode can)") if arabic_only else "the obligation is too general to test"
        rows.append(row(None, {"verdict": verdict, "reason": "Cannot be assessed reliably.",
                               "compliance_gap": f"No determination made: {why}.",
                               "recommended_action": "Have a compliance analyst assess this obligation manually, or "
                                                     "re-run mapping in live mode."}, best_sim, 0.3))
    else:
        related = [(s, c) for s, c in ranked[:10] if s >= RELATED]
        evals = [(s, c, evaluate(o, c, s, reg_ref)) for s, c in related]
        # Systems are only "impacted" when the obligation really talks about what they do.
        evals = [x for x in evals if not (x[1]["component_type"] == "system" and x[2].get("coverage", 1) < 0.25)]
        # Best evidence first: components that (partly) address the obligation, then similarity;
        # one component per document / control so the matrix stays readable.
        rank = {"compliant": 0, "partial": 1, "gap": 2}
        evals.sort(key=lambda x: (rank[x[2]["verdict"]], -x[0]))
        seen, impacted = set(), []
        for s, c, e in evals:
            k = c["internal_doc_id"] or f"C{c['control_id']}"
            if k in seen:
                continue
            seen.add(k)
            impacted.append((s, c, e))
        if any(e["verdict"] != "gap" for _, _, e in impacted):
            impacted = [x for x in impacted if x[2]["verdict"] != "gap"]
        else:
            impacted = [x for x in impacted if x[0] >= 0.2]
        impacted = impacted[:MAX_IMPACTED]
        verdicts = {e["verdict"] for _, _, e in impacted}
        if "compliant" in verdicts:
            verdict = "compliant"
        elif "partial" in verdicts:
            verdict = "partial"
        elif impacted or (area in documented_areas(conn)):
            verdict = "gap"          # the area is documented, yet nothing requires this
        else:
            verdict = "insufficient_evidence"  # no internal documentation for this area at all
        for s, c, e in impacted:
            rows.append(row(c, e, s))
        if verdict == "gap" and not rows:
            near = ranked[0][1] if ranked else None
            dept = AREA_DEPT.get(area or "", "Compliance")
            rows.append(row(None, {
                "verdict": "gap",
                "reason": f"Related documentation exists (closest: {near['label']}, similarity {best_sim:.2f}) but none of it "
                          f"addresses this obligation." if near else "No related internal documentation.",
                "compliance_gap": "No internal policy, procedure or control requires this today.",
                "recommended_action": f"Create a new {dept} requirement implementing {reg_ref} and assign an owner.",
                "proposed_wording": f"New clause: “{adapt(o['text'])}{_evidence_sentence(o['text'])}”"}, best_sim, 0.5))
        if verdict == "insufficient_evidence":
            rows.append(row(None, {
                "verdict": verdict, "reason": f"The internal repository holds no documentation for the {area or 'relevant'} "
                                              f"business area.",
                "compliance_gap": f"Cannot be assessed: no policy, procedure or process covering {area or 'this area'} has "
                                  f"been uploaded (documented areas: {', '.join(sorted(documented_areas(conn))) or 'none'}), "
                                  f"so RegWatch cannot tell whether this obligation is already handled.",
                "recommended_action": f"Upload the {area or 'relevant'} policy or procedure that governs this activity, "
                                      "then re-run mapping."}, best_sim, 0.35))

    primary = next((r for r in rows if r["verdict"] == verdict), rows[0] if rows else None)
    summary = {"compliant": "Addressed by existing internal requirements.",
               "partial": "Partially addressed: existing requirements need amendment.",
               "gap": "No internal requirement addresses this obligation.",
               "insufficient_evidence": "Not enough internal documentation to make a reliable determination."}[verdict]
    assessment = {"obligation_id": o["id"], "control_id": next((r["control_id"] for r in rows if r["control_id"]), None),
                  "status": verdict, "rationale": f"{summary} {primary['compliance_gap'] if primary else ''}".strip(),
                  "recommended_change": primary["recommended_action"] if primary and verdict != "compliant" else None,
                  "proposed_wording": primary["proposed_wording"] if primary else None,
                  "implementation_priority": priority(verdict, days),
                  "confidence": primary["confidence"] if primary else 0.4, "mode": mode}
    return assessment, rows


def assess_publication(conn, pub_id: int, comps: list[dict] | None = None) -> dict:
    pub = db.get_publication(conn, pub_id)
    comps = comps if comps is not None else components(conn)
    obls = [dict(o) for o in conn.execute("SELECT * FROM obligation WHERE publication_id=? ORDER BY id", (pub_id,))
            if assessable(o)]
    corpus = [f"{c['label']} {c['text']}" for c in comps]
    sims_all = [tfidf_cosine(o["text"], corpus) if comps else [] for o in obls]
    live: list[dict | None] = [None] * len(obls)
    mode = "mock"
    if obls and llm.is_live() and comps:
        reg = f"{pub['regulator']} {pub['reference_no'] or ''}".strip()
        items = [(o, sorted(zip(s, comps), key=lambda x: x[0], reverse=True)[:TOP_K], f"{reg} {o['clause_ref']}")
                 for o, s in zip(obls, sims_all)]
        try:
            for b, start in enumerate(range(0, len(items), BATCH)):
                if b >= security.MAX_LLM_CALLS_PER_DOC:
                    break
                live[start:start + BATCH] = llm_map(items[start:start + BATCH], pub["content_hash"] or "")
            mode = "live"
        except llm.LLMError:
            mode = "mock (LLM failed)"
    assessments = []
    for o, s, lv in zip(obls, sims_all, live):
        a, rows = map_obligation(conn, o, pub, comps, s, lv, mode if lv else ("mock" if mode == "mock" else "mock (LLM failed)"))
        assessments.append(a)
        db.replace_policy_mappings(conn, o["id"], rows)
    db.replace_assessments(conn, pub_id, assessments)
    counts = {s: sum(a["status"] == s for a in assessments) for s in STATUSES}
    return {"assessed": len(assessments), **counts, "mode": mode}


def reassess_all(conn) -> int:
    comps = components(conn)
    ids = [r[0] for r in conn.execute("SELECT id FROM publication")]
    for i in ids:
        assess_publication(conn, i, comps)
    return len(ids)
