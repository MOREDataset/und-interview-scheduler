"""Candidates: profile, visit days, fixed events and who they should meet."""
from __future__ import annotations

from datetime import datetime, time
from html import escape

import pandas as pd
import streamlit as st

from ...core import models as m
from ...core import services as sv
from ...core.notify import itinerary_link
from ...core.timeutil import fmt_date, fmt_range, fmt_time, time_slots, utc_to_local
from .. import theme
from ..ctx import write

MEET_CHOICES = ["Required", "Optional", "Not meeting"]


def _as_time(v) -> time | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, str):
        return time.fromisoformat(v)
    return pd.Timestamp(v).time()


def render(ctx) -> None:
    search = ctx.search
    theme.page_header("Set up", "Candidates",
                      "Each candidate has visit days, fixed events (talks, meals, arrival) and a list of people to meet. "
                      "Fixed events reserve time so no one-on-one meeting can be placed on top of them.")
    names = [c.name for c in search.candidates]
    tabs = st.tabs(names + ["＋ Add candidate"])
    for tab, cand in zip(tabs, search.candidates):
        with tab:
            _candidate(ctx, cand)
    with tabs[-1]:
        _new_candidate(ctx)


def _new_candidate(ctx) -> None:
    st.markdown("Add a candidate. You can enter visit days, events and meetings right after.")
    with st.form("new_cand", border=False):
        name = st.text_input("Full name (as it should appear on the itinerary)")
        email = st.text_input("Email (optional — only used to send their itinerary link)")
        if st.form_submit_button("Add candidate", type="primary", icon=":material/person_add:"):
            with write(ctx, f"Added {name}"):
                c = sv.save_candidate(ctx.s, ctx.search, ctx.actor, candidate=None, name=name, email=email,
                                      public_note=None, internal_notes=None)
                for p in ctx.search.participants:  # default: everyone optional at their usual length
                    if p.active:
                        sv.upsert_requirement(ctx.s, c, p, False, p.preferred_duration or ctx.search.default_meeting_minutes)


