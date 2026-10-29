"""UND Department of Communication — Faculty Interview Scheduler.

Run with:  streamlit run app.py

Three entry points share this one app:
  /?invite=<token>     faculty availability page (personal link)
  /?itinerary=<token>  candidate's read-only published itinerary
  /                    coordinator sign-in and workspace
"""
from __future__ import annotations

import os

import streamlit as st

from scheduler_app.core import models as m
from scheduler_app.core.auth import (
    authenticate, candidate_by_token, make_session_token, participant_by_token, read_session_token, user_by_email,
)
from scheduler_app.core.config import get_settings
from scheduler_app.core.db import get_sessionmaker, init_db
from scheduler_app.core.seed import DEMO_COORDINATOR_EMAIL, seed_demo, sync_demo_coordinator
from scheduler_app.core.services import audit, list_searches
from scheduler_app.ui import theme
from scheduler_app.ui.components.cookie import COOKIE, set_cookie
from scheduler_app.ui.ctx import Ctx, flash

st.set_page_config(page_title="Interview Scheduler · UND Communication", page_icon=":material/event_available:",
                   layout="wide", initial_sidebar_state="expanded")
theme.inject()
settings = get_settings()


@st.cache_resource
def _bootstrap() -> bool:
    """Create tables once per server process; load demo data when enabled and empty."""
    init_db()
    if settings.demo_mode:
        s = get_sessionmaker()()
        try:
            if not list_searches(s):
                seed_demo(s)
            sync_demo_coordinator(s)
            s.commit()
        finally:
            s.close()
    return True


_bootstrap()
s = get_sessionmaker()()
ctx = Ctx(s=s, settings=settings)
qp = st.query_params

