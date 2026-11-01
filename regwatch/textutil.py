"""Small text helpers: date parsing, tokenising and TF-IDF cosine similarity."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

DATE_PATTERNS = [
    # 1 March 2026 / 01 Mar, 2026 / 1st March 2026
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"),
    # March 1, 2026
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"),
    # 2026-03-01
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    # 01/03/2026 (day first, as used by all four regulators)
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[int, date]]:
    """Return (position, date) for every recognisable date in text, in order."""
    found: list[tuple[int, date]] = []
    for i, pat in enumerate(DATE_PATTERNS):
        for m in pat.finditer(text or ""):
            g = m.groups()
            d = None
            if i == 0 and g[1][:3].lower() in _MONTHS:
                d = _mk(int(g[2]), _MONTHS[g[1][:3].lower()], int(g[0]))
            elif i == 1 and g[0][:3].lower() in _MONTHS:
                d = _mk(int(g[2]), _MONTHS[g[0][:3].lower()], int(g[1]))
            elif i == 2:
                d = _mk(int(g[0]), int(g[1]), int(g[2]))
            elif i == 3:
                d = _mk(int(g[2]), int(g[1]), int(g[0]))
            if d and 1990 <= d.year <= 2100:
                found.append((m.start(), d))
    found.sort(key=lambda x: x[0])
    return found


def first_date(text: str) -> date | None:
    ds = find_dates(text)
    return ds[0][1] if ds else None


def parse_date(value) -> str | None:
    """Coerce a date-like value to ISO yyyy-mm-dd, or None."""
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    d = first_date(s)
    return d.isoformat() if d else None


STOP = set("""a an the of and or to in on for by with as at from be is are was were shall must may will this that
these those such any all its it their which who not no under per within into than other each also has have
been being including include where when if upon""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z][a-z0-9\-]{2,}|[ء-ي]{3,}", (text or "").lower()) if t not in STOP]


def tfidf_cosine(query: str, docs: list[str]) -> list[float]:
    """Cosine similarity of query to each doc using TF-IDF fit on query+docs."""
    corpus = [tokens(query)] + [tokens(d) for d in docs]
    n = len(corpus)
    df = Counter()
    for toks in corpus:
        df.update(set(toks))
    idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

    def vec(toks):
        tf = Counter(toks)
        v = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    q = vec(corpus[0])
    out = []
    for toks in corpus[1:]:
        d = vec(toks)
        out.append(sum(q[t] * d.get(t, 0.0) for t in q))
    return out


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
