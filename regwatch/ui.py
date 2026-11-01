"""Design system + HTML components (minimal, enterprise BFSI look).

Tokens: deep canvas #0B0F19, trust navy #0F172A, one brand accent (cyan #00B4D8 / #0077B6).
Risk colours are reserved for status only: red #EF4444, amber #F59E0B, emerald #10B981, indigo #6366F1.
Every value that comes from a document, the model or a user goes through esc() before entering HTML.
"""
from __future__ import annotations

import html
from datetime import date, datetime

from .models import CHANGE_LABELS

esc = lambda v: html.escape("" if v is None else str(v), quote=True)  # noqa: E731

ACCENT, ACCENT_2 = "#00B4D8", "#0077B6"
RED, AMBER, GREEN, INDIGO, GREY = "#EF4444", "#F59E0B", "#10B981", "#6366F1", "#8B95A9"

CHANGE_COLORS = {"new_obligation": GREEN, "modified": AMBER, "withdrawn": RED, "deadline_changed": INDIGO,
                 "clarification": ACCENT, "no_material_change": GREY, "no_reliable_match": AMBER}
REVIEW_COLORS = {"pending": AMBER, "approved": GREEN, "rejected": RED, "needs_info": INDIGO}
SEVERITY_COLORS = {"High": RED, "Medium": AMBER, "Moderate": AMBER, "Low": GREEN}
STATUS_COLORS = {"Draft · awaiting approval": AMBER, "Approved · not started": INDIGO, "In progress": ACCENT,
                 "Implemented": GREEN, "Verified": GREEN, "Rejected": GREY, "Compliant": GREEN}
ASSESS_COLORS = {"compliant": GREEN, "partial": AMBER, "gap": RED, "insufficient_evidence": INDIGO}
ASSESS_LABEL = {"compliant": "Addressed", "partial": "Partially addressed", "gap": "No internal control",
                "insufficient_evidence": "Insufficient evidence"}
FLAGS = {"KSA": "🇸🇦", "UAE": "🇦🇪", "India": "🇮🇳", "Nigeria": "🇳🇬"}
BILINGUAL = {"SAMA", "CBUAE"}

ICONS = {
    "doc": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    "spark": '<path d="M12 3l1.9 5.8L20 10l-6.1 1.2L12 17l-1.9-5.8L4 10l6.1-1.2z"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "radar": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
}


def icon(name: str, size: int = 16) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Color+Emoji&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');
:root{
  --bg:#0B0F19; --navy:#0F172A; --text:#E5E9F2; --muted:#8B95A9; --faint:#5B657A;
  --surface:rgba(255,255,255,.035); --surface-2:rgba(255,255,255,.06);
  --line:rgba(226,232,240,.09); --line-2:rgba(226,232,240,.16);
  --accent:#00B4D8; --accent-2:#0077B6; --radius:14px; --blur:blur(12px);
}
html, body, .stApp, button, input, textarea, select, [class*="css"]{font-family:'Inter',system-ui,sans-serif !important;}
.stApp{background:
   radial-gradient(900px 500px at 0% -10%, rgba(0,180,216,.07), transparent 60%),
   radial-gradient(800px 500px at 100% 0%, rgba(99,102,241,.06), transparent 60%), var(--bg) !important;
   background-attachment:fixed !important; color:var(--text);}
[data-testid="stHeader"]{background:transparent !important; height:0 !important;}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"]{display:none !important;}
.block-container{padding:1.1rem 2rem 6rem !important; max-width:1360px;}
h1,h2,h3,h4{letter-spacing:-.015em;color:var(--text);}
p, li{color:var(--text);}

/* inputs: quiet, single-line */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"], .stDateInput > div > div{
  background:var(--surface) !important; border:1px solid var(--line) !important; border-radius:10px !important; min-height:38px;}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea{background:transparent !important;}
