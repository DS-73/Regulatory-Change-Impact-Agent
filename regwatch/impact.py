"""Impact-matrix rules shared by the dashboard, the tour and the assistant (Level 2).

A matrix row links one regulatory obligation to one affected internal component.

Priority : from the mapping engine (gap -> High; partial / insufficient evidence due within 90 days -> High,
           otherwise Medium; addressed -> Low).
Workflow : recommendations are drafts until an authorised approver approves them; after approval the
           owner records implementation status (not started -> in progress -> implemented -> verified).
Owner    : only from the ownership register or a reviewer; otherwise "Needs manual assignment".
Module   : SUGGESTED Azentio module from config/product_map.yaml (keywords first, then business area).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from functools import lru_cache

import yaml

from . import CONFIG_DIR

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
SEVERITY_ORDER = PRIORITY_ORDER  # backwards-compatible name
VERDICT_LABEL = {"compliant": "Addressed", "partial": "Partially addressed", "gap": "No internal control",
                 "insufficient_evidence": "Insufficient evidence"}
VERDICT_ORDER = {"gap": 0, "partial": 1, "insufficient_evidence": 2, "compliant": 3}
IMPL_STATES = ["not_started", "in_progress", "implemented", "verified"]
IMPL_LABEL = {"not_started": "Not started", "in_progress": "In progress", "implemented": "Implemented",
              "verified": "Verified"}
WORKFLOW_ORDER = {"Draft · awaiting approval": 0, "Approved · not started": 1, "In progress": 2, "Implemented": 3,
                  "Verified": 4, "Rejected": 5, "Compliant": 6}
UNASSIGNED = "Needs manual assignment"


def days_until(d: str | None) -> int | None:
    if not d:
        return None
    try:
        return (date.fromisoformat(str(d)[:10]) - date.today()).days
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _map() -> dict:
    p = CONFIG_DIR / "product_map.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {"modules": []}


def module_for(text: str, business_area: str | None) -> str:
    m = _map()
    low = (text or "").lower()
    for mod in m.get("modules", []):
        if any(re.search(r"\b" + re.escape(k.lower()), low) for k in mod.get("keywords", [])):
            return mod["module"]
    for mod in m.get("modules", []):
        if business_area in mod.get("business_areas", []):
            return mod["module"]
    return m.get("default_module", "—")


def workflow(row: dict) -> str:
    if row.get("verdict") == "compliant":
        return "Compliant"
    if row.get("approval_status") == "rejected":
        return "Rejected"
    if row.get("approval_status") != "approved":
        return "Draft · awaiting approval"
    return {"not_started": "Approved · not started", "in_progress": "In progress", "implemented": "Implemented",
            "verified": "Verified"}.get(row.get("implementation_status") or "not_started", "Approved · not started")


def enrich_mapping(row: dict) -> dict:
    r = dict(row)
    r["priority"] = r.get("implementation_priority") or "Medium"
    r["verdict_label"] = VERDICT_LABEL.get(r.get("verdict"), r.get("verdict"))
    r["workflow"] = workflow(r)
    r["owner_label"] = (f"{r['owner_person']} · {r['owner_dept']}" if r.get("owner_person")
                        else (r.get("owner_dept") or UNASSIGNED))
    r["needs_owner"] = not r.get("owner_person") and not r.get("owner_dept")
    r["module"] = module_for(r.get("obligation", ""), r.get("business_area") or r.get("pub_area"))
    r["due"] = r.get("due_on") or r.get("deadline_on") or r.get("effective_on")
    r["days_left"] = days_until(r["due"])
    try:
        r["refs"] = json.loads(r.get("evidence_refs") or "[]")
    except (TypeError, ValueError):
        r["refs"] = []
    return r


def is_open(r: dict) -> bool:
    return r["verdict"] != "compliant" and r["workflow"] not in ("Implemented", "Verified", "Rejected")


def ticket_text(r: dict) -> str:
    """A ready-to-paste issue for Jira / any tracker. Built only from stored data."""
    return (
        f"[{r['priority']}] {r['regulator']} {r['clause_ref']} → {r['component_label']}: {r['verdict_label']}\n"
        f"Status: {r['workflow']}" + (" (DRAFT — not yet approved)" if r.get("approval_status") != "approved" else "")
        + f"\n\nRegulatory obligation: {r['obligation']}\n"
        f"Publication: #{r['pub_id']} {r['pub_title']} ({r.get('reference_no') or 'no ref'})\n"
        f"Affected component: {r['component_label']}\n"
        f"Why impacted: {r.get('reason') or '—'}\n"
        f"Existing internal requirement: {r.get('existing_requirement') or '— none —'}\n"
        f"Gap: {r.get('compliance_gap') or '—'}\n"
        f"Recommended action: {r.get('recommended_action') or '—'}\n"
        f"Proposed wording: {r.get('proposed_wording') or '—'}\n"
        f"Business area: {r.get('business_area') or '—'} · Suggested Azentio module: {r['module']}\n"
        f"Owner: {r['owner_label']} · Due: {r.get('due') or '—'} · Confidence: {float(r.get('confidence') or 0):.2f}\n"
        f"References: {'; '.join(r['refs'])}\n"
        f"Source: RegWatch mapping #{r['id']}\n")


def tickets_csv(rows: list[dict]) -> bytes:
    """Jira CSV-import friendly columns."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Summary", "Description", "Priority", "Labels", "Assignee", "Due Date", "Component"])
    for r in rows:
        w.writerow([f"{r['regulator']} {r['clause_ref']} → {r['component_label']}: {r['verdict_label']}"[:250],
                    ticket_text(r), r["priority"], f"regwatch {r['regulator'].lower()} {r['verdict']}",
                    r.get("owner_person") or "", r.get("due") or "", r["module"]])
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------- per-obligation verdicts (assessment rows)

def enrich(row: dict) -> dict:
    """Assessment (one verdict per obligation) rows: no owner is ever invented."""
    r = dict(row)
    r["owner"] = r.get("assigned_dept") or r.get("owner_dept") or "Unassigned"
    r["severity"] = {"gap": "High", "partial": "Medium", "insufficient_evidence": "Medium"}.get(r["status"], "Low")
    r["status_label"] = VERDICT_LABEL.get(r["status"], r["status"])
    r["module"] = module_for(r.get("obligation", ""), r.get("business_area"))
    r["due"] = r.get("deadline_on") or r.get("action_due") or r.get("effective_on")
    r["days_left"] = days_until(r["due"])
    return r
