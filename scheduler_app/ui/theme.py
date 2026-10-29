"""Visual language: a calm, printed-program look (serif headings, tabular numbers,
hairline rules) with a UND-inspired green accent. Status is always shown with a
shape/word as well as color."""
from __future__ import annotations

from html import escape

import streamlit as st

CSS = """
<style>
:root { --ink:#1B2420; --muted:#5B6B63; --rule:#DEDBD1; --paper:#F8F7F3; --green:#0A5C41; --green-2:#E7F2EC;
        --amber:#8A5A00; --amber-bg:#FFF6E0; --red:#B42318; --red-bg:#FDECEA; }
.block-container { padding-top: 2.2rem; max-width: 1240px; }
h1, h2, h3 { letter-spacing: -0.01em; }
[data-testid="stSidebar"] .sb-brand { padding: 4px 4px 14px; border-bottom: 1px solid #2A4A3B; margin-bottom: 8px; }
.sb-brand .o { font-size: 10.5px; letter-spacing: .14em; text-transform: uppercase; color: #9FD3B6; font-weight: 700; }
[data-testid="stSidebarHeader"] { height: auto; min-height: 3.75rem; padding-top: 1rem; }
[data-testid="stSidebarLogo"] { height: 2.9rem !important; max-width: 13.5rem; object-fit: contain; object-position: left center; }
.sb-brand .u { font-family: "Source Serif 4", Georgia, serif; font-size: 17px; color: #F2F6F3; line-height: 1.25; margin-top: 2px; }
.eyebrow { font-size: 11.5px; letter-spacing: .14em; text-transform: uppercase; color: var(--green); font-weight: 700; margin-bottom: 2px; }
.ph-title { font-family: "Source Serif 4", Georgia, serif; font-size: 2.05rem; font-weight: 600; line-height: 1.15; margin: 0 0 4px; color: var(--ink); }
.ph-sub { color: var(--muted); font-size: 0.98rem; margin: 0 0 18px; max-width: 70ch; }
.ph-rule { border: 0; border-top: 1px solid var(--rule); margin: 6px 0 22px; }
.demo-ribbon { background: #FFF6E0; border: 1px solid #F1D68B; color: #5A4300; padding: 7px 12px; border-radius: 6px;
               font-size: 13px; margin-bottom: 14px; }
.demo-ribbon b { letter-spacing: .08em; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); border-top: 2px solid var(--ink);
         border-bottom: 1px solid var(--rule); margin: 4px 0 22px; }
.stat { padding: 14px 16px 12px 0; border-right: 1px solid var(--rule); margin-right: 16px; }
.stat:last-child { border-right: 0; }
.stat .v { font-family: "Source Serif 4", Georgia, serif; font-size: 2.1rem; font-weight: 600; line-height: 1; font-variant-numeric: tabular-nums lining-nums; }
.stat .v small { font-size: 1rem; color: var(--muted); font-weight: 500; margin-left: 4px; }
.stat .l { font-size: 12px; color: var(--muted); margin-top: 6px; text-transform: uppercase; letter-spacing: .07em; font-weight: 600; }
.bar { height: 4px; background: #E7E4DA; border-radius: 2px; margin-top: 8px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--green); }
.att { list-style: none; padding: 0; margin: 0 0 8px; border-top: 1px solid var(--rule); }
.att li { display: grid; grid-template-columns: 28px 1fr auto; gap: 10px; padding: 11px 2px; border-bottom: 1px solid var(--rule); align-items: start; }
.att .g { font-size: 15px; line-height: 1.3; text-align: center; font-weight: 700; }
.att .g.error { color: var(--red); } .att .g.warning { color: var(--amber); } .att .g.ok { color: var(--green); } .att .g.info { color: #1F5F8B; }
.att .m { line-height: 1.4; } .att .m .h { font-weight: 600; } .att .m .d { color: var(--muted); font-size: 13px; }
.att .k { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); padding-top: 3px; white-space: nowrap; }
.chip { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 2px 10px 2px 8px; font-size: 12px; font-weight: 600;
        border: 1px solid; white-space: nowrap; letter-spacing: .02em; }
.chip.draft { color: #4A5550; border-color: #C9CDC8; background: #F3F4F2; }
.chip.review { color: #1F4E79; border-color: #B6CDE2; background: #EEF4FA; }
.chip.published { color: #0A5C41; border-color: #A9D3BD; background: #E9F5EE; }
.chip.changed { color: #8A5A00; border-color: #EBCB7C; background: #FFF6E0; }
.chip.error { color: var(--red); border-color: #F1B9B3; background: var(--red-bg); }
.chip.warning { color: var(--amber); border-color: #EBCB7C; background: var(--amber-bg); }
.chip.ok { color: #0A5C41; border-color: #A9D3BD; background: #E9F5EE; }
.ccard { border: 1px solid var(--rule); background: #fff; border-radius: 8px; padding: 16px 18px; height: 100%; }
.ccard .n { font-family: "Source Serif 4", Georgia, serif; font-size: 1.18rem; font-weight: 600; margin: 4px 0 2px; }
.ccard .dt { color: var(--muted); font-size: 13.5px; }
.ccard .row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-top: 1px dashed #E7E4DA; }
.ccard .row:first-of-type { margin-top: 10px; }
.ccard .row span:last-child { font-variant-numeric: tabular-nums; font-weight: 600; }
.itin { width: 100%; border-collapse: collapse; font-size: 14.5px; margin: 6px 0 22px; }
.itin th { text-align: left; font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: #fff; background: var(--green); padding: 8px 10px; font-weight: 700; }
.itin td { padding: 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }
.itin td.t { white-space: nowrap; font-weight: 600; font-variant-numeric: tabular-nums; width: 150px; }
.itin tr.gap td { color: var(--muted); background: #F6F5F0; font-style: italic; padding: 6px 10px; }
.itin tr.ev td:nth-child(2) { font-weight: 600; }
.itin .note { color: var(--muted); font-size: 12.5px; }
.dayh { font-family: "Source Serif 4", Georgia, serif; font-size: 1.25rem; font-weight: 600; color: var(--green); margin: 18px 0 2px; }
.paper { background: #fff; border: 1px solid var(--rule); border-radius: 10px; padding: 34px 40px; }
.paper .org { font-size: 11px; letter-spacing: .16em; text-transform: uppercase; color: var(--green); font-weight: 700; }
.paper .unit { color: var(--muted); font-size: 13px; }
.paper .rule { height: 3px; background: var(--green); margin: 12px 0 18px; }
.paper h1 { font-family: "Source Serif 4", Georgia, serif; font-size: 2rem; margin: 0; font-weight: 600; }
.small-muted { color: var(--muted); font-size: 13px; }
.kbd { font-family: ui-monospace, monospace; font-size: 12px; background: #EFEDE6; border: 1px solid var(--rule); border-radius: 4px; padding: 1px 5px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--muted); margin: 4px 0 10px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 16px; height: 12px; border-radius: 3px; display: inline-block; }
.steps { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 0 10px; list-style: none;
         padding: 0; margin: 2px 0 10px; }
@media (max-width: 900px) { .steps { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px 10px; } }
.steps li { padding: 9px 4px 6px 0; font-size: 12.5px; line-height: 1.3; color: var(--muted); border-top: 3px solid #E2DFD5; }
.steps li .n { display: block; font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase; font-weight: 700; margin-bottom: 3px; }
.steps li.done { border-top-color: var(--green); color: var(--ink); }
.steps li.done .n { color: var(--green); }
.steps li.next { border-top-color: #C98A00; color: var(--ink); font-weight: 600; }
.steps li.next .n { color: var(--amber); }
.nextstep { background: #fff; border: 1px solid var(--rule); border-left: 4px solid #C98A00; border-radius: 6px;
            padding: 10px 14px; margin: 4px 0 6px; font-size: 14px; }
.nextstep b { font-weight: 650; }
.faculty-hero { border-left: 4px solid var(--green); padding: 4px 0 4px 16px; margin-bottom: 18px; }
@media print {
  [data-testid="stSidebar"], header, [data-testid="stToolbar"], .no-print, [data-testid="stDownloadButton"], .stButton { display: none !important; }
  .block-container { padding: 0 !important; }
  .paper { border: 0; padding: 0; }
}
</style>
"""