[data-baseweb="tag"]{background:rgba(0,180,216,.16) !important; border-radius:6px !important;}
[data-baseweb="popover"] ul{background:#111827 !important;}
label[data-testid="stWidgetLabel"] p{color:var(--muted) !important; font-size:.78rem !important;}

/* buttons */
.stButton button, [data-testid="stFormSubmitButton"] button, .stDownloadButton button, .stLinkButton a,
[data-testid="stPopover"] > div > button{
  background:var(--surface) !important; border:1px solid var(--line-2) !important; border-radius:10px !important;
  color:var(--text) !important; font-weight:500 !important; min-height:36px; transition:border-color .15s, background .15s;}
.stButton button:hover, [data-testid="stFormSubmitButton"] button:hover, .stDownloadButton button:hover,
.stLinkButton a:hover, [data-testid="stPopover"] > div > button:hover{border-color:var(--accent) !important; background:rgba(0,180,216,.08) !important;}
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]{
  background:var(--accent-2) !important; border-color:var(--accent-2) !important; color:#fff !important;}
.stButton button[kind="primary"]:hover{background:var(--accent) !important;}
.stButton button[kind="tertiary"]{background:transparent !important; border:none !important; color:var(--accent) !important; padding:0 4px !important; min-height:0;}

/* segmented controls / pills = primary navigation + filters */
[data-testid="stButtonGroup"] button{background:transparent !important; border:1px solid var(--line) !important;
  color:var(--muted) !important; font-weight:500 !important;}
[data-testid="stButtonGroup"] button:hover{color:var(--text) !important; border-color:var(--line-2) !important;}
[data-testid="stButtonGroup"] button[aria-checked="true"], [data-testid="stButtonGroup"] button[kind*="Active"]{
  background:rgba(0,180,216,.14) !important; border-color:rgba(0,180,216,.55) !important; color:#fff !important;}
.st-key-navbar [data-testid="stButtonGroup"] button{border:none !important; padding:6px 16px !important; font-size:.92rem !important;}
.st-key-navbar [data-testid="stButtonGroup"]{gap:2px;}
.st-key-navbar{border-bottom:1px solid var(--line); padding-bottom:6px; margin-bottom:10px;}

/* surfaces */
[data-testid="stExpander"] details, [data-testid="stForm"], [class*="st-key-card"]{
  background:var(--surface) !important; border:1px solid var(--line) !important; border-radius:var(--radius) !important;
  backdrop-filter:var(--blur); -webkit-backdrop-filter:var(--blur);}
[class*="st-key-card"]{padding:14px 16px !important;}
[data-testid="stForm"]{padding:14px 16px !important;}
[data-testid="stDataFrame"]{border:1px solid var(--line); border-radius:12px; overflow:hidden;}
[data-testid="stAlert"]{background:var(--surface) !important; border:1px solid var(--line) !important; border-radius:12px !important;}
[data-testid="stFileUploaderDropzone"]{background:rgba(0,180,216,.04) !important; border:1.5px dashed rgba(0,180,216,.4) !important; border-radius:14px !important; padding:26px !important;}
[data-testid="stPopoverBody"], [data-testid="stDialog"] > div > div{background:#0F1524 !important; border:1px solid var(--line-2) !important; border-radius:14px !important;}
hr{border-color:var(--line) !important; margin:.6rem 0 !important;}

/* floating assistant button */
.st-key-fab{position:fixed; right:28px; bottom:26px; z-index:1000; width:auto !important;}
.st-key-fab button{border-radius:999px !important; padding:10px 18px !important; background:var(--accent-2) !important;
  border:1px solid rgba(255,255,255,.18) !important; color:#fff !important; font-weight:600 !important;
  box-shadow:0 10px 30px rgba(0,119,182,.45) !important;}
.st-key-fab button:hover{background:var(--accent) !important;}

/* ---------- custom components ---------- */
.card{background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:16px 18px;
  backdrop-filter:var(--blur); -webkit-backdrop-filter:var(--blur);}
.brand{display:flex; align-items:center; gap:10px; white-space:nowrap;}
.brand .mark{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent-2));}
.brand b{font-size:1.05rem; font-weight:700; letter-spacing:-.01em;}
.brand span{color:var(--muted); font-size:.85rem; border-left:1px solid var(--line-2); padding-left:10px;}
.chip{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;font-size:.72rem;font-weight:600;
  border:1px solid;white-space:nowrap;margin:0 6px 0 0;line-height:1.6;}
.reg, .reg-card .name, .bars .r span{font-family:'Inter','Noto Color Emoji',sans-serif;}
.reg{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:.82rem;white-space:nowrap;}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.76rem;}
.ref{padding:1px 7px;border-radius:6px;background:rgba(0,180,216,.10);border:1px solid rgba(0,180,216,.28);color:#9BE7F5;}
.muted{color:var(--muted);} .small{font-size:.8rem;} .faint{color:var(--faint);}
.section{display:flex;align-items:baseline;gap:10px;margin:20px 0 10px;}
.section h3{margin:0;font-size:1rem;font-weight:600;} .section span{color:var(--muted);font-size:.82rem;}

.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;}
@media (max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr));}}
.kpi .lbl{color:var(--muted);font-size:.8rem;font-weight:500;display:flex;align-items:center;gap:7px;}
.kpi .val{font-size:1.9rem;font-weight:700;letter-spacing:-.03em;margin-top:6px;line-height:1.1;}
.kpi .sub{font-size:.76rem;color:var(--muted);margin-top:4px;}
.kpi .bar{height:3px;border-radius:3px;margin-top:12px;opacity:.85;}

