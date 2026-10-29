"""Candidate-facing, read-only itinerary. Shows ONLY the latest published snapshot:
no internal notes, no availability, no other candidates, no email addresses."""
from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

from ...core import exports, services as sv
from ...core.timeutil import fmt_date


def itinerary_html(snap: dict, org: str, unit: str, draft: bool = False) -> str:
    dates = [date.fromisoformat(d["date"]) for d in snap["days"]]
    span = " &amp; ".join(fmt_date(d) for d in dates)
    parts = [f"<div class='paper'><div class='org'>{escape(org)}</div><div class='unit'>{escape(unit)}</div><div class='rule'></div>"
             f"<h1>Campus Visit Itinerary{' — DRAFT' if draft else ''}</h1>"
             f"<div style='font-size:1.1rem;margin-top:4px'><b>{escape(snap['candidate']['name'])}</b></div>"
             f"<div class='small-muted'>{escape(snap['search']['name'])} · {span}</div>"]
    if snap["candidate"].get("note"):
        parts.append(f"<p style='margin:14px 0 0'>{escape(snap['candidate']['note'])}</p>")
    for d in snap["days"]:
        parts.append(f"<div class='dayh'>{fmt_date(date.fromisoformat(d['date']))}</div>")
        rows = exports.day_rows(d)
        if not rows:
            parts.append("<p class='small-muted'>Schedule to be confirmed.</p>")
            continue
        trs = []
        for r in rows:
            cls = "gap" if r["kind"] == "gap" else ("ev" if r["kind"] != "meeting" else "")
            note = f"<div class='note'>{escape(r['note'])}</div>" if r.get("note") else ""
            trs.append(f"<tr class='{cls}'><td class='t'>{escape(r['time'])}</td><td>{escape(r['event'])}{note}</td>"
                       f"<td>{escape(r['participants'])}</td><td>{escape(r['location'])}</td></tr>")
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
    st.html(itinerary_html(snap, ctx.settings.org_name, ctx.settings.unit_name))
    c1, c2, c3, _ = st.columns([1, 1, 1, 1.5])
    c1.download_button("Download PDF", exports.candidate_pdf(snap, ctx.settings.org_name, ctx.settings.unit_name,
                                                             ctx.settings.brand_logo_path),
                       file_name="campus-visit-itinerary.pdf", mime="application/pdf", icon=":material/picture_as_pdf:")
    c2.download_button("Add to calendar", exports.candidate_ics(snap), file_name="campus-visit.ics",
                       mime="text/calendar", icon=":material/calendar_add_on:")
    c3.html("<span class='small-muted'>Tip: use your browser's Print for a paper copy.</span>")