STATUS_LABEL = {
    "draft": ("○", "Draft"),
    "review": ("◐", "Ready for review"),
    "published": ("●", "Published"),
}


def inject() -> None:
    st.html(CSS)


def page_header(eyebrow: str, title: str, subtitle: str = "") -> None:
    st.html(
        f"<div class='eyebrow'>{escape(eyebrow)}</div><div class='ph-title' role='heading' aria-level='1'>{escape(title)}</div>"
        + (f"<p class='ph-sub'>{subtitle}</p>" if subtitle else "")
    )


def demo_ribbon(is_demo: bool) -> None:
    if is_demo:
        st.html("<div class='demo-ribbon' role='note'><b>DEMO DATA</b> — every name, room, email and date in this search "
                "is fictional. Create a new search in <i>Settings</i> to enter real information.</div>")


def chip(kind: str, text: str, glyph: str = "") -> str:
    return f"<span class='chip {escape(kind)}'>{escape(glyph)} {escape(text)}</span>"


def status_chip(status: str, changed: bool = False) -> str:
    g, t = STATUS_LABEL.get(status, ("○", status))
    html = chip(status, t, g)
    if changed and status == "published":
        html += " " + chip("changed", "Unpublished changes", "△")
    return html


def stats_row(items: list[tuple[str, str, str | None, float | None]]) -> None:
    """items: (value, label, small suffix, progress 0..1)"""
    cells = []
    for v, label, small, prog in items:
        bar = f"<div class='bar' aria-hidden='true'><i style='width:{int((prog or 0) * 100)}%'></i></div>" if prog is not None else ""
        sm = f"<small>{escape(small)}</small>" if small else ""
        cells.append(f"<div class='stat'><div class='v'>{escape(v)}{sm}</div><div class='l'>{escape(label)}</div>{bar}</div>")
    st.html(f"<div class='stats'>{''.join(cells)}</div>")