.row{display:flex;align-items:center;gap:10px;padding:10px 2px;border-bottom:1px solid var(--line);font-size:.86rem;}
.row:last-child{border-bottom:none;}
.row .grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.bars .r{display:grid;grid-template-columns:110px 1fr 28px;gap:10px;align-items:center;margin:8px 0;font-size:.84rem;}
.bars .t{height:6px;border-radius:6px;background:var(--surface-2);overflow:hidden;} .bars .f{height:100%;border-radius:6px;}
.stack{display:flex;height:8px;border-radius:8px;overflow:hidden;background:var(--surface-2);margin:10px 0 12px;}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.78rem;color:var(--muted);}
.legend i{width:8px;height:8px;border-radius:2px;display:inline-block;margin-right:5px;}

.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:0 0 auto;}
.pulse{animation:pulse 1.8s ease-out infinite;}
@keyframes pulse{0%{box-shadow:0 0 0 0 currentColor;}70%{box-shadow:0 0 0 7px transparent;}100%{box-shadow:0 0 0 0 transparent;}}

.reg-card .top{display:flex;justify-content:space-between;align-items:center;}
.reg-card .name{font-size:1rem;font-weight:700;} .reg-card .full{color:var(--muted);font-size:.8rem;margin-top:2px;}
.reg-card .meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;}
.reg-card .msg{color:var(--muted);font-size:.76rem;margin-top:10px;min-height:34px;line-height:1.4;}

