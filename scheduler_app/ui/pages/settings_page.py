"""Search settings, coordinator accounts, and creating new searches."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import streamlit as st
from sqlalchemy import select

from ...core import models as m
from ...core import services as sv
from ...core.auth import create_coordinator, hash_password
from ...core.timeutil import fmt_time, time_slots
from .. import theme
from ..ctx import write

TIMEZONES = ["America/Chicago", "America/Denver", "America/New_York", "America/Los_Angeles"]


def _new_search_form(ctx, key: str) -> None:
    with st.form(key):
        name = st.text_input("Search name", placeholder="e.g. Assistant Professor of Communication, 2026–27")
        dept = st.text_input("Department", ctx.settings.unit_name)
        a, b = st.columns(2)
        coord = a.text_input("Coordinator name (shown to candidates)", ctx.user.name if ctx.user else "")
        email = b.text_input("Coordinator email (shown to candidates)", ctx.user.email if ctx.user else "")
        if st.form_submit_button("Create search", type="primary"):
            if not name.strip():
                st.error("Give the search a name.")
                return
            with write(ctx, "Search created"):
                srch = m.Search(name=name.strip(), department=dept.strip() or ctx.settings.unit_name,
                                timezone=ctx.settings.timezone, coordinator_name=coord or None, coordinator_email=email or None,
                                availability_deadline=datetime.combine(date.today() + timedelta(days=14), time(17, 0)))
                ctx.s.add(srch)
                ctx.s.flush()
                st.session_state["search_id"] = srch.id
                sv.audit(ctx.s, srch.id, ctx.actor, "search_created", f"Created search “{srch.name}”")


def first_run(ctx) -> None:
    theme.page_header("Welcome", "Create your first search",
                      "A search holds the candidates, participants and schedules for one faculty hire.")
    _new_search_form(ctx, "first_search")


def render(ctx) -> None:
    search = ctx.search
    theme.page_header("Admin", "Search settings", "Defaults used everywhere in this search. Changes are recorded in the audit history.")
    t1, t2, t3 = st.tabs(["This search", "Coordinators", "New search"])
    with t1:
        with st.form("search_settings"):
            name = st.text_input("Search name", search.name)
            dept = st.text_input("Department", search.department)
            a, b, c = st.columns(3)
            slot = a.selectbox("Time grid (minutes)", [5, 10, 15, 30], index=[5, 10, 15, 30].index(search.slot_minutes)
                               if search.slot_minutes in (5, 10, 15, 30) else 2,
                               help="Size of each block on the faculty availability grid and the scheduling start-time grid.")
            dur = b.number_input("Default meeting length (minutes)", 5, 240, search.default_meeting_minutes, step=5)
            buf = c.number_input("Buffer between items (minutes)", 0, 60, search.buffer_minutes, step=5,
                                 help="Transition time the scheduling assistant keeps free between meetings and events.")
            d1, d2, d3 = st.columns(3)
            dl = search.availability_deadline or datetime.combine(date.today() + timedelta(days=7), time(17, 0))
            dd = d1.date_input("Availability deadline", dl.date())
            times = time_slots(time(0, 0), time(23, 59), 15)
            dt = d2.selectbox("Deadline time", times, index=times.index(dl.time()) if dl.time() in times else 68,
                              format_func=fmt_time)
            tz = d3.selectbox("Time zone", TIMEZONES, index=TIMEZONES.index(search.timezone) if search.timezone in TIMEZONES else 0)
            open_ = st.checkbox("Availability collection open", search.availability_open)
            e1, e2 = st.columns(2)
            cn = e1.text_input("Coordinator name (shown to candidates)", search.coordinator_name or "")
            ce = e2.text_input("Coordinator email (shown to candidates)", search.coordinator_email or "")
            if st.form_submit_button("Save settings", type="primary"):
                with write(ctx, "Settings saved"):
                    if slot != search.slot_minutes and any(p.availability for p in search.participants):
                        st.session_state["_flash"] = ("Settings saved. Note: the grid size changed; existing availability "
                                                      "is kept but may be shown rounded on the grid.")
                    search.name, search.department = name.strip(), dept.strip()
                    search.slot_minutes, search.default_meeting_minutes, search.buffer_minutes = int(slot), int(dur), int(buf)
                    search.availability_deadline = datetime.combine(dd, dt)
                    search.timezone, search.availability_open = tz, open_
                    search.coordinator_name, search.coordinator_email = cn or None, ce or None
                    sv.audit(ctx.s, search.id, ctx.actor, "settings_saved", "Updated search settings")
        st.html("<p class='small-muted'><b>Branding:</b> official UND marks are not bundled. To add an authorized logo "
                "to PDFs, set <span class='kbd'>BRAND_LOGO_PATH</span> in your .env to a PNG supplied by University "
                "Marketing.</p>")
        if search.is_demo:
            with st.expander("Delete the demo search", icon=":material/delete:"):
                st.caption("Removes all fictional demo data. Set DEMO_MODE=false in .env so it isn't recreated.")
                if st.button("Delete demo data", type="primary"):
                    with write(ctx, "Demo search deleted"):
                        sv.audit(ctx.s, None, ctx.actor, "search_deleted", f"Deleted demo search “{search.name}”")
                        ctx.s.delete(search)
                        st.session_state.pop("search_id", None)
    with t2:
        users = list(ctx.s.scalars(select(m.User).where(m.User.role == m.ROLE_COORDINATOR).order_by(m.User.name)))
        for u in users:
            a, b = st.columns([3, 1], vertical_alignment="center")
            a.html(f"<b>{u.name}</b> <span class='small-muted'>{u.email}{' · inactive' if not u.active else ''}</span>")
            if u.id != ctx.user.id and b.button("Deactivate" if u.active else "Reactivate", key=f"u_{u.id}"):
                with write(ctx, "Account updated"):
                    u.active = not u.active
                    sv.audit(ctx.s, None, ctx.actor, "user_toggled", f"{'Reactivated' if u.active else 'Deactivated'} {u.email}")
        with st.expander("Add a coordinator", icon=":material/person_add:"):
            with st.form("add_coord"):
                n = st.text_input("Name")
                e = st.text_input("University email")
                pw = st.text_input("Temporary password (min. 8 characters)", type="password")
                if st.form_submit_button("Add coordinator", type="primary"):
                    with write(ctx, "Coordinator added"):
                        if ctx.s.scalar(select(m.User).where(m.User.email == e.strip().lower())):
                            raise sv.ValidationError("That email already has an account.")
                        try:
                            create_coordinator(ctx.s, e, n, pw)
                        except ValueError as exc:
                            raise sv.ValidationError(str(exc)) from exc
                        sv.audit(ctx.s, None, ctx.actor, "user_created", f"Added coordinator {e}")
        with st.expander("Change my password", icon=":material/key:"):
            with st.form("pw"):
                p1 = st.text_input("New password", type="password")
                p2 = st.text_input("Repeat new password", type="password")
                if st.form_submit_button("Change password"):
                    with write(ctx, "Password changed"):
                        if p1 != p2:
                            raise sv.ValidationError("The passwords don't match.")
                        try:
                            ctx.user.password_hash = hash_password(p1)
                        except ValueError as exc:
                            raise sv.ValidationError(str(exc)) from exc
                        sv.audit(ctx.s, None, ctx.actor, "password_changed", "Changed own password")
    with t3:
        st.caption("Start a separate search (for example, next year's hire). Candidates and participants are added afterwards.")
        _new_search_form(ctx, "new_search")