GLYPH = {"error": "✕", "warning": "▲", "ok": "✓", "info": "i"}


def attention_list(rows: list[tuple[str, str, str, str]]) -> None:
    """rows: (severity, headline, detail, tag)"""
    items = []
    for sev, head, detail, tag in rows:
        sr = {"error": "Blocking", "warning": "Warning", "ok": "OK", "info": "Info"}.get(sev, sev)
        items.append(
            f"<li><span class='g {sev}' aria-hidden='true'>{GLYPH.get(sev, '•')}</span>"
            f"<span class='m'><span class='sr-only' style='position:absolute;left:-9999px'>{sr}: </span>"
            f"<span class='h'>{escape(head)}</span>"
            + (f"<br><span class='d'>{escape(detail)}</span>" if detail else "")
            + f"</span><span class='k'>{escape(tag)}</span></li>"
        )
    st.html(f"<ul class='att'>{''.join(items)}</ul>")


def sidebar_brand(org: str, unit: str, logo: bytes | None = None) -> None:
    """Logo at the very top of the sidebar (st.logo); department name under the menu."""
    if logo:
        st.logo(logo, size="large")
    top = "" if logo else f"<div class='o'>{escape(org)}</div>"
    st.sidebar.html(f"<div class='sb-brand'>{top}<div class='u'>{escape(unit)}</div>"
                    f"<div style='font-size:12px;color:#9DB5A8;margin-top:4px'>Faculty search · Interview scheduling</div></div>")


def logo_img(img: bytes | None, alt: str, height: int = 48) -> str:
    if not img:
        return ""
    from ..core.branding import data_uri

    return (f"<img src='{data_uri(img)}' alt='{escape(alt)}' "
            f"style='max-height:{height}px;max-width:280px;display:block;margin-bottom:12px'>")


def steps(items: list[tuple[str, bool]]) -> None:
    """Setup progress: (title, done). The first unfinished step is highlighted."""
    first_open = next((i for i, (_, done) in enumerate(items) if not done), None)
    lis = []
    for i, (title, done) in enumerate(items):
        cls = "done" if done else ("next" if i == first_open else "")
        tag = "✓ Done" if done else ("Next" if i == first_open else f"Step {i + 1}")
        lis.append(f"<li class='{cls}'><span class='n'>{tag}</span>{escape(title)}</li>")
    st.html(f"<ol class='steps' aria-label='Setup progress'>{''.join(lis)}</ol>")