.redline{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0;}
@media (max-width:800px){.redline{grid-template-columns:1fr;}}
.pane{border-radius:10px;padding:10px 12px;background:rgba(15,23,42,.55);border:1px solid var(--line);font-size:.88rem;line-height:1.6;}
.pane .lbl{display:block;font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);font-weight:600;margin-bottom:4px;}
.pane del{background:rgba(239,68,68,.16);color:#FECACA;text-decoration:line-through;border-radius:3px;padding:0 2px;}
.pane ins{background:rgba(16,185,129,.16);color:#BBF7D0;text-decoration:none;border-radius:3px;padding:0 2px;}
.callout{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 12px;border-radius:10px;
  background:rgba(0,180,216,.06);border:1px solid rgba(0,180,216,.22);font-size:.84rem;}
.callout .txt{flex-basis:100%;color:var(--text);line-height:1.5;}
.head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.title{font-weight:600;font-size:.95rem;margin:2px 0 0;}

.obl{padding:12px 0;border-bottom:1px solid var(--line);}
.obl:last-child{border-bottom:none;}
.obl .txt{font-size:.9rem;line-height:1.55;margin:6px 0;}
.obl .assess{font-size:.82rem;color:var(--muted);}
.obl details summary{cursor:pointer;color:var(--accent);font-size:.78rem;margin-top:6px;}
.obl blockquote{margin:6px 0 0;padding:8px 10px;border-left:2px solid var(--accent);background:rgba(0,180,216,.05);font-size:.84rem;color:#CBD5E1;}
.warn{display:flex;gap:8px;padding:10px 12px;border-radius:10px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.3);font-size:.86rem;}
.ctl-row{display:grid;grid-template-columns:1.6fr .8fr .6fr 1.4fr;gap:10px;align-items:center;padding:10px 4px;border-bottom:1px solid var(--line);font-size:.84rem;}
.ctl-row.h{color:var(--faint);font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;font-weight:600;}
.tour-step{font-size:.72rem;color:var(--accent);font-weight:600;letter-spacing:.08em;text-transform:uppercase;}
.tour-progress{display:flex;gap:4px;margin:6px 0 12px;} .tour-progress i{flex:1;height:3px;border-radius:3px;background:var(--surface-2);}
.tour-progress i.on{background:var(--accent);}
[dir="auto"]{unicode-bidi:plaintext;}
</style>
"""


def css() -> str:
    return CSS


# ---------------------------------------------------------------- atoms

def chip(text: str, color: str) -> str:
    return f'<span class="chip" style="color:{color};border-color:{color}55;background:{color}14">{esc(text)}</span>'


def dot(color: str, pulse: bool = False) -> str:
    return f'<span class="dot{" pulse" if pulse else ""}" style="background:{color};color:{color}"></span>'


def reg(code: str, juris: str) -> str:
    return f'<span class="reg">{FLAGS.get(juris, "🏳️")} {esc(code)}</span>'


def change_chip(cls: str) -> str:
    return chip(CHANGE_LABELS.get(cls, cls), CHANGE_COLORS.get(cls, GREY))


def review_chip(state: str) -> str:
    return chip(state.replace("_", " ").title(), REVIEW_COLORS.get(state, GREY))


def severity_chip(s: str) -> str:
    return chip(s, SEVERITY_COLORS.get(s, GREY))


def status_chip(s: str) -> str:
    return chip(s, STATUS_COLORS.get(s, GREY))


def ref(text: str) -> str:
    return f'<span class="ref mono">{esc(text)}</span>'


def section(title: str, sub: str = "") -> str:
    return f'<div class="section"><h3>{esc(title)}</h3><span>{esc(sub)}</span></div>'


def brand() -> str:
    return (f'<div class="brand"><span class="mark">{icon("shield", 16)}</span><b>RegWatch</b>'
            f'<span>Regulatory Intelligence</span></div>')


def rel_time(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        delta = datetime.now() - datetime.fromisoformat(str(iso).replace(" ", "T")[:19])
    except ValueError:
        return str(iso)[:16]
    s = int(delta.total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def due_chip(deadline: str | None, label: str = "Due") -> str:
    if not deadline:
        return ""
    try:
        d = date.fromisoformat(str(deadline)[:10])
    except ValueError:
        return ""
    n = (d - date.today()).days
    when = f"{d:%d %b %Y}"
    if n < 0:
        return chip(f"{label} passed · {when}", GREY)
    color = RED if n <= 30 else (AMBER if n <= 90 else GREEN)
    return chip(f"⏳ {label} in {n} days · {when}", color)


# ---------------------------------------------------------------- blocks

def kpis(items: list[dict]) -> str:
    cells = "".join(
        f'<div class="card kpi"><div class="lbl"><span style="color:{k["color"]}">{icon(k["icon"], 15)}</span>{esc(k["label"])}</div>'
        f'<div class="val">{esc(k["value"])}</div><div class="sub">{esc(k.get("sub", ""))}</div>'
        f'<div class="bar" style="background:linear-gradient(90deg,{k["color"]},transparent)"></div></div>' for k in items)
    return f'<div class="kpis">{cells}</div>'


def bar_list(rows: list[tuple[str, int, str]]) -> str:
    mx = max([v for _, v, _ in rows] or [1]) or 1
    body = "".join(f'<div class="r"><span>{esc(l)}</span><div class="t"><div class="f" style="width:{v / mx * 100:.0f}%;'
                   f'background:{c}"></div></div><b style="text-align:right">{v}</b></div>' for l, v, c in rows)
    return f'<div class="bars">{body or "<span class=muted>No data</span>"}</div>'


def stack(rows: list[tuple[str, int, str]]) -> str:
    total = sum(v for _, v, _ in rows) or 1
    segs = "".join(f'<div title="{esc(l)}: {v}" style="width:{v / total * 100:.2f}%;background:{c}"></div>' for l, v, c in rows if v)
    leg = "".join(f'<span><i style="background:{c}"></i>{esc(l)} {v}</span>' for l, v, c in rows if v)
    return f'<div class="stack">{segs}</div><div class="legend">{leg}</div>'


def rows(items: list[str]) -> str:
    return "".join(f'<div class="row">{i}</div>' for i in items) or '<div class="muted small" style="padding:8px 0">Nothing here.</div>'


def warn(text: str) -> str:
    return f'<div class="warn"><span style="color:{AMBER}">{icon("alert", 15)}</span><div>{esc(text)}</div></div>'


def redline(old_html: str, new_html: str, old_label: str = "Previous", new_label: str = "Amended") -> str:
    return (f'<div class="redline"><div class="pane" dir="auto"><span class="lbl">{esc(old_label)}</span>{old_html}</div>'
            f'<div class="pane" dir="auto"><span class="lbl">{esc(new_label)}</span>{new_html}</div></div>')


def change_callout(summary: str, obl_type: str | None, deadline: str | None, grounding: float | None) -> str:
    kind = ""
    if obl_type:
        kind = chip("Mandatory directive" if obl_type in ("mandatory", "prohibition") else "Guidance / advisory",
                    INDIGO if obl_type in ("mandatory", "prohibition") else GREY)
    g = chip(f"{grounding * 100:.0f}% clause grounding", GREEN if grounding >= 0.6 else AMBER) if grounding is not None else ""
    return (f'<div class="callout">{kind}{due_chip(deadline)}{g}'
            f'<div class="txt">{esc(summary)}</div></div>')


def regulator_card(code: str, name: str, juris: str, method: str, every: float, last_run: str | None,
                   health: str, message: str) -> str:
    color = {"live": GREEN, "fallback": AMBER, "down": RED}.get(health, GREY)
    label = {"live": "Live", "fallback": "Fallback to seeded copies", "down": "Down", "manual": "Manual upload"}.get(health, "Ready to poll")
    sync = f"Synced {rel_time(last_run)}" if last_run else "Not polled yet"
    lang = chip("AR / EN", INDIGO) if code in BILINGUAL else chip("EN", GREY)
    return (f'<div class="card reg-card"><div class="top"><span class="name">{FLAGS.get(juris, "")} {esc(code)}</span>'
            f'<span class="small" style="color:{color};display:flex;align-items:center;gap:6px">{dot(color, health == "live")}{esc(label)}</span></div>'
            f'<div class="full">{esc(name)}</div>'
            f'<div class="meta">{chip(("Live scraping" if method == "scrape" else "Manual") + (f" · every {every:g}h" if every else " upload only"), ACCENT)}{lang}'
            f'{chip(sync, GREY)}</div><div class="msg">{esc((message or "")[:150])}</div></div>')


def obligation(o: dict, source_clause: str | None) -> str:
    a = ""
    if o.get("status"):
        c = ASSESS_COLORS.get(o["status"], GREY)
        ctl = f' vs {ref(o["control_code"])}' if o.get("control_code") else ""
        rec = f' · {esc(o["recommended_change"])}' if o.get("recommended_change") else ""
        a = (f'<div class="assess">{dot(c)} <b style="color:{c}">{esc(ASSESS_LABEL.get(o["status"], o["status"]))}</b>'
             f'{ctl}{rec}</div>')
    weak = chip("weakly grounded", AMBER) if (o.get("grounding") is not None and o["grounding"] < 0.6) else ""
    src = (f'<details><summary>Source clause</summary><blockquote dir="auto">{esc(source_clause)}</blockquote></details>'
           if source_clause else "")
    return (f'<div class="obl"><div class="head">{ref(o["clause_ref"])}'
            f'{chip(o["obligation_type"], INDIGO if o["obligation_type"] in ("mandatory", "prohibition") else GREY)}'
            f'{due_chip(o.get("deadline_on"))}{weak}</div>'
            f'<div class="txt" dir="auto">{esc(o["text"])}</div>{a}{src}</div>')


def verdict_chip(v: str) -> str:
    return chip(ASSESS_LABEL.get(v, v), ASSESS_COLORS.get(v, GREY))


def mapping_detail(r: dict) -> str:
    """One row of the Level 2 impact matrix, fully explained."""
    existing = (esc(r["existing_requirement"]) if r.get("existing_requirement")
                else '<span class="faint">No internal requirement found</span>')
    approved = r.get("approval_status") == "approved"
    wording = ""
    if r.get("proposed_wording"):
        badge = chip("Approved by " + (r.get("approved_by") or ""), GREEN) if approved else chip("DRAFT · not approved", AMBER)
        wording = (f'<div class="pane" dir="auto" style="margin-top:10px"><span class="lbl">Proposed wording</span>'
                   f'{badge}<div style="margin-top:6px">{esc(r["proposed_wording"])}</div></div>')
    owner = (chip(r["owner_label"], INDIGO) if not r.get("needs_owner")
             else chip("⚠ No owner in the ownership register · assign manually", RED))
    refs = " · ".join(esc(x) for x in r.get("refs") or [])
    conf = f"Confidence {float(r.get('confidence') or 0):.2f} · {r.get('mode') or '-'}"
    return (
        f'<div class="head">{severity_chip(r["priority"])}{verdict_chip(r["verdict"])}{status_chip(r["workflow"])}'
        f'{reg(r["regulator"], r["jurisdiction"])}{ref(r["clause_ref"])}'
        f'<span class="muted small">#{esc(r["pub_id"])} · {esc(r["pub_title"])}</span></div>'
        + redline(esc(r["obligation"]), existing, f"Regulatory obligation · {r['clause_ref']}",
                  f"Existing internal requirement · {r['component_label']}")
        + f'<div class="callout" style="display:block"><div class="txt"><b>Why impacted · </b>{esc(r.get("reason") or "—")}</div>'
          f'<div class="txt" style="margin-top:6px"><b>Gap · </b>{esc(r.get("compliance_gap") or "—")}</div>'
          f'<div class="txt" style="margin-top:6px"><b>Recommended action · </b>{esc(r.get("recommended_action") or "—")}</div></div>'
        + wording
        + f'<div class="head" style="margin-top:10px">{owner}{due_chip(r.get("due"))}'
          f'{chip("Area: " + (r.get("business_area") or "—"), GREY)}{chip("Azentio module (suggested): " + r["module"], ACCENT)}'
          f'{chip(conf, GREY)}</div>'
          f'<div class="faint small" style="margin-top:6px">References: {refs}</div>')


def control_rows(items: list[dict]) -> str:
    head = '<div class="ctl-row h"><span>Control</span><span>OWASP LLM</span><span>Status</span><span>Measured</span></div>'
    body = "".join(
        f'<div class="ctl-row"><span><b>{esc(c["control"])}</b><br><span class="muted small">{esc(c["detail"])}</span></span>'
        f'<span class="mono">{esc(c.get("owasp", "—"))}</span>'
        f'<span>{chip(c["status"], GREEN if c["status"] == "on" else (GREY if c["status"] == "n/a" else AMBER))}</span>'
        f'<span class="small">{esc(c.get("metric", ""))}</span></div>' for c in items)
    return head + body
