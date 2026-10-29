"""Candidate-facing, read-only itinerary. Shows ONLY the latest published snapshot:
no internal notes, no availability, no other candidates, no email addresses."""
from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

from ...core import exports, services as sv
from ...core.branding import data_uri, load_brand
from ...core.timeutil import fmt_span


def itinerary_html(snap: dict, org: str, unit: str, draft: bool = False, logo: bytes | None = None) -> str:
    span = fmt_span([date.fromisoformat(d["date"]) for d in snap["days"]])
    if logo:
        head = (f"<img src='{data_uri(logo)}' alt='{escape(org)}' style='max-height:58px;max-width:320px;display:block'>"
                f"<div class='unit' style='margin-top:6px'>{escape(unit)}</div>")
    else:
        head = f"<div class='org'>{escape(org)}</div><div class='unit'>{escape(unit)}</div>"
    aff = snap["candidate"].get("affiliation")
    parts = [f"<div class='paper'>{head}<div class='rule'></div>"
             f"<h1>Campus Visit Itinerary{' — DRAFT' if draft else ''}</h1>"
             f"<div style='font-size:1.1rem;margin-top:4px'><b>{escape(snap['candidate']['name'])}</b>"
             + (f"<span class='small-muted' style='font-size:1rem'> · {escape(aff)}</span>" if aff else "")
             + f"</div><div class='small-muted'>{escape(snap['search']['name'])} · {escape(span)}</div>"]
    if snap["candidate"].get("note"):
        parts.append(f"<p style='margin:14px 0 0'>{escape(snap['candidate']['note'])}</p>")
    for d in snap["days"]:
        parts.append(f"<div class='dayh'>{escape(exports.day_heading(d))}</div>")
        if d.get("note"):
            parts.append(f"<p class='small-muted' style='margin:0 0 4px'>{escape(d['note'])}</p>")
        rows = exports.day_rows(d)
        if not rows:
            parts.append("<p class='small-muted'>Schedule to be confirmed.</p>")
            continue
        trs = []
        for r in rows:
            cls = "gap" if r["kind"] == "gap" else ("ev" if r["kind"] != "meeting" else "")
            extra = "".join(f"<div class='note'>{escape(x)}</div>" for x in (r.get("host"), r.get("note")) if x)
            where = escape(r["location"]) + (f"<div class='note'>{escape(r['location_details'])}</div>"
                                             if r.get("location_details") else "")
            trs.append(f"<tr class='{cls}'><td class='t'>{escape(r['time'])}</td><td>{escape(r['event'])}{extra}</td>"
                       f"<td>{escape(r['participants'])}</td><td>{where}</td></tr>")
        parts.append("<table class='itin'><thead><tr><th>Time</th><th>Event / meeting</th><th>Participants</th>"
                     f"<th>Location</th></tr></thead><tbody>{''.join(trs)}</tbody></table>")
    tz = snap["search"]["timezone"]
    parts.append(f"<p class='small-muted'>All times are local Grand Forks time ({escape(tz)}).")
    if snap["search"].get("contact_email"):
        parts.append(f" Questions: {escape(snap['search'].get('contact_name', ''))} "
                     f"&lt;{escape(snap['search']['contact_email'])}&gt;.")
    parts.append("</p>")
    if snap.get("version"):
        parts.append(f"<p class='small-muted'>Version {snap['version']} · updated {escape(snap['published_at'][:10])}</p>")
    parts.append("</div>")
    return "".join(parts)


def render(ctx, cand) -> None:
    v = sv.latest_version(cand) if cand else None
    if cand is None or v is None:
        st.html("<div class='eyebrow'>Campus visit</div><div class='ph-title'>Itinerary not available</div>"
                "<p class='ph-sub'>This link is not valid, or the itinerary has not been published yet. "
                "Please contact the search coordinator.</p>")
        return
    snap = v.snapshot
    logo = load_brand(ctx.s, ctx.settings.brand_logo_path).light
    st.html(itinerary_html(snap, ctx.settings.org_name, ctx.settings.unit_name, logo=logo))
    c1, c2, c3, _ = st.columns([1, 1, 1, 1.5])
    c1.download_button("Download PDF", exports.candidate_pdf(snap, ctx.settings.org_name, ctx.settings.unit_name, logo),
                       file_name="campus-visit-itinerary.pdf", mime="application/pdf", icon=":material/picture_as_pdf:")
    c2.download_button("Add to calendar", exports.candidate_ics(snap), file_name="campus-visit.ics",
                       mime="text/calendar", icon=":material/calendar_add_on:")
    c3.html("<span class='small-muted'>Tip: use your browser's Print for a paper copy.</span>")
