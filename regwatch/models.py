"""Shared vocabularies and small dataclasses."""
from dataclasses import dataclass, field

JURISDICTIONS = ["KSA", "UAE", "India", "Nigeria"]
DOC_TYPES = ["circular", "amendment", "guideline", "consultation", "notification", "other"]
BUSINESS_AREAS = ["AI", "banking", "lending", "insurance", "payments", "AML", "other"]
OBLIGATION_TYPES = ["mandatory", "prohibition", "clarification", "definition"]
CHANGE_CLASSES = [
    "new_obligation",
    "modified",
    "withdrawn",
    "deadline_changed",
    "clarification",
    "no_material_change",
    "no_reliable_match",
]
REVIEW_STATES = ["pending", "approved", "rejected", "needs_info"]

CHANGE_LABELS = {
    "new_obligation": "New obligation",
    "modified": "Modified",
    "withdrawn": "Withdrawn / superseded",
    "deadline_changed": "Deadline changed",
    "clarification": "Clarification",
    "no_material_change": "No material change",
    "no_reliable_match": "No reliable match",
}


@dataclass
class Clause:
    ref: str          # 'Clause 4.2' or 'p.3'
    text: str
    page: int | None = None


@dataclass
class IngestedDoc:
    text: str                      # full text with [p.N] markers
    clauses: list[Clause] = field(default_factory=list)
    pages: int = 1
    ocr_used: bool = False
    quality: str = "good"          # good | ocr | low | image_only_no_ocr | empty
