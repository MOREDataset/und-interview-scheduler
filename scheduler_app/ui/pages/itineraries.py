"""Review, publish and export itineraries."""
from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ...core import exports, notify
from ...core import services as sv
from ...core.timeutil import fmt_date, fmt_time, utc_to_local
from .. import theme
from ..ctx import write
from .candidate_view import itinerary_html


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.split(" (")[0].lower()).strip("-")


def render(ctx) -> None:
    search = ctx.search
    theme.page_header("Coordinate", "Itineraries & export",
                      "Publishing saves a fixed version that faculty and the candidate can see. Later edits stay in the "
                      "draft until you publish again.")
    tabs = st.tabs([c.name.split(" (")[0] for c in search.candidates] + ["Faculty schedules", "Coordinator exports"])
    for tab, c in zip(tabs, search.candidates):
        with tab:
            _candidate(ctx, c)
    with tabs[-2]:
        _faculty(ctx)
    with tabs[-1]:
        _coordinator(ctx)


def _candidate(ctx, c) -> None:
    s, settings = ctx.s, ctx.settings
    v = sv.latest_version(c)
    changed = sv.has_unpublished_changes(c)
    errs = sv.blocking_issues(s, c)
    st.html(f"<div style='display:flex;gap:8px;align-items:center;flex-wrap:wrap'>{theme.status_chip(c.status, changed)}"
            + (theme.chip("error", f"{len(errs)} blocking issue(s)", "✕") if errs else theme.chip("ok", "No blocking issues", "✓"))
            + f"<span class='small-muted'>{'Published v' + str(v.version_number) + ' · ' + fmt_date(utc_to_local(v.published_at, ctx.search.timezone), long=False) if v else 'Not published yet'}</span></div>")

    which = "Draft"
    if v:
        which = st.segmented_control("Preview", ["Draft", f"Published v{v.version_number}"], default="Draft",
                                     key=f"pv_{c.id}", label_visibility="collapsed") or "Draft"
    snap = sv.build_itinerary(c) if which == "Draft" else v.snapshot
    left, right = st.columns([2.3, 1], gap="large")
    with left:
        st.html(itinerary_html(snap, settings.org_name, settings.unit_name, draft=which == "Draft"))
    with right:
        st.markdown("#### Publish")
        if errs:
            for e in errs[:5]:
                st.error(e.message, icon=":material/block:")
            st.caption("Fix these in the scheduling workspace before publishing.")
        if c.status == "draft":
            if st.button("Mark ready for review", key=f"rr_{c.id}", width="stretch"):
                with write(ctx, "Marked ready for review"):
                    sv.set_status(s, c, "review", ctx.actor)
        note = st.text_input("Change note (optional)", key=f"pn_{c.id}",
                             placeholder="e.g. Moved Dr. X to the afternoon")
        label = "Publish update" if v else "Publish itinerary"
        if st.button(label, type="primary", key=f"pub_{c.id}", width="stretch", disabled=bool(errs) or (v is not None and not changed),
                     icon=":material/publish:"):
            with write(ctx, f"Published {c.name}"):
                sv.publish(s, c, ctx.actor, note or None)
        if v and not changed:
            st.caption("The published version matches the draft.")

        st.markdown("#### Downloads")
        pdf_snap = snap
        st.download_button(f"PDF ({which.lower()})", exports.candidate_pdf(pdf_snap, settings.org_name, settings.unit_name,
                                                                          settings.brand_logo_path, draft=which == "Draft"),
                           file_name=f"itinerary-{_slug(c.name)}-{'draft' if which == 'Draft' else 'v' + str(v.version_number)}.pdf",
                           mime="application/pdf", key=f"dl_pdf_{c.id}", width="stretch", icon=":material/picture_as_pdf:")
        st.download_button("Calendar file (.ics)", exports.candidate_ics(pdf_snap), file_name=f"itinerary-{_slug(c.name)}.ics",
                           mime="text/calendar", key=f"dl_ics_{c.id}", width="stretch", icon=":material/calendar_month:")
        st.download_button("Spreadsheet (.csv)", exports.itinerary_csv([pdf_snap]), file_name=f"itinerary-{_slug(c.name)}.csv",
                           mime="text/csv", key=f"dl_csv_{c.id}", width="stretch", icon=":material/table:")

        st.markdown("#### Share with the candidate")
        st.code(notify.itinerary_link(settings, c.share_token), language=None)
        if not v:
            st.caption("The link shows “not published yet” until you publish.")
        elif c.email:
            d = notify.itinerary_notice(settings, ctx.search, c)
            if settings.email_enabled:
                if st.button("Email the link to the candidate", key=f"em_{c.id}", width="stretch"):
                    try:
                        notify.send(settings, d)
                        with write(ctx, "Email sent"):
                            sv.audit(s, c.search_id, ctx.actor, "itinerary_emailed", f"Emailed itinerary link to {c.name}")
                    except Exception as exc:  # pragma: no cover - network
                        st.error(f"Email failed: {exc}")
            else:
                st.html(f"<a href='{escape(d.mailto)}'>✉ Open a pre-written email to {escape(c.name)}</a>")

        if c.versions:
            st.markdown("#### Version history")
            tz = ZoneInfo(ctx.search.timezone)
            hist = [{"Version": f"v{x.version_number}",
                     "Published": x.published_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%b %d, %-I:%M %p"),
                     "By": x.published_by or "", "Note": x.note or ""} for x in reversed(c.versions)]
            st.dataframe(pd.DataFrame(hist), hide_index=True, width="stretch")


