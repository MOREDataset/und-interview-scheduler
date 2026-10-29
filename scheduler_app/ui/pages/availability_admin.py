"""Availability response tracking, reminders and coordinator-side review."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ...core import models as m
from ...core import notify
from ...core import services as sv
from ...core.timeutil import fmt_date, fmt_time, time_slots
from .. import theme
from ..components.avail_grid import availability_grid
from ..ctx import write


def _local(ts: datetime | None, tz: str) -> str:
    if not ts:
        return "—"
    x = ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz))
    return f"{fmt_date(x, long=False)} {fmt_time(x)}"


def render(ctx) -> None:
    search = ctx.search
    open_ = sv.availability_is_open(search)
    theme.page_header("Set up", "Availability responses",
                      "Who has answered, which dates they covered, and reminders for the rest. "
                      "Faculty can change their answers until collection closes.")
    c1, c2 = st.columns([3, 1], vertical_alignment="center")
    dl = search.availability_deadline
    c1.html(("<span class='chip ok'>● Collection open</span>" if open_ else "<span class='chip error'>✕ Collection closed</span>")
            + (f" <span class='small-muted'>Deadline: {fmt_date(dl, long=False)}, {fmt_time(dl)}</span>" if dl else ""))
    if c2.button("Close collection" if search.availability_open else "Reopen collection", width="stretch"):
        with write(ctx, "Updated"):
            search.availability_open = not search.availability_open
            sv.audit(ctx.s, search.id, ctx.actor, "collection_toggled",
                     f"Availability collection {'reopened' if search.availability_open else 'closed'}")

    dates = sv.visit_dates(search)
    invited = sv.invited_participants(search)
    left_out = [p for p in search.participants if p.active and p not in invited]
    rows = []
    for p in invited:
        covered = sorted(r.date for r in p.responses if r.date in dates)
        n_meet = sum(1 for c in search.candidates for x in c.meetings if x.participant_id == p.id)
        rows.append({"id": p.id, "Participant": p.name, "Status": "✓ Submitted" if p.submitted_at else "▲ Not submitted",
                     "Last submission": _local(p.submitted_at, search.timezone),
                     "Dates covered": f"{len(covered)} / {len(dates)}",
                     "Scheduled": n_meet, "Last contacted": _local(p.last_reminded_at, search.timezone),
                     "Free time": " · ".join(f"{fmt_date(d, long=False)}: {sv.free_windows_text(p, d)}" for d in dates)
                     if p.submitted_at else "—"})
    df = pd.DataFrame(rows)
    flt = st.segmented_control("Show", ["Everyone", "Not submitted", "Submitted"], default="Everyone",
                               label_visibility="collapsed")
    view = df
    if flt == "Not submitted" and not df.empty:
        view = df[df["Status"].str.contains("Not")]
    elif flt == "Submitted" and not df.empty:
        view = df[~df["Status"].str.contains("Not")]
    if left_out:
        st.caption(f"Not asked for availability because they aren't on any candidate's meeting list: "
                   f"{', '.join(p.name for p in left_out)}. Add them under Candidates → Who meets whom if needed.")
    if view.empty:
        st.info("Nobody in this list." if invited else
                "Nobody is on a meeting list yet. Choose who meets whom on the Candidates page first.")
    else:
        ev = st.dataframe(view.drop(columns=["id"]), hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key="resp_tbl")
        sel = ev.selection.rows if ev and ev.selection else []
        if sel:
            _review(ctx, ctx.s.get(m.Participant, int(view.iloc[sel[0]]["id"])))
        else:
            st.caption("Select a row to see that person's availability grid.")

    st.markdown("#### Invitations & reminders")
    missing = [p for p in invited if not p.submitted_at]
    if not missing:
        if invited:
            st.success("Everyone has submitted availability.", icon=":material/check_circle:")
        return
    st.caption("People who haven't been contacted yet get an invitation; everyone else gets a reminder. "
               "Each message contains that person's own link.")
    pick = st.multiselect("Who should get a message?", [p.id for p in missing], default=[p.id for p in missing],
                          format_func=lambda i: next(f"{p.name} ({'reminder' if p.last_reminded_at else 'invitation'})"
                                                     for p in missing if p.id == i))
    chosen = [p for p in missing if p.id in pick]
    drafts = [(p, notify.availability_request(ctx.settings, search, p, reminder=p.last_reminded_at is not None))
              for p in chosen if p.email]
    no_email = [p for p in chosen if not p.email]
    if no_email:
        st.html("<p class='small-muted' style='margin:6px 0 2px'>No email address on file — copy the link and send it "
                "however you usually reach them:</p>")
        for p in no_email:
            st.code(notify.invite_link(ctx.settings, p.access_token), language=None)
            st.caption(f"↑ {p.name}")
        if st.button(f"Mark {'them' if len(no_email) > 1 else 'this person'} as contacted", icon=":material/done_all:",
                     key="mark_noemail"):
            with write(ctx, "Marked as contacted"):
                for p in no_email:
                    p.last_reminded_at = m.utcnow()
    if not drafts:
        return
    with st.expander(f"Preview the message ({len(drafts)} recipient{'s' if len(drafts) != 1 else ''})", expanded=False):
        st.text(f"To: {drafts[0][1].to}\nSubject: {drafts[0][1].subject}\n\n{drafts[0][1].body}")
    if ctx.settings.email_enabled:
        if st.button(f"Send {len(drafts)} email(s)", type="primary", icon=":material/send:"):
            sent, failed = 0, []
            for p, d in drafts:
                try:
                    notify.send(ctx.settings, d)
                    p.last_reminded_at = m.utcnow()
                    sent += 1
                except Exception as exc:  # pragma: no cover - network
                    failed.append(f"{p.name}: {exc}")
            with write(ctx, f"Sent {sent} email(s)", rerun=not failed):
                sv.audit(ctx.s, search.id, ctx.actor, "reminders_sent", f"Sent {sent} availability email(s)")
            for f in failed:
                st.error(f)
    else:
        st.html("<p class='small-muted'>Email sending isn't configured, so nothing is sent from the app. Use the links "
                "below to open each message in your own email program (the personal link is already included).</p>")
        links = " ".join(f"<a href='{escape(d.mailto)}' style='margin-right:14px'>✉ {escape(p.name)}</a>" for p, d in drafts)
        st.html(f"<div style='line-height:2'>{links}</div>")
        st.caption("After sending, mark them as contacted so the next message is worded as a reminder.")
        if st.button("Mark these as contacted", icon=":material/done_all:"):
            with write(ctx, "Marked as contacted"):
                for p, _ in drafts:
                    p.last_reminded_at = m.utcnow()
                sv.audit(ctx.s, search.id, ctx.actor, "reminders_prepared",
                         f"Prepared {len(drafts)} availability message(s)")


def _review(ctx, p: m.Participant) -> None:
    search = ctx.search
    st.markdown(f"#### {escape(p.name)}")
    dates = sv.visit_dates(search)
    cells = {d.isoformat(): v for d, v in sv.participant_cells(p, search).items()}
    edit = st.toggle("Enter availability on their behalf (e.g. they replied by email)", key=f"obo_{p.id}")
    cols = []
    for d in dates:
        ws, we = sv.day_window(search, d)
        cols.append({"key": d.isoformat(), "label": fmt_date(d, long=False), "sub": "", "startMin": ws.hour * 60 + ws.minute,
                     "step": search.slot_minutes, "n": len(time_slots(ws, we, search.slot_minutes))})
    k = f"obo_draft_{p.id}"
    if k not in st.session_state:
        st.session_state[k] = {"rev": 1, "cells": cells}
    dr = st.session_state[k]
    res = availability_grid(cols, dr["cells"] if edit else cells, dr["rev"] if edit else 0, key=f"rv_{p.id}_{int(edit)}",
                            readonly=not edit)
    if edit and res and res.get("rev") == dr["rev"]:
        dr["cells"] = res["cells"]
    if edit and st.button("Save on their behalf", type="primary"):
        with write(ctx, f"Saved availability for {p.name}"):
            sv.save_availability(ctx.s, p, {date.fromisoformat(a): b for a, b in dr["cells"].items()},
                                 actor=f"{ctx.actor} (on behalf of {p.email or p.name})", coordinator_override=True)
            st.session_state.pop(k, None)
