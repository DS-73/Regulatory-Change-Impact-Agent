"""Normalise any document (PDF / DOCX / HTML / text) into text + a clause map.

Text carries [p.N] page markers so clause references can cite pages. Clauses are
split on numbered headings ("4.2", "Article 7", "Para 5") and keep the page they
start on; documents with no numbering fall back to paragraph chunks ("p.2 ¶3").
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil

from .models import Clause, IngestedDoc

# Arabic-Indic and Extended Arabic-Indic digits -> ASCII, so clause numbers and dates parse.
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
CLAUSE_RE = re.compile(
    rf"^\s*(?:(Article|Clause|Section|Paragraph|Para\.?|Rule|المادة|البند|الفقرة)\s+)?"
    rf"(\d{{1,2}}(?:\.\d{{1,2}}){{0,3}})(?:[.):]|\s)\s*(?!{MONTHS}\b)(\S.*)$",
    re.I,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(text: str) -> str:
    text = text.replace("\r", "\n").replace(" ", " ")
    text = text.translate(ARABIC_DIGITS)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- OCR (Tesseract) for image-only pages

MIN_PAGE_CHARS = 25
MAX_OCR_PAGES = 50
_TESS_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
               "/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"]


def ocr_status() -> tuple[bool, str]:
    """(available, detail). OCR needs pytesseract + PyMuPDF + the Tesseract binary."""
    try:
        import pymupdf  # noqa: F401  (renders PDF pages to images)
        import pytesseract
    except ImportError as e:
        return False, f"missing Python package: {e.name}"
    exe = shutil.which("tesseract") or next((p for p in _TESS_PATHS if os.path.exists(p)), None)
    if not exe:
        return False, "Tesseract binary not installed"
    pytesseract.pytesseract.tesseract_cmd = exe
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception:
        langs = {"eng"}
    lang = "+".join(x for x in ("ara", "eng") if x in langs) or "eng"
    return True, lang


def ocr_pdf_pages(data: bytes, indices: list[int]) -> dict[int, str]:
    ok, lang = ocr_status()
    if not ok:
        return {}
    import pymupdf as fitz
    import pytesseract
    from PIL import Image

    out = {}
    with fitz.open(stream=data, filetype="pdf") as doc:
        for i in indices[:MAX_OCR_PAGES]:
            pix = doc[i].get_pixmap(dpi=300)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out[i] = _clean(pytesseract.image_to_string(img, lang=lang))
    return out


def pages_from_pdf(data: bytes) -> list[str]:
    return pdf_with_quality(data)[0]


def pdf_with_quality(data: bytes) -> tuple[list[str], bool, str]:
    """Return (pages, ocr_used, extraction_quality)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [_clean(p.extract_text() or "") for p in reader.pages]
    image_pages = [i for i, t in enumerate(pages) if len(t.replace(" ", "")) < MIN_PAGE_CHARS]
    if not image_pages:
        return pages, False, "good"
    ocr = ocr_pdf_pages(data, image_pages)
    if ocr:
        for i, t in ocr.items():
            pages[i] = t
        still = [i for i in image_pages if len(pages[i].replace(" ", "")) < MIN_PAGE_CHARS]
        return pages, True, "ocr" if not still else "low"
    if len(image_pages) == len(pages):
        return pages, False, "image_only_no_ocr"
    return pages, False, "low"


def pages_from_docx(data: bytes) -> list[str]:
    import docx

    d = docx.Document(io.BytesIO(data))
    lines = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            lines.append(" | ".join(c.text for c in row.cells))
    return [_clean("\n".join(lines))]


def pages_from_html(data: bytes) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    main = soup.find(class_=re.compile("tablebg|content|main", re.I)) or soup.body or soup
    # Newlines only at block boundaries, so inline tags don't split sentences or dates.
    for br in main.find_all("br"):
        br.replace_with("\n")
    for blk in main.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"]):
        blk.append("\n")
    text = re.sub(r"\(\s*\d+\s*kb\s*\)", "", main.get_text(""), flags=re.I)
    text = "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n"))
    return [_clean(text)]


def pages_from_text(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    # Allow form-feed or explicit "[p.N]" markers in text samples to split pages.
    parts = re.split(r"\f|^\s*\[p\.\d+\]\s*$", text, flags=re.M)
    return [_clean(p) for p in parts if p.strip()] or [""]


def detect_kind(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return "pdf"
    if name.endswith(".docx") or data[:2] == b"PK":
        return "docx"
    if name.endswith((".html", ".htm", ".aspx")) or b"<html" in data[:2000].lower():
        return "html"
    return "text"


def segment_clauses(pages: list[str]) -> list[Clause]:
    clauses: list[Clause] = []
    current: Clause | None = None
    preamble: list[str] = []  # text before the first numbered clause (often holds the core instruction)
    for pno, page in enumerate(pages, start=1):
        for line in page.split("\n"):
            m = CLAUSE_RE.match(line)
            if m:
                label = (m.group(1) or "Clause").rstrip(".").title()
                if current:
                    clauses.append(current)
                current = Clause(ref=f"{label} {m.group(2)}", text=m.group(3).strip(), page=pno)
            elif current and line.strip():
                current.text += " " + line.strip()
            elif current is None and line.strip():
                preamble.append(line.strip())
    if current:
        clauses.append(current)
    pre_text = " ".join(preamble)
    if clauses and len(pre_text) > 200 and re.search(r"\b(shall|must|advised|required)\b", pre_text, re.I):
        clauses.insert(0, Clause(ref="Preamble", text=pre_text, page=1))

    if len(clauses) >= 2:
        # Merge duplicate refs (e.g. a heading line and its body) keeping order.
        merged: dict[str, Clause] = {}
        for c in clauses:
            if c.ref in merged:
                merged[c.ref].text += " " + c.text
            else:
                merged[c.ref] = c
        return list(merged.values())

    # Fallback: paragraph chunks with page anchors.
    out: list[Clause] = []
    for pno, page in enumerate(pages, start=1):
        paras = [p.strip() for p in re.split(r"\n\s*\n", page) if len(p.strip()) > 40]
        for k, p in enumerate(paras, start=1):
            out.append(Clause(ref=f"p.{pno} ¶{k}", text=re.sub(r"\s+", " ", p), page=pno))
    return out


def ingest_bytes(data: bytes, filename: str) -> IngestedDoc:
    kind = detect_kind(filename, data)
    ocr_used, quality = False, "good"
    if kind == "pdf":
        pages, ocr_used, quality = pdf_with_quality(data)
    else:
        pages = {"docx": pages_from_docx, "html": pages_from_html, "text": pages_from_text}[kind](data)
    if not any(p.strip() for p in pages):
        quality = "image_only_no_ocr" if kind == "pdf" and not ocr_used else "empty"
    text = "\n\n".join(f"[p.{i}]\n{p}" for i, p in enumerate(pages, start=1))
    return IngestedDoc(text=text, clauses=segment_clauses(pages), pages=len(pages),
                       ocr_used=ocr_used, quality=quality)