def _faculty(ctx) -> None:
    search = ctx.search
    settings = ctx.settings
    st.caption("Schedules here include only PUBLISHED meetings — exactly what each person sees on their own page.")
    people = [p for p in search.participants if p.active]
    data = []
    for p in people:
        items = sv.published_items_for_participant(search, p.id)
        data.append((p, items))
    pid = st.selectbox("Participant", [p.id for p, _ in data],
                       format_func=lambda i: next(f"{p.name} — {len(it)} published item(s)" for p, it in data if p.id == i))
    p, items = next((p, it) for p, it in data if p.id == pid)
    if not items:
        st.info(f"{p.name} has no published meetings yet.")
    else:
        rows = [{"Date": fmt_date(pd.Timestamp(it["start"]).to_pydatetime(), long=False),
                 "Time": f"{fmt_time(pd.Timestamp(it['start']).to_pydatetime())}–{fmt_time(pd.Timestamp(it['end']).to_pydatetime())}",
                 "Candidate": it["candidate"], "What": exports.event_title(it), "Location": it.get("location") or ""} for it in items]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        a, b, c = st.columns(3)
        a.download_button("PDF", exports.participant_pdf(p.name, items, search.timezone, settings.org_name, settings.unit_name,
                                                         search.name, settings.brand_logo_path),
                          file_name=f"schedule-{_slug(p.name)}.pdf", mime="application/pdf", width="stretch")
        b.download_button(".ics", exports.participant_ics(p.name, items, search.timezone, search.department),
                          file_name=f"schedule-{_slug(p.name)}.ics", mime="text/calendar", width="stretch")
        c.download_button("CSV", exports.participant_csv(p.name, items, search.timezone),
                          file_name=f"schedule-{_slug(p.name)}.csv", mime="text/csv", width="stretch")
    st.html("<p class='small-muted'>Each person can also open their own link at any time to see their published "
            "meetings and add them to their calendar.</p>")


def _coordinator(ctx) -> None:
    search, settings = ctx.search, ctx.settings
    st.caption("Internal documents covering every candidate. Draft = current working schedule.")
    snaps = [sv.build_itinerary(c) for c in search.candidates]
    a, b = st.columns(2)
    a.download_button("All itineraries — PDF (draft, internal)", exports.master_pdf(snaps, settings.org_name, settings.unit_name,
                                                                                   settings.brand_logo_path),
                      file_name="all-itineraries-draft.pdf", mime="application/pdf", width="stretch",
                      icon=":material/picture_as_pdf:")
    a.download_button("All itineraries — CSV (draft)", exports.itinerary_csv(snaps), file_name="all-itineraries-draft.csv",
                      mime="text/csv", width="stretch", icon=":material/table:")
    pubs = [sv.latest_version(c).snapshot for c in search.candidates if sv.latest_version(c)]
    b.download_button("Published itineraries — CSV", exports.itinerary_csv(pubs), file_name="published-itineraries.csv",
                      mime="text/csv", width="stretch", disabled=not pubs, icon=":material/table:")
    # Raw meeting list with internal fields for administrative use.
    rows = []
    for c in search.candidates:
        for mt in c.meetings:
            rows.append({"Candidate": c.name, "Participant": mt.participant.name, "Email": mt.participant.email,
                         "Date": mt.start.date().isoformat(), "Start": mt.start.strftime("%H:%M"), "End": mt.end.strftime("%H:%M"),
                         "Location": (mt.location or mt.participant.location).name if (mt.location or mt.participant.location) else "",
                         "Locked": mt.locked, "Source": mt.source, "Override reason": mt.override_reason or "",
                         "Internal note": mt.notes or ""})
    df = pd.DataFrame(rows)
    b.download_button("Meeting list with internal fields — CSV", df.to_csv(index=False).encode("utf-8-sig"),
                      file_name="meetings-internal.csv", mime="text/csv", width="stretch", disabled=df.empty,
                      icon=":material/lock:")
