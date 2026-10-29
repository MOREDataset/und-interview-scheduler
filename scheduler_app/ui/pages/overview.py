"""Overview dashboard: what's done, and what needs attention next."""
from __future__ import annotations

from html import escape

import streamlit as st

from ...core import services as sv
from ...core.conflicts import ERROR
from ...core.timeutil import fmt_date, fmt_range, fmt_span, fmt_time, utc_to_local
from .. import theme


def render(ctx) -> None:
    search = ctx.search
    stats, issues = sv.search_stats(ctx.s, search)
    dl = search.availability_deadline
    sub = f"{escape(search.name)}"
    if dl:
        state = "open" if sv.availability_is_open(search) else "closed"
        sub += f" · Availability collection is <b>{state}</b> (deadline {fmt_date(dl, long=False)}, {fmt_time(dl)})"
    theme.page_header("Overview", "Where things stand", sub)

    steps = sv.setup_steps(search)
    if not all(x.done for x in steps):
        theme.steps([(x.title, x.done) for x in steps])
        nxt = next(x for x in steps if not x.done)
        c1, c2 = st.columns([4.2, 1], vertical_alignment="center")
        c1.html(f"<div class='nextstep'><b>Next: {escape(nxt.title)}.</b> {escape(nxt.hint)}</div>")
        c2.page_link(ctx.pages[nxt.page], label="Go there", icon=":material/arrow_forward:")

    sched_req = stats.required_total - stats.required_unscheduled
    theme.stats_row([
        (str(len(search.candidates)), "Candidates", None, None),
        (str(stats.participants), "Invited participants", None, None),
        (f"{stats.submitted}", "Availability submitted", f"/ {stats.participants} · {stats.pct_submitted}%",
         stats.submitted / stats.participants if stats.participants else 0),
        (str(stats.meetings), "Meetings scheduled", None, None),
        (f"{sched_req}", "Required meetings placed", f"/ {stats.required_total}",
         sched_req / stats.required_total if stats.required_total else 0),
        (str(stats.errors), "Blocking issues", None, None),
    ])

    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.markdown("#### Needs attention")
        rows = []
        if stats.not_submitted:
            n = len(stats.not_submitted)
            rows.append(("warning", f"{n} participant{'s have' if n != 1 else ' has'} not submitted availability.",
                         ", ".join(stats.not_submitted[:6]) + (" …" if n > 6 else ""), "Responses"))
        if stats.required_unscheduled:
            n = stats.required_unscheduled
            rows.append(("warning", f"{n} required meeting{'s still need' if n != 1 else ' still needs'} scheduling.",
                         "Run the scheduling assistant or place them in the workspace.", "Assistant"))
        gaps = sv.open_logistics(search)
        if gaps:
            n = len(gaps)
            rows.append(("warning", f"{n} pick-up{'s' if n != 1 else ''}, escort{'s' if n != 1 else ''} or "
                                    f"host{'s' if n != 1 else ''} still need{'' if n != 1 else 's'} someone.",
                         "; ".join(f"{e.host_label or 'Host'}: {e.title} ({e.candidate.name.split(' (')[0]}, "
                                   f"{fmt_date(e.start, long=False)})" for e in gaps[:4]) + (" …" if n > 4 else ""),
                         "Logistics"))
        drafts = [i for i in issues if i.code == "availability_override" and "itinerary draft" in i.message]
        if drafts:
            n = len(drafts)
            rows.append(("info", f"{n} meeting time{'s' if n != 1 else ''} came from the itinerary draft and "
                                 f"{'are' if n != 1 else 'is'} not confirmed by availability yet.",
                         "Once people submit availability, re-check them in the workspace (or let the assistant "
                         "move them).", "Draft times"))
        errs = [i for i in issues if i.severity == ERROR]
        for i in errs[:6]:
            rows.append(("error", i.message, "Blocks publishing until resolved.", "Conflict"))
        for c in search.candidates:
            if c.status == "published" and sv.has_unpublished_changes(c):
                rows.append(("info", f"{c.name}: draft has changes that are not published yet.",
                             "Faculty and the candidate still see the previous version.", "Publish"))
            if not c.visit_days:
                rows.append(("warning", f"{c.name} has no visit dates.", "Add them on the Candidates page.", "Setup"))
        other = [i for i in issues if i.severity != ERROR and i.code not in ("required_unscheduled",) and i not in drafts]
        for i in other[:4]:
            rows.append(("warning", i.message, "", "Warning"))
        if not search.coordinator_email and search.candidates:
            rows.append(("info", "No contact person is shown on itineraries yet.",
                         "Add a coordinator name and email under Search settings so candidates know whom to ask.",
                         "Settings"))
        if not rows:
            rows.append(("ok", "Nothing needs attention right now.", "All required meetings are placed and there are no conflicts.", "All clear"))
        theme.attention_list(rows)
        b1, b2, b3 = st.columns(3)
        b1.page_link(ctx.pages["responses"], label="Send reminders", icon=":material/mail:")
        b2.page_link(ctx.pages["assistant"], label="Run assistant", icon=":material/auto_awesome:")
        b3.page_link(ctx.pages["workspace"], label="Open workspace", icon=":material/view_timeline:")

    with right:
        st.markdown("#### Candidates")
        if not search.candidates:
            st.info("No candidates yet. Add them on the **Candidates** page.")
        for c in search.candidates:
            idays = [d.date for d in c.visit_days if d.is_interview]
            days = fmt_span(idays) if idays else "No interview dates set"
            if len(idays) < len(c.visit_days):
                n_t = len(c.visit_days) - len(idays)
                days += f" · plus {n_t} travel day{'s' if n_t != 1 else ''}"
            open_c = sum(1 for e in c.fixed_events if e.needs_host and not (e.host or "").strip())
            req = [r for r in c.requirements if r.required]
            placed = {m.participant_id for m in c.meetings}
            req_ok = sum(1 for r in req if r.participant_id in placed)
            c_issues = [i for i in issues if i.candidate_id == c.id]
            n_err = sum(1 for i in c_issues if i.severity == ERROR)
            v = sv.latest_version(c)
            pub = f"v{v.version_number}, {fmt_date(utc_to_local(v.published_at, search.timezone), long=False)}" if v else "never"
            st.html(
                f"<div class='ccard' style='margin-bottom:12px'>{theme.status_chip(c.status, sv.has_unpublished_changes(c))}"
                f"<div class='n'>{escape(c.name)}</div><div class='dt'>{escape(days)}</div>"
                f"<div class='row'><span>Meetings in draft</span><span>{len(c.meetings)}</span></div>"
                f"<div class='row'><span>Required meetings placed</span><span>{req_ok} / {len(req)}</span></div>"
                f"<div class='row'><span>Fixed events</span><span>{len(c.fixed_events)}</span></div>"
                + (f"<div class='row'><span>Drivers / escorts still needed</span><span>▲ {open_c}</span></div>"
                   if open_c else "") +
                f"<div class='row'><span>Blocking issues</span><span>{'✕ ' if n_err else ''}{n_err}</span></div>"
                f"<div class='row'><span>Last published</span><span>{escape(pub)}</span></div></div>"
            )

        group = sv.rsvp_events(search)
        if group:
            st.markdown("#### Sign-ups for talks & meals")
            lis = "".join(
                f"<li><span class='g info' aria-hidden='true'>{len(e.attendees)}</span><span class='m'>"
                f"<span class='h'>{escape(e.title)}</span><br><span class='d'>{escape(e.candidate.name)} · "
                f"{fmt_date(e.start, long=False)}, {fmt_range(e.start, e.end)}</span></span>"
                f"<span class='k'>{len(e.attendees)} faculty</span></li>" for e in group)
            st.html(f"<ul class='att'>{lis}</ul>")
            st.caption("Faculty sign up from their personal link. Full lists are under Itineraries & export → "
                       "Coordinator exports.")