def _candidate(ctx, c: m.Candidate) -> None:
    s = ctx.s
    v = sv.latest_version(c)
    st.html(f"<div style='display:flex;gap:10px;align-items:center;margin:2px 0 12px'>{theme.status_chip(c.status, sv.has_unpublished_changes(c))}"
            f"<span class='small-muted'>{'Last published v' + str(v.version_number) + ' on ' + fmt_date(utc_to_local(v.published_at, ctx.search.timezone), long=False) if v else 'Never published'}</span></div>")

    sec = st.segmented_control("Section", ["Profile", "Visit days", "Fixed events", "Who they meet"],
                               default="Profile", key=f"sec_{c.id}", label_visibility="collapsed")
    if sec == "Profile":
        with st.form(f"prof_{c.id}"):
            name = st.text_input("Name", c.name)
            email = st.text_input("Email", c.email or "")
            note = st.text_area("Note shown on the candidate's itinerary", c.public_note or "",
                                help="Visible to the candidate (e.g. where to check in, parking).")
            internal = st.text_area("Internal notes (coordinator only)", c.internal_notes or "",
                                    help="Never shown to faculty or the candidate.")
            if st.form_submit_button("Save profile", type="primary"):
                with write(ctx, "Profile saved"):
                    sv.save_candidate(s, ctx.search, ctx.actor, candidate=c, name=name, email=email, public_note=note,
                                      internal_notes=internal, expected_version=c.version_id)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Itinerary status**")
            choice = st.radio("Status", ["draft", "review"], index=0 if c.status != "review" else 1, horizontal=True,
                              format_func=lambda x: theme.STATUS_LABEL[x][1], key=f"st_{c.id}", label_visibility="collapsed",
                              disabled=c.status == "published")
            if c.status == "published":
                st.caption("Published. Edits go into the draft; publish again from *Itineraries & export*.")
                if st.button("Move back to draft", key=f"bd_{c.id}"):
                    with write(ctx, "Status changed"):
                        sv.set_status(s, c, "draft", ctx.actor)
            elif choice != c.status:
                with write(ctx, "Status changed"):
                    sv.set_status(s, c, choice, ctx.actor)
        with c2:
            st.markdown("**Candidate's itinerary link**")
            st.code(itinerary_link(ctx.settings, c.share_token), language=None)
            st.caption("Read-only. Shows only the latest *published* version.")
            if st.button("Issue a new link (old one stops working)", key=f"rot_{c.id}"):
                with write(ctx, "New link issued"):
                    sv.rotate_candidate_link(s, c, ctx.actor)
        with st.expander("Delete this candidate", icon=":material/delete:"):
            st.warning("This permanently removes the candidate, their meetings and published versions.")
            if st.text_input("Type the candidate's name to confirm", key=f"del_{c.id}") == c.name:
                if st.button("Delete permanently", type="primary", key=f"delb_{c.id}"):
                    with write(ctx, "Candidate deleted"):
                        sv.delete_candidate(s, c, ctx.actor)

    elif sec == "Visit days":
        st.caption("One row per visit day, with interview hours. Add more rows for multi-day visits.")
        df = pd.DataFrame([{"Date": d.date, "Start": d.start_time, "End": d.end_time} for d in c.visit_days],
                          columns=["Date", "Start", "End"])
        ed = st.data_editor(df, num_rows="dynamic", key=f"vd_{c.id}", width="stretch", column_config={
            "Date": st.column_config.DateColumn(required=True, format="ddd MMM D, YYYY"),
            "Start": st.column_config.TimeColumn(required=True, format="h:mm a", step=900, default=time(8, 30)),
            "End": st.column_config.TimeColumn(required=True, format="h:mm a", step=900, default=time(17, 0)),
        })
        if st.button("Save visit days", type="primary", key=f"vds_{c.id}"):
            rows = [(r["Date"], r["Start"], r["End"]) for _, r in ed.dropna(subset=["Date"]).iterrows()]
            rows = [(pd.Timestamp(d).date(), _as_time(a), _as_time(b)) for d, a, b in rows]
            with write(ctx, "Visit days saved"):
                sv.replace_visit_days(s, c, rows, ctx.actor)

    elif sec == "Fixed events":
        _events(ctx, c)
    else:
        _requirements(ctx, c)


def _events(ctx, c: m.Candidate) -> None:
    s = ctx.s
    if not c.visit_days:
        st.info("Add visit days first.")
        return
    if not c.fixed_events:
        st.html("<p class='small-muted'>No fixed events yet. Typical ones: arrival, research talk, teaching demo, lunch, "
                "meeting with the chair or dean, departure.</p>")
    for e in c.fixed_events:
        att = ", ".join(a.name for a in e.attendees)
        who = " · ".join(x for x in [e.audience or "", att] if x)
        cols = st.columns([0.28, 0.42, 0.18, 0.12], vertical_alignment="center")
        cols[0].html(f"<b>{fmt_date(e.start, long=False)}</b><br><span class='small-muted'>{fmt_range(e.start, e.end)}</span>")
        cols[1].html(f"<b>{escape(e.title)}</b> <span class='chip draft'>{escape(m.EVENT_KINDS.get(e.kind, e.kind))}</span>"
                     f"<br><span class='small-muted'>{escape(who) or 'Candidate only'}</span>")
        cols[2].html(f"<span class='small-muted'>{escape(e.location.name) if e.location else '—'}</span>")
        with cols[3]:
            b1, b2 = st.columns(2)
            if b1.button("", icon=":material/edit:", key=f"ee_{e.id}", help="Edit"):
                _event_dialog(ctx, c, e)
            if b2.button("", icon=":material/delete:", key=f"ed_{e.id}", help="Delete"):
                with write(ctx, "Event removed"):
                    sv.delete_event(s, e, ctx.actor)
    if st.button("Add fixed event", icon=":material/add:", type="primary", key=f"ea_{c.id}"):
        _event_dialog(ctx, c, None)


