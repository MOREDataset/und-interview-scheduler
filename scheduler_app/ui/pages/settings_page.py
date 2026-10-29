"""Search settings, branding, templates, coordinator accounts, and new searches."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from html import escape

import streamlit as st
from sqlalchemy import select

from ...core import models as m
from ...core import services as sv
from ...core.auth import create_coordinator, hash_password
from ...core.seed import DEMO_COORDINATOR_EMAIL
from ...core.branding import data_uri, load_brand, prepare_logo
from ...core.templates import export_template, import_template
from ...core.timeutil import fmt_time, time_slots
from .. import theme
from ..ctx import write

TIMEZONES = ["America/Chicago", "America/Denver", "America/New_York", "America/Los_Angeles"]
BRAND_URL = "https://campus.und.edu/brand/downloads.html"
STEP_OPTIONS = [None, 5, 10, 15, 30]


def _new_search_form(ctx, key: str) -> None:
    with st.form(key):
        name = st.text_input("Search name", placeholder="e.g. Assistant Professor of Communication, 2026–27")
        dept = st.text_input("Department", ctx.settings.unit_name)
        a, b = st.columns(2)
        coord = a.text_input("Coordinator name (shown to candidates)", ctx.user.name if ctx.user else "")
        email = b.text_input("Coordinator email (shown to candidates)", ctx.user.email if ctx.user else "")
        if st.form_submit_button("Create blank search", type="primary"):
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


def _import_form(ctx, key: str) -> None:
    st.html("<p class='small-muted' style='margin:0 0 6px'>A template file (.json) holds a whole setup: rooms, people, "
            "candidates, visit days, events and who meets whom. Importing always creates a <b>new</b> search; nothing "
            "you already have is changed.</p>")
    up = st.file_uploader("Template file", type=["json"], key=f"{key}_file")
    if up is not None and st.button("Create search from this file", type="primary", key=f"{key}_go",
                                    icon=":material/upload_file:"):
        with write(ctx, "Search created from the template"):
            rep = import_template(ctx.s, up.getvalue(), ctx.actor)
            st.session_state["search_id"] = rep.search.id
            st.session_state["_import_report"] = {"name": rep.search.name, "counts": rep.counts, "warnings": rep.warnings}


def _import_report() -> None:
    rep = st.session_state.pop("_import_report", None)
    if not rep:
        return
    counts = ", ".join(f"{v} {k}" for k, v in rep["counts"].items() if v)
    st.success(f"Created **{rep['name']}** — {counts}. It is now the selected search (see the menu on the left).",
               icon=":material/task_alt:")
    for w in rep["warnings"]:
        st.warning(w)


def first_run(ctx) -> None:
    theme.page_header("Welcome", "Create your first search",
                      "A search holds the candidates, participants and schedules for one faculty hire.")
    a, b = st.columns(2, gap="large")
    with a:
        st.markdown("#### Start blank")
        _new_search_form(ctx, "first_search")
    with b:
        st.markdown("#### Or load a prepared file")
        _import_form(ctx, "first_import")


def render(ctx) -> None:
    theme.page_header("Admin", "Search settings",
                      "Defaults used everywhere in this search, the logo, and saving or loading whole setups. "
                      "Changes are recorded in the audit history.")
    _import_report()
    t1, t2, t3, t4, t5 = st.tabs(["This search", "Logo", "Templates", "Coordinators", "New search"])
    with t1:
        _this_search(ctx)
    with t2:
        _branding(ctx)
    with t3:
        _templates(ctx)
    with t4:
        _coordinators(ctx)
    with t5:
        a, b = st.columns(2, gap="large")
        with a:
            st.markdown("#### Start blank")
            st.caption("For example, next year's hire. Candidates and participants are added afterwards.")
            _new_search_form(ctx, "new_search")
        with b:
            st.markdown("#### Start from a template file")
            _import_form(ctx, "new_import")


def _this_search(ctx) -> None:
    search = ctx.search
    with st.form("search_settings"):
        name = st.text_input("Search name", search.name)
        dept = st.text_input("Department", search.department)
        a, b, c = st.columns(3)
        slot = a.selectbox("Availability grid (minutes)", [5, 10, 15, 30],
                           index=[5, 10, 15, 30].index(search.slot_minutes) if search.slot_minutes in (5, 10, 15, 30) else 2,
                           help="Size of each block faculty paint on their availability grid.")
        step = b.selectbox("Meetings may start every", STEP_OPTIONS,
                           index=STEP_OPTIONS.index(search.start_step_minutes)
                           if search.start_step_minutes in STEP_OPTIONS else 0,
                           format_func=lambda v: "Same as the grid" if v is None else f"{v} minutes",
                           help="Start times the scheduling assistant may use. 10 minutes lets meetings start at "
                                "9:40, 10:10 and so on, like a hand-made itinerary.")
        dur = c.number_input("Default meeting length (minutes)", 5, 240, search.default_meeting_minutes, step=5)
        d1, d2 = st.columns(2)
        buf = d1.number_input("Buffer between items (minutes)", 0, 60, search.buffer_minutes, step=5,
                              help="Transition time the scheduling assistant keeps free between meetings and events.")
        loc_ids = [None] + [l.id for l in search.locations]
        lname = {l.id: l.name for l in search.locations}
        room = d2.selectbox("Default meeting room", loc_ids,
                            index=loc_ids.index(search.default_location_id) if search.default_location_id in loc_ids else 0,
                            format_func=lambda i: "None (use each person's office)" if i is None else lname[i],
                            help="Used for one-on-ones when neither the meeting nor the person has a room. "
                                 "Rooms are listed under Participants → Meeting locations.")
        e1, e2, e3 = st.columns(3)
        dl = search.availability_deadline or datetime.combine(date.today() + timedelta(days=7), time(17, 0))
        dd = e1.date_input("Availability deadline", dl.date())
        times = time_slots(time(0, 0), time(23, 59), 15)
        dt = e2.selectbox("Deadline time", times, index=times.index(dl.time()) if dl.time() in times else 68,
                          format_func=fmt_time)
        tz = e3.selectbox("Time zone", TIMEZONES,
                          index=TIMEZONES.index(search.timezone) if search.timezone in TIMEZONES else 0)
        open_ = st.checkbox("Availability collection open", search.availability_open)
        f1, f2 = st.columns(2)
        cn = f1.text_input("Coordinator name (shown to candidates)", search.coordinator_name or "")
        ce = f2.text_input("Coordinator email (shown to candidates)", search.coordinator_email or "")
        if st.form_submit_button("Save settings", type="primary"):
            with write(ctx, "Settings saved"):
                if slot != search.slot_minutes and any(p.availability for p in search.participants):
                    st.session_state["_flash"] = ("Settings saved. Note: the grid size changed; existing availability "
                                                  "is kept but may be shown rounded on the grid.")
                search.name, search.department = name.strip(), dept.strip()
                search.slot_minutes, search.default_meeting_minutes, search.buffer_minutes = int(slot), int(dur), int(buf)
                search.start_step_minutes = step
                search.default_location_id = room
                search.availability_deadline = datetime.combine(dd, dt)
                search.timezone, search.availability_open = tz, open_
                search.coordinator_name, search.coordinator_email = cn or None, ce or None
                sv.audit(ctx.s, search.id, ctx.actor, "settings_saved", "Updated search settings")
    if search.is_demo:
        with st.expander("Delete the demo search", icon=":material/delete:"):
            st.caption("Removes all fictional demo data. Set DEMO_MODE=false so it isn't recreated on an empty database.")
            if st.button("Delete demo data", type="primary"):
                with write(ctx, "Demo search deleted"):
                    sv.audit(ctx.s, None, ctx.actor, "search_deleted", f"Deleted demo search “{search.name}”")
                    ctx.s.delete(search)
                    st.session_state.pop("search_id", None)


def _branding(ctx) -> None:
    brand = load_brand(ctx.s)
    st.html(
        "<p class='ph-sub' style='margin-bottom:8px'>The logo appears in the sidebar, on the sign-in page, on faculty and "
        "candidate pages, and at the top of every PDF itinerary. It is stored in the app's database, so it stays after "
        "redeploys and applies to every search.</p>"
        f"<p class='small-muted'>Get the official files from the <a href='{BRAND_URL}' target='_blank'>UND brand "
        "downloads page</a> (look for the <b>Digital PNG</b> versions), and follow the university's logo guidelines. "
        "Departments usually ask University Marketing for a department lockup.</p>")
    a, b = st.columns(2, gap="large")
    with a:
        _logo_slot(ctx, sv.LOGO_LIGHT, brand.light, "Logo for white backgrounds",
                   "Full-color version. Used on PDFs, itineraries and the sign-in page.", "#FFFFFF")
    with b:
        _logo_slot(ctx, sv.LOGO_DARK, brand.dark, "Logo for the dark sidebar (optional)",
                   "White / reverse version. Without it, the color logo is shown on a white tile.", "#10261D")


def _logo_slot(ctx, key: str, current: bytes | None, title: str, help_: str, bg: str) -> None:
    st.markdown(f"##### {title}")
    st.caption(help_)
    if current:
        st.html(f"<div style='background:{bg};border:1px solid var(--rule);border-radius:8px;padding:18px;"
                f"display:flex;align-items:center;justify-content:center;min-height:96px'>"
                f"<img src='{data_uri(current)}' alt='Current logo' style='max-height:70px;max-width:100%'></div>")
    else:
        st.html(f"<div style='background:{bg};border:1px dashed #B9B5A8;border-radius:8px;padding:18px;min-height:96px;"
                f"display:flex;align-items:center;justify-content:center;color:#8A938E;font-size:13px'>No logo yet</div>")
    n = st.session_state.get(f"_up_{key}", 0)
    up = st.file_uploader("PNG or JPG", type=["png", "jpg", "jpeg"], key=f"logo_{key}_{n}", label_visibility="collapsed")
    c1, c2 = st.columns(2)
    if up is not None and c1.button("Use this logo", key=f"save_{key}", type="primary", width="stretch"):
        with write(ctx, "Logo saved"):
            sv.set_setting_blob(ctx.s, key, prepare_logo(up.getvalue()), ctx.actor)
            st.session_state[f"_up_{key}"] = n + 1
    if current and c2.button("Remove", key=f"rm_{key}", width="stretch"):
        with write(ctx, "Logo removed"):
            sv.set_setting_blob(ctx.s, key, None, ctx.actor)


def _templates(ctx) -> None:
    search = ctx.search
    st.markdown("#### Save this search as a template")
    st.caption("Downloads the setup — rooms, people, candidates, visit days, events, meeting lists and placed meetings — "
               "as one file. It does not include availability, personal links or the audit history. It does include "
               "names and any emails, so store it privately.")
    slug = re.sub(r"[^a-z0-9]+", "-", search.name.lower()).strip("-") or "search"
    st.download_button("Download template (.json)",
                       json.dumps(export_template(search), indent=2, ensure_ascii=False).encode("utf-8"),
                       file_name=f"{slug}.json", mime="application/json", icon=":material/download:")
    st.markdown("#### Load a template")
    _import_form(ctx, "tab_import")
    st.html("<p class='small-muted'>Tip: to reuse this year's structure next year, download the template, change the "
            "dates and names in the file (or after importing), and load it as a new search.</p>")


def _coordinators(ctx) -> None:
    users = list(ctx.s.scalars(select(m.User).where(m.User.role == m.ROLE_COORDINATOR).order_by(m.User.name)))
    for u in users:
        a, b = st.columns([3, 1], vertical_alignment="center")
        a.html(f"<b>{escape(u.name)}</b> <span class='small-muted'>{escape(u.email)}"
               f"{' · inactive' if not u.active else ''}</span>")
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
        if ctx.settings.demo_mode and ctx.user.email == DEMO_COORDINATOR_EMAIL:
            st.caption("This is the demo account: its password comes from DEMO_COORDINATOR_PASSWORD and is reset to "
                       "that value whenever the app restarts. Add your own coordinator account above instead.")
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