try:
    # ------------------------------------------------------------ candidate link
    if "itinerary" in qp:
        from scheduler_app.ui.pages import candidate_view

        cand = candidate_by_token(s, qp.get("itinerary", ""))
        candidate_view.render(ctx, cand)
        st.stop()

    # ------------------------------------------------------------ faculty link
    if "invite" in qp:
        from scheduler_app.ui.pages import faculty

        part = participant_by_token(s, qp.get("invite", ""))
        faculty.render(ctx, part)
        st.stop()

    # ------------------------------------------------------------ coordinator
    uid = st.session_state.get("user_id")
    if uid is None and not st.session_state.get("signed_out"):
        # A signed cookie keeps coordinators signed in across page reloads.
        uid = read_session_token(st.context.cookies.get(COOKIE), settings.secret_key)
        if uid:
            st.session_state["user_id"] = uid
    user = s.get(m.User, uid) if uid else None

    # Optional SSO (configure [auth] in .streamlit/secrets.toml).
    sso_ready = False
    try:
        sso_ready = "auth" in st.secrets
    except Exception:
        sso_ready = False
    if user is None and sso_ready and getattr(st.user, "is_logged_in", False):
        user = user_by_email(s, st.user.get("email", ""))
        if user:
            st.session_state["user_id"] = user.id

    if user is None or user.role != m.ROLE_COORDINATOR or not user.active:
        if st.session_state.pop("clear_cookie", False):
            set_cookie(None)
        left, mid, right = st.columns([1, 1.25, 1])
        with mid:
            st.write("")
            st.html(f"<div class='eyebrow'>{settings.org_name}</div>"
                    f"<div class='ph-title'>Interview Scheduler</div>"
                    f"<p class='ph-sub'>{settings.unit_name} · Coordinator sign-in</p><hr class='ph-rule'>")
            if sso_ready:
                st.button("Sign in with university SSO", on_click=st.login, type="primary", width="stretch")
                if getattr(st.user, "is_logged_in", False):
                    st.warning("Your SSO account is not registered as a coordinator for this app.")
                st.caption("or use a local coordinator account")
            with st.form("login", border=False):
                email = st.text_input("University email", autocomplete="username")
                pw = st.text_input("Password", type="password", autocomplete="current-password")
                go = st.form_submit_button("Sign in", type="primary", width="stretch")
            if go:
                try:
                    u = authenticate(s, email, pw)
                except PermissionError as exc:
                    u = None
                    st.error(str(exc))
                else:
                    if u is None or u.role != m.ROLE_COORDINATOR:
                        st.error("That email and password don't match a coordinator account.")
                if u is not None and u.role == m.ROLE_COORDINATOR:
                    st.session_state["user_id"] = u.id
                    st.session_state.pop("signed_out", None)
                    st.session_state["set_cookie"] = make_session_token(u.id, settings.secret_key)
                    audit(s, None, u.email, "signed_in", f"{u.name} signed in")
                    s.commit()
                    st.rerun()
            st.html("<p class='small-muted' style='margin-top:18px'>Faculty: use the personal link from your email — "
                    "no password needed. Candidates: use the itinerary link you received.</p>")
            if not settings.demo_mode and settings.secret_key in ("dev-only-change-me", "CHANGE_ME"):
                st.error("Administrator: set a real SECRET_KEY in .env before using this app with real data.")
            if settings.demo_mode:
                raw = os.getenv("DEMO_COORDINATOR_PASSWORD")
                if raw is None:
                    where = "No `DEMO_COORDINATOR_PASSWORD` is set, so the password is `demo-coordinator`."
                elif len(raw.strip()) < 8:
                    where = ("`DEMO_COORDINATOR_PASSWORD` is shorter than 8 characters, so it is ignored and the "
                             "password is `demo-coordinator`.")
                else:
                    where = ("The password is the value of `DEMO_COORDINATOR_PASSWORD` from your Secrets / .env "
                             "(not your `SECRET_KEY`).")
                st.info(f"**Demo mode.** Sign in as `{DEMO_COORDINATOR_EMAIL}`. {where}", icon=":material/science:")
        st.stop()

    ctx.user = user
    if st.session_state.get("set_cookie"):
        set_cookie(st.session_state.pop("set_cookie"))
    searches = list_searches(s)
    if not searches:
        from scheduler_app.ui.pages import settings_page

        settings_page.first_run(ctx)
        st.stop()

    theme.sidebar_brand(settings.org_name, settings.unit_name)
    ids = [x.id for x in searches]
    current = st.session_state.get("search_id")
    if current not in ids:
        current = ids[0]
    if len(searches) > 1:
        current = st.sidebar.selectbox("Search", ids, index=ids.index(current),
                                       format_func=lambda i: next(x.name for x in searches if x.id == i))
    st.session_state["search_id"] = current
    ctx.search = s.get(m.Search, current)

    from scheduler_app.ui.pages import (
        assistant, audit_page, availability_admin, candidates, itineraries, overview, people, settings_page, workspace,
    )

    def page(fn, title, icon, path, default=False):
        def runner():
            flash()
            theme.demo_ribbon(ctx.search.is_demo)
            fn(ctx)
        runner.__name__ = f"page_{path}"
        pg = st.Page(runner, title=title, icon=icon, url_path=path, default=default)
        ctx.pages[path] = pg
        return pg

    nav = st.navigation({
        "Coordinate": [
            page(overview.render, "Overview", ":material/space_dashboard:", "overview", default=True),
            page(workspace.render, "Scheduling workspace", ":material/view_timeline:", "workspace"),
            page(assistant.render, "Scheduling assistant", ":material/auto_awesome:", "assistant"),
            page(itineraries.render, "Itineraries & export", ":material/description:", "itineraries"),
        ],
        "Set up": [
            page(candidates.render, "Candidates", ":material/badge:", "candidates"),
            page(people.render, "Participants", ":material/group:", "participants"),
            page(availability_admin.render, "Availability responses", ":material/fact_check:", "responses"),
        ],
        "Admin": [
            page(settings_page.render, "Search settings", ":material/tune:", "settings"),
            page(audit_page.render, "Audit history", ":material/history:", "audit"),
        ],
    })
    with st.sidebar:
        st.divider()
        st.caption(f"Signed in as **{user.name}**")
        if st.button("Sign out", icon=":material/logout:", width="stretch"):
            st.session_state.pop("user_id", None)
            st.session_state["signed_out"] = True
            st.session_state["clear_cookie"] = True
            if sso_ready and getattr(st.user, "is_logged_in", False):
                st.logout()
            st.rerun()
    nav.run()
finally:
    s.close()