@st.dialog("Fixed event", width="large")
def _event_dialog(ctx, c: m.Candidate, e: m.FixedEvent | None) -> None:
    search = ctx.search
    dates = [d.date for d in c.visit_days]
    kinds = list(m.EVENT_KINDS)
    title = st.text_input("Title (shown on the itinerary)", e.title if e else "")
    k1, k2 = st.columns(2)
    kind = k1.selectbox("Type", kinds, index=kinds.index(e.kind) if e else 0, format_func=lambda k: m.EVENT_KINDS[k])
    d = k2.selectbox("Date", dates, index=dates.index(e.start.date()) if e and e.start.date() in dates else 0,
                     format_func=lambda x: fmt_date(x, long=False))
    slots = time_slots(time(7, 0), time(21, 0), 5) + [time(21, 0)]
    t1, t2 = st.columns(2)
    s0 = t1.selectbox("Starts", slots, index=slots.index(e.start.time()) if e and e.start.time() in slots else slots.index(time(10, 0)),
                      format_func=fmt_time)
    end_opts = [t for t in slots if t > s0]
    default_end = e.end.time() if e and e.end.time() in end_opts else end_opts[min(11, len(end_opts) - 1)]
    s1 = t2.selectbox("Ends", end_opts, index=end_opts.index(default_end), format_func=fmt_time)
    locs = [None] + [l.id for l in search.locations]
    lname = {l.id: l.name for l in search.locations}
    loc = st.selectbox("Location", locs, index=locs.index(e.location_id) if e and e.location_id in locs else 0,
                       format_func=lambda i: "—" if i is None else lname[i])
    audience = st.text_input("Audience (optional, e.g. “Department faculty and students”)", (e.audience or "") if e else "")
    people = {p.id: p.name for p in search.participants if p.active}
    att = st.multiselect("Participants who attend (they'll be treated as busy at this time)", list(people),
                         default=[a.id for a in e.attendees] if e else [], format_func=lambda i: people[i])
    note = st.text_input("Note for the candidate (optional)", (e.public_note or "") if e else "")
    if st.button("Save event", type="primary"):
        with write(ctx, "Event saved"):
            sv.save_event(ctx.s, c, ctx.actor, event=e, title=title, kind=kind, start=datetime.combine(d, s0),
                          end=datetime.combine(d, s1), location_id=loc, audience=audience, attendee_ids=att,
                          public_note=note)


def _requirements(ctx, c: m.Candidate) -> None:
    st.caption("Choose who this candidate meets one-on-one. Required meetings are prioritized by the scheduling "
               "assistant and tracked on the dashboard.")
    reqs = {r.participant_id: r for r in c.requirements}
    rows = []
    for p in ctx.search.participants:
        if not p.active:
            continue
        r = reqs.get(p.id)
        rows.append({"pid": p.id, "Participant": p.name, "Role": p.role_title or "",
                     "Meeting": ("Required" if r.required else "Optional") if r else "Not meeting",
                     "Minutes": r.duration_minutes if r else (p.preferred_duration or ctx.search.default_meeting_minutes)})
    if not rows:
        st.info("Add participants first (Participants page).")
        return
    df = pd.DataFrame(rows)
    ed = st.data_editor(df, key=f"rq_{c.id}", hide_index=True, width="stretch", disabled=["Participant", "Role"],
                        column_order=["Participant", "Role", "Meeting", "Minutes"], column_config={
                            "Meeting": st.column_config.SelectboxColumn(options=MEET_CHOICES, required=True),
                            "Minutes": st.column_config.NumberColumn(min_value=5, max_value=240, step=5),
                        })
    n_req = int((ed["Meeting"] == "Required").sum())
    n_opt = int((ed["Meeting"] == "Optional").sum())
    st.caption(f"{n_req} required · {n_opt} optional")
    if st.button("Save meeting list", type="primary", key=f"rqs_{c.id}"):
        with write(ctx, "Meeting list saved"):
            sv.save_requirements(ctx.s, c, [(int(r["pid"]), r["Meeting"], int(r["Minutes"])) for _, r in ed.iterrows()],
                                 ctx.actor)
