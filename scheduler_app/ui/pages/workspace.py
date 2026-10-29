"""Scheduling workspace: a visual timeline of candidates and participants.

Click a meeting to edit it, drag it to move it, or click an empty spot on a
candidate's lane to add a meeting there. Every change goes through the same
validation as the rest of the app (no double-booking, no overlap with fixed
events, override reason required outside availability)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape

import streamlit as st

from ...core import models as m
from ...core import services as sv
from ...core.conflicts import ERROR, detect
from ...core.problem import load_problem
from ...core.scheduler import open_slots
from ...core.timeutil import covers, fmt_date, fmt_range, fmt_time, merge
from .. import theme
from ..components.timeline import timeline
from ..ctx import write


def _mins(dt: datetime | time) -> int:
    return dt.hour * 60 + dt.minute


def _short(name: str) -> str:
    return name.split(" (")[0]


def _initials(name: str) -> str:
    words = [w for w in _short(name).replace("Dr.", "").replace("Prof.", "").split() if w[:1].isalpha()]
    return "".join(w[0].upper() for w in words[:3]) or "?"


def render(ctx) -> None:
    search = ctx.search
    s = ctx.s
    theme.page_header("Coordinate", "Scheduling workspace",
                      "Click a meeting to edit it, drag it sideways to move it, or click an empty spot on a candidate's "
                      "lane to add a meeting. Nothing here is visible to faculty or candidates until you publish.")
    if not search.candidates or not sv.visit_dates(search):
        st.info("Add candidates and their visit days first (Candidates page).")
        return

    prob = load_problem(s, search.id)
    issues = detect(prob)
    sev: dict[int, str] = {}
    for i in issues:
        for mid in i.meeting_ids:
            if sev.get(mid) != ERROR:
                sev[mid] = i.severity

    c1, c2, c3 = st.columns([1.4, 1.6, 1.2], vertical_alignment="bottom")
    view = c1.segmented_control("View", ["Day", "Candidate", "Combined"], default="Candidate", key="ws_view")
    dates = sv.visit_dates(search)
    cand_ids = [c.id for c in search.candidates]
    cname = {c.id: c.name for c in search.candidates}
    if view == "Day":
        d_pick = c2.selectbox("Day", dates, format_func=lambda d: fmt_date(d), key="ws_day")
        show_dates, show_cands = [d_pick], [c for c in search.candidates if any(v.date == d_pick for v in c.visit_days)]
    elif view == "Combined":
        show_dates, show_cands = dates, list(search.candidates)
        c2.html("<span class='small-muted'>All candidates and all visit days.</span>")
    else:
        cid = c2.selectbox("Candidate", cand_ids, format_func=lambda i: cname[i], key="ws_cand")
        cand = next(c for c in search.candidates if c.id == cid)
        show_dates, show_cands = [v.date for v in cand.visit_days], [cand]
    who = c3.selectbox("Participant rows", ["People they should meet", "Everyone", "Hide participants"], key="ws_who")

    st.html(
        "<div class='legend'>"
        "<span><i style='background:#0A5C41'></i>Meeting</span>"
        "<span><i style='background:#0A5C41;background-image:repeating-linear-gradient(135deg,transparent 0 4px,rgba(255,255,255,.35) 4px 6px)'></i>🔒 Locked</span>"
        "<span><i style='background:#8A5A00'></i>▲ Warning</span><span><i style='background:#B42318'></i>✕ Conflict</span>"
        "<span><i style='background:#E6E3F2;border:1px solid #C9C3E3'></i>Fixed event</span>"
        "<span><i style='background:#CDEBDB'></i>Available</span><span><i style='background:#7FC3A0'></i>★ Preferred</span>"
        "<span><i style='background:repeating-linear-gradient(135deg,#F7F6F2 0 4px,#E2DFD6 4px 6px)'></i>No response</span>"
        "<span><i style='background:#24352D'></i>Busy</span></div>"
    )

    sections = _sections(search, prob, show_dates, show_cands, who, sev)
    action = timeline(sections, key="ws_tl", step=search.start_step_minutes or search.slot_minutes,
                      empty_text="No visit days to show for this selection.")
    if action:
        _handle(ctx, action)
    pending = st.session_state.get("ws_pending")
    if pending:
        _override_dialog(ctx, pending)

    # --- below the timeline: issues and unscheduled ----------------------
    ids = {c.id for c in show_cands}
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        st.markdown("#### Conflicts and warnings")
        mine = [i for i in issues if i.candidate_id in ids and i.code != "required_unscheduled"]
        if not mine:
            st.html("<ul class='att'><li><span class='g ok'>✓</span><span class='m'><span class='h'>No conflicts in this view."
                    "</span></span><span class='k'>OK</span></li></ul>")
        else:
            theme.attention_list([(i.severity, i.message, "Blocks publishing" if i.severity == ERROR else "",
                                   "Blocking" if i.severity == ERROR else "Warning") for i in mine])
    with right:
        st.markdown("#### Still to schedule")
        placed = {(x.candidate_id, x.participant_id) for x in prob.meetings}
        todo = [r for r in prob.requirements if r.candidate_id in ids and (r.candidate_id, r.participant_id) not in placed]
        todo.sort(key=lambda r: (not r.required, cname[r.candidate_id]))
        if not todo:
            st.html("<p class='small-muted'>Every requested meeting in this view is on the schedule.</p>")
        for r in todo[:30]:
            p = prob.participants[r.participant_id]
            tag = "Required" if r.required else "Optional"
            state = "" if p.submitted else " · ▲ no availability submitted"
            a, b = st.columns([3, 1], vertical_alignment="center")
            a.html(f"<b>{escape(p.name)}</b> <span class='small-muted'>→ {escape(_short(cname[r.candidate_id]))} · "
                   f"{r.duration} min · {tag}{state}</span>")
            if b.button("Place…", key=f"pl_{r.id}", width="stretch"):
                _add_dialog(ctx, r.candidate_id, None, r.participant_id)
    if st.button("Add a meeting manually", icon=":material/add:"):
        _add_dialog(ctx, show_cands[0].id if show_cands else None, None, None)


# ---------------------------------------------------------------- timeline data
def _sections(search, prob, show_dates, show_cands, who, sev) -> list[dict]:
    pname = {p.id: p.name for p in search.participants}
    cand_by_id = {c.id: c for c in search.candidates}
    out = []
    for d in show_dates:
        lanes = [c for c in show_cands if any(v.date == d for v in c.visit_days)]
        if not lanes:
            continue
        vds = [v for c in lanes for v in c.visit_days if v.date == d]
        evs = [e for c in lanes for e in c.fixed_events if e.start.date() == d]
        # Whole hours keep grid lines aligned; stretch to fit early pick-ups and evening dinners.
        lo = ((min([_mins(v.start_time) for v in vds] + [_mins(e.start) for e in evs]) - 30) // 60) * 60
        hi = -(-(max([_mins(v.end_time) for v in vds] + [_mins(e.end) for e in evs]) + 30) // 60) * 60
        lo, hi = max(lo, 0), min(hi, 24 * 60)
        rows = [{"kind": "group", "label": "Candidates", "items": []}]
        for c in lanes:
            vd = next(v for v in c.visit_days if v.date == d)
            items = [{"type": "win", "cls": "win", "s": _mins(vd.start_time), "e": _mins(vd.end_time)}]
            for e in c.fixed_events:
                if e.start.date() == d:
                    items.append({"type": "event", "cls": f"event {e.kind}", "s": _mins(e.start), "e": _mins(e.end),
                                  "t": e.title, "u": fmt_range(e.start, e.end),
                                  "title": f"{e.title} · {fmt_range(e.start, e.end)} (fixed)"})
            for mt in c.meetings:
                if mt.start.date() != d:
                    continue
                st_ = sev.get(mt.id, "")
                icon = "🔒" if mt.locked else ("✕" if st_ == ERROR else ("▲" if st_ else ""))
                items.append({"type": "meeting", "id": mt.id, "s": _mins(mt.start), "e": _mins(mt.end),
                              "cls": " ".join(["meeting", "warn" if st_ == "warning" else "", "error" if st_ == ERROR else "",
                                               "locked" if mt.locked else "", "auto" if mt.source == "auto" else ""]),
                              "locked": mt.locked, "icon": icon, "t": pname.get(mt.participant_id, "?"),
                              "u": fmt_range(mt.start, mt.end),
                              "title": f"{pname.get(mt.participant_id)} with {_short(c.name)}, {fmt_range(mt.start, mt.end)}"
                                       + (" — overridden: " + mt.override_reason if mt.override_reason else "")})
            if vd.is_interview:
                sub = (f"{fmt_range(datetime.combine(d, vd.start_time), datetime.combine(d, vd.end_time))} · "
                       f"{theme.STATUS_LABEL[c.status][1]}")
                rows.append({"kind": "cand", "label": _short(c.name), "sub": sub, "lane": {"cid": c.id}, "items": items})
            else:  # travel days hold events only; no one-on-ones are placed there
                rows.append({"kind": "cand", "label": _short(c.name), "sub": "Travel day · events only",
                             "items": items[1:]})
        interview = any(v.is_interview for v in vds)
        if who != "Hide participants" and interview:
            if who == "Everyone":
                people = [p for p in search.participants if p.active]
            else:
                ids = {r.participant_id for c in lanes for r in c.requirements}
                people = [p for p in search.participants if p.id in ids and p.active]
            if people:
                rows.append({"kind": "group", "label": "Participants · availability and bookings", "items": []})
            for p in people:
                pp = prob.participants[p.id]
                items = []
                if not pp.submitted:
                    items.append({"type": "none", "cls": "none", "t": "No response yet — not assumed free"})
                else:
                    day_av = [(a, b) for a, b in pp.available if a.date() == d]
                    day_pr = [(a, b) for a, b in pp.preferred if a.date() == d]
                    if not day_av and not day_pr:
                        items.append({"type": "none", "cls": "none", "t": "Not available this day"})
                    for a, b in merge(day_av):
                        items.append({"type": "avail", "cls": "avail", "s": _mins(a), "e": _mins(b),
                                      "title": f"Available {fmt_range(a, b)}"})
                    for a, b in merge(day_pr):
                        items.append({"type": "avail", "cls": "pref", "s": _mins(a), "e": _mins(b), "t": "★",
                                      "title": f"Preferred {fmt_range(a, b)}"})
                for mt in prob.meetings:
                    if mt.participant_id == p.id and mt.start.date() == d:
                        cn = _short(cand_by_id[mt.candidate_id].name)
                        items.append({"type": "busy", "cls": "busy", "s": _mins(mt.start), "e": _mins(mt.end),
                                      "t": _initials(cn), "title": f"Meeting with {cn}, {fmt_range(mt.start, mt.end)}"})
                for ev in prob.events:
                    if p.id in ev.attendee_ids and ev.start.date() == d:
                        items.append({"type": "busy", "cls": "busy other", "s": _mins(ev.start), "e": _mins(ev.end),
                                      "t": ev.title, "title": f"{ev.title} ({_short(cand_by_id[ev.candidate_id].name)})"})
                rows.append({"kind": "person", "pid": p.id, "label": p.name, "sub": p.role_title or "", "items": items})
        n_m = sum(1 for c in lanes for x in c.meetings if x.start.date() == d)
        out.append({"key": d.isoformat(), "label": fmt_date(d), "meta": f"{len(lanes)} candidate(s) · {n_m} meeting(s)",
                    "startMin": lo, "endMin": hi, "rows": rows})
    return out


# ---------------------------------------------------------------- interactions
def _handle(ctx, action: dict) -> None:
    t = action.get("type")
    if t == "select":
        mt = ctx.s.get(m.Meeting, int(action["id"]))
        if mt:
            _meeting_dialog(ctx, mt)
    elif t == "slot":
        d = date.fromisoformat(action["date"])
        start = datetime.combine(d, time()) + timedelta(minutes=int(action["startMin"]))
        _add_dialog(ctx, int(action["candidateId"]), start, None)
    elif t == "move":
        mt = ctx.s.get(m.Meeting, int(action["id"]))
        if mt is None or mt.locked:
            st.warning("Locked meetings can't be moved. Unlock it first.")
            return
        d = date.fromisoformat(action["date"])
        start = datetime.combine(d, time()) + timedelta(minutes=int(action["startMin"]))
        dur = int((mt.end - mt.start).total_seconds() // 60)
        try:
            with ctx.s.begin_nested():
                sv.save_meeting(ctx.s, candidate=mt.candidate, participant=mt.participant, start=start, duration=dur,
                                location_id=mt.location_id, actor=ctx.actor, meeting=mt, expected_version=mt.version_id)
            ctx.s.commit()
            st.session_state["_flash"] = f"Moved to {fmt_range(start, start + timedelta(minutes=dur))}"
            st.rerun()
        except sv.ValidationError as exc:
            ctx.s.rollback()
            if exc.needs_override:
                st.session_state["ws_pending"] = {"id": mt.id, "start": start.isoformat(), "dur": dur,
                                                  "version": mt.version_id}
                st.rerun()
            else:
                st.error(f"Can't move there: {exc}", icon=":material/block:")
        except sv.ServiceError as exc:
            ctx.s.rollback()
            st.error(str(exc))
    elif t == "person":
        p = ctx.s.get(m.Participant, int(action["id"]))
        if p:
            _person_dialog(ctx, p)


@st.dialog("Outside submitted availability")
def _override_dialog(ctx, pending: dict) -> None:
    mt = ctx.s.get(m.Meeting, pending["id"])
    if mt is None:
        st.session_state.pop("ws_pending", None)
        st.rerun()
    start = datetime.fromisoformat(pending["start"])
    end = start + timedelta(minutes=pending["dur"])
    st.warning(f"**{mt.participant.name}** has not marked {fmt_date(start, long=False)}, {fmt_range(start, end)} as available.")
    reason = st.text_input("Reason for the override (recorded in the audit history)",
                           placeholder="e.g. Confirmed by phone with Dr. X on Oct 30")
    a, b = st.columns(2)
    if a.button("Move anyway", type="primary", disabled=not reason.strip()):
        st.session_state.pop("ws_pending", None)
        with write(ctx, "Moved (override recorded)"):
            sv.save_meeting(ctx.s, candidate=mt.candidate, participant=mt.participant, start=start, duration=pending["dur"],
                            location_id=mt.location_id, actor=ctx.actor, meeting=mt, override_reason=reason,
                            expected_version=pending["version"])
    if b.button("Cancel"):
        st.session_state.pop("ws_pending", None)
        st.rerun()


def _slot_choices(ctx, cand: m.Candidate) -> list[datetime]:
    out = []
    step = ctx.search.start_step_minutes or ctx.search.slot_minutes
    for v in cand.visit_days:
        if not v.is_interview:
            continue
        t = datetime.combine(v.date, v.start_time)
        end = datetime.combine(v.date, v.end_time)
        while t < end:
            out.append(t)
            t += timedelta(minutes=step)
    return out


def _suggestions(ctx, cid: int, pid: int, dur: int, key: str) -> datetime | None:
    prob = load_problem(ctx.s, ctx.search.id)
    opts = open_slots(prob, cid, pid, dur)
    if not opts:
        p = prob.participants[pid]
        msg = ("has not submitted availability" if not p.submitted else
               "has no open window of that length that avoids other meetings, fixed events and buffers")
        st.html(f"<p class='small-muted'>No conflict-free time: {escape(p.name)} {msg}.</p>")
        return None
    opts.sort(key=lambda x: (not x[2], x[0]))
    labels = {o[0]: f"{'★ ' if o[2] else ''}{fmt_date(o[0], long=False)} {fmt_time(o[0])}" for o in opts[:12]}
    pick = st.pills("Conflict-free times (★ = their preferred time)", list(labels), format_func=lambda x: labels[x],
                    key=key)
    return pick


@st.dialog("Edit meeting", width="large")
def _meeting_dialog(ctx, mt: m.Meeting) -> None:
    c, p = mt.candidate, mt.participant
    st.html(f"<div class='eyebrow'>{escape(_short(c.name))}</div><div style='font-size:1.25rem;font-weight:600'>"
            f"{escape(p.name)}</div><div class='small-muted'>{escape(p.role_title or '')} · currently "
            f"{fmt_date(mt.start, long=False)}, {fmt_range(mt.start, mt.end)}"
            + (f" · source: scheduling assistant" if mt.source == "auto" else "") + "</div>")
    if mt.override_reason:
        st.info(f"Availability override on record: “{mt.override_reason}”")
    dur0 = int((mt.end - mt.start).total_seconds() // 60)
    a, b = st.columns(2)
    dur = a.number_input("Duration (minutes)", 5, 240, dur0, step=5)
    locs = [None] + [l.id for l in ctx.search.locations]
    lname = {l.id: l.name for l in ctx.search.locations}
    loc = b.selectbox("Location", locs, index=locs.index(mt.location_id) if mt.location_id in locs else 0,
                      format_func=lambda i: "—" if i is None else lname[i])
    sug = _suggestions(ctx, c.id, p.id, int(dur), key=f"sug_{mt.id}")
    choices = _slot_choices(ctx, c)
    if mt.start not in choices:  # never silently move a meeting that sits off the start-time grid
        choices = sorted(choices + [mt.start])
    default = sug or mt.start
    start = st.selectbox("Start time", choices, index=choices.index(default) if default in choices else 0,
                         format_func=lambda x: f"{fmt_date(x, long=False)} {fmt_time(x)}")
    locked = st.toggle("Lock (the scheduling assistant will never move it)", mt.locked)
    notes = st.text_input("Internal note (coordinator only)", mt.notes or "")
    reason = st.text_input("Override reason (only needed if the time is outside their availability)", "")
    x, y, z = st.columns([1.2, 1, 1])
    if x.button("Save changes", type="primary", width="stretch"):
        with write(ctx, "Meeting saved"):
            sv.save_meeting(ctx.s, candidate=c, participant=p, start=start, duration=int(dur), location_id=loc,
                            actor=ctx.actor, meeting=mt, expected_version=mt.version_id, locked=locked, notes=notes,
                            override_reason=reason or mt.override_reason)
    if y.button("Remove from draft", width="stretch", disabled=mt.locked, help="Unlock first" if mt.locked else None):
        with write(ctx, "Meeting removed"):
            sv.delete_meeting(ctx.s, mt, ctx.actor)


@st.dialog("Add a meeting", width="large")
def _add_dialog(ctx, cid: int | None, start: datetime | None, pid: int | None) -> None:
    search = ctx.search
    cands = {c.id: c for c in search.candidates if c.visit_days}
    if not cands:
        st.info("No candidates with visit days.")
        return
    cid = st.selectbox("Candidate", list(cands), index=list(cands).index(cid) if cid in cands else 0,
                       format_func=lambda i: cands[i].name)
    cand = cands[cid]
    prob = load_problem(ctx.s, search.id)
    placed = {x.participant_id for x in cand.meetings}
    reqs = {r.participant_id: r for r in cand.requirements}
    people = [p for p in search.participants if p.active and p.id not in placed]
    people.sort(key=lambda p: (p.id not in reqs, not (reqs.get(p.id).required if p.id in reqs else False), p.name))

    def label(i: int) -> str:
        p = next(x for x in people if x.id == i)
        r = reqs.get(i)
        tag = ("required" if r.required else "optional") if r else "not on list"
        free = ""
        if start is not None:
            pp = prob.participants[i]
            dur = r.duration_minutes if r else (p.preferred_duration or search.default_meeting_minutes)
            end = start + timedelta(minutes=dur)
            if not pp.submitted:
                free = " · ? no response"
            elif covers(merge(pp.preferred), start, end):
                free = " · ★ preferred then"
            elif covers(pp.free, start, end):
                free = " · ✓ free then"
            else:
                free = " · ✕ not free then"
        return f"{p.name} ({tag}){free}"

    if not people:
        st.success("Everyone is already scheduled with this candidate.")
        return
    ids = [p.id for p in people]
    pid = st.selectbox("Participant", ids, index=ids.index(pid) if pid in ids else 0, format_func=label)
    p = next(x for x in people if x.id == pid)
    r = reqs.get(pid)
    a, b = st.columns(2)
    dur = a.number_input("Duration (minutes)", 5, 240, r.duration_minutes if r else (p.preferred_duration or search.default_meeting_minutes), step=5)
    locs = [None] + [l.id for l in search.locations]
    lname = {l.id: l.name for l in search.locations}
    loc_default = sv.default_location_id_for(r, p)
    loc = b.selectbox("Location", locs, index=locs.index(loc_default) if loc_default in locs else 0,
                      format_func=lambda i: "—" if i is None else lname[i])
    sug = _suggestions(ctx, cid, pid, int(dur), key=f"sug_new_{cid}_{pid}")
    choices = _slot_choices(ctx, cand)
    default = sug or start or choices[0]
    st_ = st.selectbox("Start time", choices, index=choices.index(default) if default in choices else 0,
                       format_func=lambda x: f"{fmt_date(x, long=False)} {fmt_time(x)}")
    locked = st.toggle("Lock this meeting", False)
    reason = st.text_input("Override reason (only if outside their availability)", "")
    if st.button("Add to draft", type="primary"):
        with write(ctx, "Meeting added"):
            sv.save_meeting(ctx.s, candidate=cand, participant=p, start=st_, duration=int(dur), location_id=loc,
                            actor=ctx.actor, override_reason=reason, locked=locked)


@st.dialog("Participant")
def _person_dialog(ctx, p: m.Participant) -> None:
    search = ctx.search
    st.html(f"<div style='font-size:1.2rem;font-weight:600'>{escape(p.name)}</div>"
            f"<div class='small-muted'>{escape(' · '.join(x for x in (p.role_title, p.email) if x))}</div>")
    st.markdown("**Submitted availability**" if p.submitted_at else "**No availability submitted yet**")
    for d in sv.visit_dates(search):
        st.html(f"<div>{fmt_date(d, long=False)}: <span class='small-muted'>{escape(sv.free_windows_text(p, d))}</span></div>")
    st.markdown("**Meetings in the draft**")
    any_ = False
    for c in search.candidates:
        for mt in c.meetings:
            if mt.participant_id == p.id:
                any_ = True
                st.html(f"<div>{escape(_short(c.name))}: {fmt_date(mt.start, long=False)}, {fmt_range(mt.start, mt.end)}"
                        f"{' 🔒' if mt.locked else ''}</div>")
    if not any_:
        st.html("<span class='small-muted'>None yet.</span>")
