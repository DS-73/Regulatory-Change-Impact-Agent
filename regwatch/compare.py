"""Capability 3: match a publication to its prior regulation and record every clause change.

Step 1 retrieve: previous version in the same document group (score 1.0), else an explicit
        cross-reference to a prior reference number (1.0), else TF-IDF within the jurisdiction.
        A reviewer can also force the link ("Force link with prior circular").
Step 2 pair:     live mode: the LLM pairs prior clauses with new clauses and classifies each pair.
        Mock mode: deterministic alignment by clause number / similarity + rule classification.
Step 3 store:    one comparison row + one comparison_clause row per change. Clause text is
        always copied from the source documents, never from model output. difflib is used
        only by the UI for red/green highlighting.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from . import db, llm, security
from .extract import clauses_for
from .models import CHANGE_CLASSES, CHANGE_LABELS
from .textutil import find_dates, norm_ws, tfidf_cosine

MATCH_THRESHOLD = 0.45  # without an explicit cross-reference or version link, only strong text similarity auto-links
MAX_CHANGES = 40
MAX_PROMPT_CLAUSES = 150

PAIR_CLASSES = [c for c in CHANGE_CLASSES if c not in ("no_reliable_match", "no_material_change")]

PAIR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "prior_ref": {"type": ["string", "null"]},
                    "new_ref": {"type": ["string", "null"]},
                    "change_class": {"type": "string", "enum": PAIR_CLASSES},
                    "summary": {"type": "string"},
                },
                "required": ["prior_ref", "new_ref", "change_class", "summary"],
            },
        }
    },
    "required": ["pairs"],
}

PAIR_PROMPT = """You compare a PRIOR regulation with a NEW one (an amendment, revision or new version).
Both are given as lists of clauses: "[ref] text". Clauses may be renumbered, merged or split, and the text
may be Arabic or English.
1. Pair each NEW clause with the PRIOR clause it replaces or amends (prior_ref), or null if it is entirely new.
2. List PRIOR clauses that were removed with new_ref = null.
3. Skip pairs whose meaning is unchanged (editorial or numbering-only edits).
For each remaining pair choose change_class:
- new_obligation: a requirement that did not exist before
- modified: an existing requirement's scope, threshold or substance changed
- withdrawn: a requirement removed, repealed or superseded
- deadline_changed: the main change is a compliance date or period being extended or shortened
- clarification: wording explains or clarifies without changing the requirement
summary: one or two plain-English sentences on why it matters to a compliance team.
Use refs exactly as written in the lists. Base everything only on the clause text provided."""


# ---------------------------------------------------------------- step 1: retrieval

def _norm_ref(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_prior(conn, pub) -> tuple[object | None, float, str, object | None]:
    """Return (best_prior_row_or_None, score, reason, best_candidate_even_if_below_threshold)."""
    if pub["published_on"]:
        cands = conn.execute(
            """SELECT p.* FROM publication p JOIN source s ON s.id=p.source_id
               WHERE s.jurisdiction=? AND p.id<>? AND
                 (p.published_on < ? OR (p.published_on IS NULL AND p.id < ?) OR (p.published_on = ? AND p.id < ?))""",
            (pub["jurisdiction"], pub["id"], pub["published_on"], pub["id"], pub["published_on"], pub["id"]),
        ).fetchall()
    else:
        cands = conn.execute(
            """SELECT p.* FROM publication p JOIN source s ON s.id=p.source_id
               WHERE s.jurisdiction=? AND p.id < ?""",
            (pub["jurisdiction"], pub["id"]),
        ).fetchall()
    if not cands:
        return None, 0.0, "no earlier publication from this jurisdiction", None

    text = pub["content_text"] or ""
    flat = _norm_ref(text)
    # Explicit cross-references win.
    for c in sorted(cands, key=lambda r: r["published_on"] or "", reverse=True):
        ref = _norm_ref(c["reference_no"])
        if len(ref) >= 6 and ref in flat:
            return c, 1.0, f"cites reference {c['reference_no']}", c

    query = f"{pub['title'] or ''}\n{text[:20000]}"
    sims = tfidf_cosine(query, [f"{c['title'] or ''}\n{(c['content_text'] or '')[:20000]}" for c in cands])
    scored = []
    for c, s in zip(cands, sims):
        if pub["business_area"] and c["business_area"] == pub["business_area"]:
            s += 0.05
        scored.append((min(s, 0.99), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if best_score >= MATCH_THRESHOLD:
        return best, round(best_score, 3), f"text similarity {best_score:.2f}", best
    return None, round(best_score, 3), f"best text similarity {best_score:.2f} below {MATCH_THRESHOLD}", best


# ---------------------------------------------------------------- step 2: diff

def _key(ref: str) -> str:
    m = re.search(r"\d+(?:\.\d+)*", ref)
    return m.group(0) if m and not ref.startswith("p.") else ref


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_ws(a).lower(), norm_ws(b).lower()).ratio()


def diff_clauses(old_text: str, new_text: str) -> list[dict]:
    old_c, new_c = clauses_for(old_text), clauses_for(new_text)
    old_by = {_key(c.ref): c for c in old_c}
    new_keys = [_key(c.ref) for c in new_c]
    overlap = sum(1 for k in new_keys if k in old_by) / max(len(new_keys), 1)

    changes: list[dict] = []
    matched_old: set[str] = set()
    for c in new_c:
        k = _key(c.ref)
        prior = old_by.get(k) if overlap >= 0.3 else None
        if prior is None and overlap < 0.3:
            # Similarity alignment only when the two documents use different numbering.
            best, br = None, 0.0
            for oc in old_c:
                r = _ratio(oc.text, c.text)
                if r > br:
                    best, br = oc, r
            prior = best if br >= 0.55 else None
        if prior is not None:
            matched_old.add(_key(prior.ref))
            if norm_ws(prior.text).lower() == norm_ws(c.text).lower():
                continue
            if len(prior.text) < 40 and len(c.text) < 40:
                continue  # heading-only tweak
            changes.append({"clause_ref": c.ref, "old": prior.text, "new": c.text})
        else:
            if len(c.text) < 40:
                continue
            changes.append({"clause_ref": c.ref, "old": None, "new": c.text})

    # Removals only make sense if the new document restates most of the old one.
    coverage = len(matched_old) / max(len(old_c), 1)
    if coverage >= 0.5:
        for oc in old_c:
            if _key(oc.ref) not in matched_old and len(oc.text) >= 40:
                changes.append({"clause_ref": oc.ref, "old": oc.text, "new": None})
    return changes[:MAX_CHANGES]


# ---------------------------------------------------------------- step 3: classify

WITHDRAW_RE = re.compile(r"\b(withdrawn|repealed|deleted|rescinded|no longer (?:apply|applies|required)|ceases? to|superseded)\b", re.I)
CLARIFY_RE = re.compile(r"\b(for the avoidance of doubt|clarif\w*|for clarity)\b", re.I)
NUMBER_WORDS = r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|sixty|ninety|hundred)\b"


def _quantities(s: str) -> list[str]:
    return re.findall(rf"\d+(?:\.\d+)?|{NUMBER_WORDS}", s.lower())


def word_opcodes(a: list[str], b: list[str]):
    """difflib opcodes over words, ignoring case and attached punctuation."""
    key = lambda w: re.sub(r"\W", "", w.lower()) or w  # noqa: E731
    return SequenceMatcher(None, [key(w) for w in a], [key(w) for w in b], autojunk=False).get_opcodes()


def _word_delta(old: str, new: str) -> tuple[str, str]:
    a, b = norm_ws(old).split(), norm_ws(new).split()
    added, removed = [], []
    for op, i1, i2, j1, j2 in word_opcodes(a, b):
        if op in ("replace", "delete"):
            removed.append(" ".join(a[i1:i2]).strip(" ,;."))
        if op in ("replace", "insert"):
            added.append(" ".join(b[j1:j2]).strip(" ,;."))
    return " … ".join(x for x in added if x)[:220], " … ".join(x for x in removed if x)[:220]


def _strip_dates(s: str) -> str:
    return re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4}\b|\b[A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b",
                  "<date>", s)


def rule_classify(ch: dict) -> tuple[str, str]:
    old, new, ref = ch["old"], ch["new"], ch["clause_ref"]
    if old is None:
        if WITHDRAW_RE.search(new):
            return "withdrawn", f"{ref} states that an earlier requirement is withdrawn or superseded: “{new[:160]}”."
        if CLARIFY_RE.search(new):
            return "clarification", f"{ref} adds clarifying guidance: “{new[:160]}”."
        return "new_obligation", f"New requirement introduced in {ref}: “{new[:180]}”."
    if new is None:
        return "withdrawn", f"{ref} of the prior instrument no longer appears in the updated text, so this requirement appears to be withdrawn."
    if WITHDRAW_RE.search(new) and not WITHDRAW_RE.search(old):
        return "withdrawn", f"{ref} now withdraws or supersedes the earlier requirement."
    old_d = [d for _, d in find_dates(old)]
    new_d = [d for _, d in find_dates(new)]
    if old_d and new_d and old_d != new_d and _ratio(_strip_dates(old), _strip_dates(new)) > 0.8:
        o, n = old_d[-1], new_d[-1]
        direction = "extended" if n > o else "brought forward"
        return "deadline_changed", (
            f"The compliance date in {ref} is {direction} from {o:%d %b %Y} to {n:%d %b %Y} "
            f"({abs((n - o).days)} days). Re-plan implementation milestones accordingly.")
    added, removed = _word_delta(old, new)
    if CLARIFY_RE.search(new) and not CLARIFY_RE.search(old) and _ratio(old, new) > 0.6:
        return "clarification", f"{ref} keeps the requirement but adds clarification: “{added[:160]}”."
    if _quantities(old) != _quantities(new) and len(added) < 60:
        return "modified", (f"A threshold or period in {ref} has changed: “{removed or '—'}” is now “{added or '—'}”. "
                            "Check limits, timelines and controls that depend on it.")
    if re.sub(r"\W+", "", old.lower()) == re.sub(r"\W+", "", new.lower()):
        return "no_material_change", f"Editorial wording change in {ref}; no change in substance."
    parts = []
    if added:
        parts.append(f"adds “{added[:150]}”")
    if removed:
        parts.append(f"removes “{removed[:150]}”")
    return "modified", f"The requirement in {ref} has changed: it " + " and ".join(parts) + "."




# ---------------------------------------------------------------- live: LLM pairs the clauses

def _prep(s: str) -> str:
    s = security.sanitize_for_prompt(s or "")[:700]
    return security.redact_pii(s)[0] if security.redaction_enabled() else s


def changed_clauses(old_text: str, new_text: str):
    """Token saver: clauses whose text is word-for-word identical in both versions are dropped before
    anything is sent to the model. Returns (all_old, all_new, changed_old, changed_new)."""
    old_all, new_all = clauses_for(old_text), clauses_for(new_text)
    old_norm = {norm_ws(c.text).lower() for c in old_all}
    new_norm = {norm_ws(c.text).lower() for c in new_all}
    ch_old = [c for c in old_all if norm_ws(c.text).lower() not in new_norm][:MAX_PROMPT_CLAUSES]
    ch_new = [c for c in new_all if norm_ws(c.text).lower() not in old_norm][:MAX_PROMPT_CLAUSES]
    return old_all, new_all, ch_old, ch_new


def _listing(old_c, new_c) -> str:
    return ("PRIOR clauses:\n" + "\n".join(f"[{c.ref}] {_prep(c.text)}" for c in old_c)
            + "\n\nNEW clauses:\n" + "\n".join(f"[{c.ref}] {_prep(c.text)}" for c in new_c))


def prompt_budget(old_text: str, new_text: str) -> dict:
    """Characters that a naive full-document comparison would send vs what RegWatch sends."""
    old_all, new_all, ch_old, ch_new = changed_clauses(old_text, new_text)
    naive = len(old_text or "") + len(new_text or "")
    sent = len(_listing(ch_old, ch_new)) if (ch_old or ch_new) else 0
    return {"full_documents_chars": naive, "sent_chars": sent, "approx_tokens_sent": sent // 4,
            "clauses_total": len(old_all) + len(new_all), "clauses_sent": len(ch_old) + len(ch_new)}


def llm_pair(old_text: str, new_text: str, doc_hash: str = "") -> list[dict]:
    old_all, new_all, old_c, new_c = changed_clauses(old_text, new_text)
    if not old_c and not new_c:
        return []  # identical clause text: no model call, zero tokens
    listing = _listing(old_c, new_c)
    res = llm.chat_json(
        [{"role": "system", "content": PAIR_PROMPT + "\n\n" + security.GUARDRAIL},
         {"role": "user", "content": security.wrap_untrusted(listing, "untrusted_clauses")}],
        PAIR_SCHEMA, "clause_pairs", purpose="pair", doc_hash=doc_hash, max_tokens=4000)
    old_by = {c.ref.lower(): c for c in old_all} | {_key(c.ref): c for c in old_all}
    new_by = {c.ref.lower(): c for c in new_all} | {_key(c.ref): c for c in new_all}

    def find(ref, table):
        if not ref:
            return None
        r = str(ref).strip().strip("[]")
        return table.get(r.lower()) or table.get(_key(r))

    out = []
    for p in res.get("pairs", []) or []:
        if not isinstance(p, dict) or p.get("change_class") not in PAIR_CLASSES:
            continue
        o, n = find(p.get("prior_ref"), old_by), find(p.get("new_ref"), new_by)
        if o is None and n is None:
            continue  # model referenced clauses that don't exist: drop (grounding)
        if (o is None or len(o.text) < 40) and (n is None or len(n.text) < 40):
            continue  # heading-only change (e.g. a section title): not a regulatory change
        summary = security.clamp_text(p.get("summary"), 600) or ""
        if security.is_injection_sentence(summary):
            continue
        if o is not None and n is not None and norm_ws(o.text).lower() == norm_ws(n.text).lower():
            continue
        out.append({"prior_ref": o.ref if o else None, "new_ref": n.ref if n else None,
                    "change_class": p["change_class"], "summary": summary,
                    # clause text always comes from the documents, never from the model
                    "old_clause": o.text if o else None, "new_clause": n.text if n else None})
    return out[:MAX_CHANGES]


def rule_pairs(old_text: str, new_text: str) -> list[dict]:
    out = []
    for ch in diff_clauses(old_text, new_text):
        cls, summ = rule_classify(ch)
        if cls == "no_material_change":
            continue
        out.append({"prior_ref": ch["clause_ref"] if ch["old"] is not None else None,
                    "new_ref": ch["clause_ref"] if ch["new"] is not None else None,
                    "change_class": cls, "summary": summ, "old_clause": ch["old"], "new_clause": ch["new"]})
    return out


# ---------------------------------------------------------------- entry point

def previous_version(conn, pub):
    if not pub["document_group_id"] or (pub["version"] or 1) <= 1:
        return None
    return conn.execute("SELECT * FROM publication WHERE document_group_id=? AND version<? ORDER BY version DESC",
                        (pub["document_group_id"], pub["version"])).fetchone()


def compare_publication(conn, pub_id: int, forced_prior_id: int | None = None) -> dict:
    pub = db.get_publication(conn, pub_id)
    link_type = "auto"
    if forced_prior_id:
        prior = conn.execute("SELECT * FROM publication WHERE id=?", (forced_prior_id,)).fetchone()
        score, reason, best, link_type = 1.0, "linked manually by a reviewer", prior, "manual"
    elif (pv := previous_version(conn, pub)) is not None:
        prior, score, reason, best, link_type = pv, 1.0, f"previous version (v{pv['version']}) of the same document", pv, "version"
    else:
        prior, score, reason, best = find_prior(conn, pub)

    if prior is None and best is None:
        db.replace_comparison(conn, pub_id, None, [])  # baseline: nothing earlier in this jurisdiction
        db.update_publication(conn, pub_id, status="compared")
        return {"class": "baseline", "changes": 0}
    if prior is None:
        hint = f" Closest candidate: “{best['title']}”." if best is not None else ""
        db.replace_comparison(conn, pub_id, {
            "prior_pub_id": None, "overall_class": "no_reliable_match", "match_score": score,
            "match_reason": reason, "compare_mode": "retrieval", "link_type": "auto",
            "summary": "Could not establish a reliable relationship with an earlier publication "
                       f"({reason}). A reviewer can force the link from the Document tab.{hint}"}, [])
        db.update_publication(conn, pub_id, status="compared")
        return {"class": "no_reliable_match", "changes": 0}

    mode = "mock"
    pairs = None
    if llm.is_live():
        try:
            pairs = llm_pair(prior["content_text"] or "", pub["content_text"] or "", pub["content_hash"] or "")
            mode = "live"
        except llm.LLMError:
            mode = "mock (LLM failed)"
    if pairs is None:
        pairs = rule_pairs(prior["content_text"] or "", pub["content_text"] or "")
    counts = {}
    for p in pairs:
        counts[p["change_class"]] = counts.get(p["change_class"], 0) + 1
    summary = ("; ".join(f"{n} {CHANGE_LABELS[c].lower()}" for c, n in counts.items()) if pairs
               else "No material differences found between the clauses of the two publications.")
    db.replace_comparison(conn, pub_id, {
        "prior_pub_id": prior["id"], "overall_class": "changes" if pairs else "no_material_change",
        "summary": summary, "match_score": score, "match_reason": reason, "compare_mode": mode,
        "link_type": link_type}, pairs)
    db.update_publication(conn, pub_id, status="compared")
    return {"class": "matched", "prior": prior["id"], "changes": len(pairs), "mode": mode, "link": link_type}
