"""Candidates: profile, visit days, fixed events and who they should meet."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape

import pandas as pd
import streamlit as st

from ...core import exports
from ...core import models as m
from ...core import services as sv
from ...core.notify import itinerary_link
from ...core.timeutil import fmt_date, fmt_range, fmt_time, time_slots, utc_to_local
from .. import theme
from ..ctx import write

MEET_CHOICES = ["Required", "Optional", "Not meeting"]
DAY_KINDS = {"interview": "Interview day", "travel": "Travel day (arrival / departure only)"}
HOST_LABELS = ["Driver", "Airport pick-up", "Escorted by", "Hosted by", "Tour by", "Transportation by"]


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
                      "Each candidate has visit days, fixed events (talks, meals, pick-ups) and a list of people to "
                      "meet. Fixed events reserve time, so no one-on-one meeting can be placed on top of them.")
    names = [c.name for c in search.candidates]
    tabs = st.tabs(names + ["＋ Add candidate"])
    for tab, cand in zip(tabs, search.candidates):
        with tab:
            _candidate(ctx, cand)
    with tabs[-1]:
        _new_candidate(ctx)


def _new_candidate(ctx) -> None:
    others = [c for c in ctx.search.candidates if c.visit_days]
    mode = "Start blank"
    if others:
        mode = st.radio("How do you want to start?", ["Copy another candidate's schedule", "Start blank"],
                        horizontal=True,
                        captions=["Same days, hours, events and meeting list — shifted to new dates. Fastest.",
                                  "Enter everything yourself."])
    with st.form("new_cand", border=False):
        name = st.text_input("Full name (as it should appear on the itinerary)")
        if mode.startswith("Copy"):
            a, b = st.columns(2)
            src_id = a.selectbox("Copy from", [c.id for c in others],
                                 format_func=lambda i: next(c.name for c in others if c.id == i))
            src = next(c for c in others if c.id == src_id)
            first = b.date_input("New candidate's first visit day", src.visit_days[-1].date + timedelta(days=1))
            st.caption("Event times stay the same; RSVPs and scheduled one-on-ones are not copied.")
        else:
            email = st.text_input("Email (optional — only used to send their itinerary link)")
        if st.form_submit_button("Add candidate", type="primary", icon=":material/person_add:"):
            with write(ctx, f"Added {name}"):
                if mode.startswith("Copy"):
                    if not name.strip():
                        raise sv.ValidationError("Enter the candidate's name.")
                    sv.clone_candidate(ctx.s, src, name.strip(), first, ctx.actor)
                else:
                    c = sv.save_candidate(ctx.s, ctx.search, ctx.actor, candidate=None, name=name, email=email,
                                          public_note=None, internal_notes=None)
                    for p in ctx.search.participants:  # default: everyone optional at their usual length
                        if p.active:
                            sv.upsert_requirement(ctx.s, c, p, False,
                                                  p.preferred_duration or ctx.search.default_meeting_minutes)


def _candidate(ctx, c: m.Candidate) -> None:
    s = ctx.s
    v = sv.latest_version(c)
    days_txt = " · ".join(fmt_date(d.date, long=False) + (" (travel)" if not d.is_interview else "")
                          for d in c.visit_days) or "no visit days yet"
    st.html(f"<div style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:2px 0 12px'>"
            f"{theme.status_chip(c.status, sv.has_unpublished_changes(c))}"
            f"<span class='small-muted'>{escape(days_txt)} · "
            f"{'last published v' + str(v.version_number) + ' on ' + fmt_date(utc_to_local(v.published_at, ctx.search.timezone), long=False) if v else 'never published'}"
            f"</span></div>")

    sec = st.segmented_control("Section", ["Profile", "Visit days", "Fixed events", "Who they meet"],
                               default="Profile", key=f"sec_{c.id}", label_visibility="collapsed")
    if sec == "Profile":
        _profile(ctx, c)
    elif sec == "Visit days":
        _days(ctx, c)
    elif sec == "Fixed events":
        _events(ctx, c)
    else:
        _requirements(ctx, c)


def _profile(ctx, c: m.Candidate) -> None:
    s = ctx.s
    with st.form(f"prof_{c.id}"):
        a, b = st.columns(2)
        name = a.text_input("Name", c.name)
        affiliation = b.text_input("Title and institution (shown under their name)", c.affiliation or "",
                                   placeholder="e.g. Ph.D. Candidate at Example University")
        email = st.text_input("Email (only used to send them their itinerary link)", c.email or "")
        note = st.text_area("Welcome note on the candidate's itinerary", c.public_note or "",
                            help="Visible to the candidate (e.g. hotel, parking, who to call).")
        internal = st.text_area("Internal notes (coordinator only)", c.internal_notes or "",
                                help="Never shown to faculty or the candidate.")
        if st.form_submit_button("Save profile", type="primary"):
            with write(ctx, "Profile saved"):
                sv.save_candidate(s, ctx.search, ctx.actor, candidate=c, name=name, email=email, public_note=note,
                                  internal_notes=internal, expected_version=c.version_id, affiliation=affiliation)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Itinerary status**")
        if c.status == "published":
            st.caption("Published. Edits go into the draft; publish again from *Itineraries & export*.")
            if st.button("Move back to draft", key=f"bd_{c.id}"):
                with write(ctx, "Status changed"):
                    sv.set_status(s, c, "draft", ctx.actor)
        else:
            choice = st.radio("Status", ["draft", "review"], index=0 if c.status != "review" else 1, horizontal=True,
                              format_func=lambda x: theme.STATUS_LABEL[x][1], key=f"st_{c.id}",
                              label_visibility="collapsed")
            if choice != c.status:
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


def _days(ctx, c: m.Candidate) -> None:
    st.caption("One row per day. **Interview days** get one-on-one meetings and appear on faculty availability "
               "forms. **Travel days** (arrival or departure only) appear on the itinerary but nobody is asked "
               "for availability. The note appears at the top of that day, e.g. “Breakfast at the hotel.”")
    df = pd.DataFrame([{"Date": d.date, "Type": DAY_KINDS["travel" if not d.is_interview else "interview"],
                        "Start": d.start_time, "End": d.end_time, "Note": d.note or ""} for d in c.visit_days],
                      columns=["Date", "Type", "Start", "End", "Note"])
    ed = st.data_editor(df, num_rows="dynamic", key=f"vd_{c.id}", width="stretch", hide_index=True, column_config={
        "Date": st.column_config.DateColumn(required=True, format="ddd MMM D, YYYY"),
        "Type": st.column_config.SelectboxColumn(options=list(DAY_KINDS.values()), required=True,
                                                 default=DAY_KINDS["interview"]),
        "Start": st.column_config.TimeColumn("Interview hours from", required=True, format="h:mm a", step=300,
                                             default=time(8, 30)),
        "End": st.column_config.TimeColumn("until", required=True, format="h:mm a", step=300, default=time(16, 30)),
        "Note": st.column_config.TextColumn("Note for the day (optional)", width="large"),
    })
    if st.button("Save visit days", type="primary", key=f"vds_{c.id}"):
        rows = []
        for _, r in ed.dropna(subset=["Date"]).iterrows():
            kind = "travel" if r["Type"] == DAY_KINDS["travel"] else "interview"
            rows.append((pd.Timestamp(r["Date"]).date(), _as_time(r["Start"]), _as_time(r["End"]), kind,
                         r["Note"] if isinstance(r["Note"], str) else None))
        with write(ctx, "Visit days saved"):
            sv.replace_visit_days(ctx.s, c, rows, ctx.actor)


def _events(ctx, c: m.Candidate) -> None:
    s = ctx.s
    if not c.visit_days:
        st.info("Add visit days first.")
        return
    others = [o for o in ctx.search.candidates if o.id != c.id and o.fixed_events]
    top = st.columns([1, 1, 2])
    if top[0].button("Add fixed event", icon=":material/add:", type="primary", key=f"ea_{c.id}", width="stretch"):
        _event_dialog(ctx, c, None)
    if others and top[1].button("Copy from another candidate", icon=":material/content_copy:", key=f"cp_{c.id}",
                                width="stretch"):
        _copy_dialog(ctx, c, others)
    if not c.fixed_events:
        st.html("<p class='small-muted'>No fixed events yet. Typical ones: airport pick-up, research talk, teaching "
                "demo, lunch, dinner, meeting with the chair, campus tour, departure.</p>")
    by_day: dict[date, list[m.FixedEvent]] = {}
    for e in c.fixed_events:
        by_day.setdefault(e.start.date(), []).append(e)
    for d, evs in sorted(by_day.items()):
        st.html(f"<div class='dayh' style='font-size:1.05rem;margin-top:14px'>{fmt_date(d)}</div>")
        for e in evs:
            att = ", ".join(a.name for a in e.attendees)
            who = " · ".join(x for x in [e.audience or "", att] if x)
            tags = [f"<span class='chip draft'>{escape(m.EVENT_KINDS.get(e.kind, e.kind))}</span>"]
            if e.rsvp_open:
                tags.append(f"<span class='chip review'>✓ Faculty sign-up · {len(e.attendees)}</span>")
            if e.needs_host and not e.host:
                tags.append(f"<span class='chip warning'>▲ {escape(e.host_label or 'Host')} needed</span>")
            elif e.host:
                tags.append(f"<span class='chip ok'>{escape(exports.host_line({'host_label': e.host_label, 'host': e.host}))}"
                            "</span>")
            cols = st.columns([0.2, 0.5, 0.18, 0.12], vertical_alignment="center")
            cols[0].html(f"<b>{fmt_range(e.start, e.end)}</b>")
            cols[1].html(f"<b>{escape(e.title)}</b> {' '.join(tags)}"
                         f"<br><span class='small-muted'>{escape(who) or 'Candidate only'}</span>")
            cols[2].html(f"<span class='small-muted'>{escape(e.location.name) if e.location else '—'}</span>")
            with cols[3]:
                b1, b2 = st.columns(2)
                if b1.button("", icon=":material/edit:", key=f"ee_{e.id}", help="Edit"):
                    _event_dialog(ctx, c, e)
                with b2.popover("", icon=":material/delete:", help="Delete"):
                    st.markdown(f"Delete **{escape(e.title)}**?")
                    if st.button("Delete event", key=f"ed_{e.id}", type="primary"):
                        with write(ctx, "Event removed"):
                            sv.delete_event(s, e, ctx.actor)


@st.dialog("Copy fixed events")
def _copy_dialog(ctx, c: m.Candidate, others: list[m.Candidate]) -> None:
    src_id = st.selectbox("Copy events from", [o.id for o in others], format_func=lambda i: next(o.name for o in others if o.id == i))
    src = next(o for o in others if o.id == src_id)
    pairs = " · ".join(f"{fmt_date(a.date, long=False)} → {fmt_date(b.date, long=False)}"
                       for a, b in zip(src.visit_days, c.visit_days))
    st.caption(f"Days are matched in order: {pairs or '—'}. Times stay the same. Sign-up lists start empty.")
    replace = st.checkbox(f"Replace {c.name}'s current events ({len(c.fixed_events)})", value=not c.fixed_events)
    if st.button("Copy events", type="primary"):
        with write(ctx, "Events copied"):
            sv.copy_events(ctx.s, src, c, ctx.actor, replace=replace)


@st.dialog("Fixed event", width="large")
def _event_dialog(ctx, c: m.Candidate, e: m.FixedEvent | None) -> None:
    search = ctx.search
    dates = [d.date for d in c.visit_days]
    kinds = list(m.EVENT_KINDS)
    title = st.text_input("Title (shown on the itinerary)", e.title if e else "")
    k1, k2 = st.columns(2)
    kind = k1.selectbox("Type", kinds, index=kinds.index(e.kind) if e and e.kind in kinds else 0,
                        format_func=lambda k: m.EVENT_KINDS[k])
    d = k2.selectbox("Date", dates, index=dates.index(e.start.date()) if e and e.start.date() in dates else 0,
                     format_func=lambda x: fmt_date(x, long=False))
    slots = time_slots(time(6, 0), time(23, 0), 5) + [time(23, 0)]
    if e:  # keep off-grid times such as a 11:34 flight arrival exactly as entered
        slots = sorted(set(slots) | {e.start.time(), e.end.time()})
    t1, t2 = st.columns(2)
    s0 = t1.selectbox("Starts", slots, index=slots.index(e.start.time()) if e and e.start.time() in slots
                      else slots.index(time(10, 0)), format_func=fmt_time)
    end_opts = [t for t in slots if t > s0]
    default_end = e.end.time() if e and e.end.time() in end_opts else end_opts[min(11, len(end_opts) - 1)]
    s1 = t2.selectbox("Ends", end_opts, index=end_opts.index(default_end), format_func=fmt_time)

    NEW = -1
    locs = [None] + [l.id for l in search.locations] + [NEW]
    lname = {l.id: l.name for l in search.locations}
    loc = st.selectbox("Location", locs, index=locs.index(e.location_id) if e and e.location_id in locs else 0,
                       format_func=lambda i: "—" if i is None else ("＋ New location…" if i == NEW else lname[i]))
    new_loc = st.text_input("New location name", placeholder="e.g. O’Kelly 201") if loc == NEW else ""

    audience = st.text_input("Audience (optional)", (e.audience or "") if e else "",
                             placeholder="e.g. Open to all faculty and graduate students")
    note = st.text_area("Instructions / note for the candidate (optional)", (e.public_note or "") if e else "",
                        placeholder="e.g. Please prepare a 40–45 minute talk…", height=90)

    st.markdown("**Logistics**")
    h1, h2, h3 = st.columns([1, 1.4, 1])
    labels = [None] + HOST_LABELS + ([e.host_label] if e and e.host_label and e.host_label not in HOST_LABELS else [])
    host_label = h1.selectbox("Role", labels, index=labels.index(e.host_label) if e and e.host_label in labels else 0,
                              format_func=lambda x: "None" if x is None else x,
                              help="For pick-ups, escorts, tours and rides. Shown on the itinerary once a person is named.")
    host = h2.text_input("Person (leave blank if not decided)", (e.host or "") if e else "", disabled=host_label is None)
    needs_host = h3.checkbox("Someone is needed", (e.needs_host if e else False), disabled=host_label is None,
                             help="Shows on the dashboard until a person is filled in.")
    if host_label is None:
        host, needs_host = "", False

    people = {p.id: p.name for p in search.participants if p.active}
    rsvp = st.toggle("Let faculty sign up for this event from their own page (shows a head count)",
                     e.rsvp_open if e else False)
    att = st.multiselect("Attending (they're treated as busy at this time)", list(people),
                         default=[a.id for a in e.attendees if a.id in people] if e else [],
                         format_func=lambda i: people[i])
    if st.button("Save event", type="primary"):
        with write(ctx, "Event saved"):
            loc_id = loc
            if loc == NEW:
                if not new_loc.strip():
                    raise sv.ValidationError("Type a name for the new location.")
                l = m.Location(search_id=search.id, name=new_loc.strip())
                ctx.s.add(l)
                ctx.s.flush()
                loc_id = l.id
            sv.save_event(ctx.s, c, ctx.actor, event=e, title=title, kind=kind, start=datetime.combine(d, s0),
                          end=datetime.combine(d, s1), location_id=loc_id, audience=audience, attendee_ids=att,
                          public_note=note, host_label=host_label, host=host, needs_host=needs_host, rsvp_open=rsvp)


def _requirements(ctx, c: m.Candidate) -> None:
    st.caption("Who this candidate meets one-on-one. Required meetings come first for the scheduling assistant and "
               "are tracked on the dashboard. The itinerary note is shown to the candidate (e.g. a Zoom link, "
               "“escorted by …”). Group events like the chair meeting or lunches belong under *Fixed events*.")
    reqs = {r.participant_id: r for r in c.requirements}
    rows = []
    for p in ctx.search.participants:
        if not p.active:
            continue
        r = reqs.get(p.id)
        rows.append({"pid": p.id, "Participant": p.name, "Role": p.role_title or "",
                     "Meeting": ("Required" if r.required else "Optional") if r else "Not meeting",
                     "Minutes": r.duration_minutes if r else (p.preferred_duration or ctx.search.default_meeting_minutes),
                     "Itinerary note": (r.public_note or "") if r else ""})
    if not rows:
        st.info("Add participants first (Participants page).")
        return
    df = pd.DataFrame(rows)
    ed = st.data_editor(df, key=f"rq_{c.id}", hide_index=True, width="stretch", disabled=["Participant", "Role"],
                        column_order=["Participant", "Role", "Meeting", "Minutes", "Itinerary note"], column_config={
                            "Meeting": st.column_config.SelectboxColumn(options=MEET_CHOICES, required=True),
                            "Minutes": st.column_config.NumberColumn(min_value=5, max_value=240, step=5),
                            "Itinerary note": st.column_config.TextColumn(width="medium"),
                        })
    n_req = int((ed["Meeting"] == "Required").sum())
    n_opt = int((ed["Meeting"] == "Optional").sum())
    st.caption(f"{n_req} required · {n_opt} optional")
    if st.button("Save meeting list", type="primary", key=f"rqs_{c.id}"):
        with write(ctx, "Meeting list saved"):
            sv.save_requirements(ctx.s, c, [(int(r["pid"]), r["Meeting"], int(r["Minutes"]),
                                             r["Itinerary note"] if isinstance(r["Itinerary note"], str) else None)
                                            for _, r in ed.iterrows()], ctx.actor)
