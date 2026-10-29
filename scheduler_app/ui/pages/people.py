"""Participant directory, CSV import and meeting locations."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core import models as m
from ...core import services as sv
from ...core.notify import invite_link
from .. import theme
from ..ctx import write

CSV_TEMPLATE = (
    "name,email,role,location,duration,required_for,optional_for\n"
    "Example Person,example.person@example.edu,Associate Professor,Room name,30,all,\n"
)


def render(ctx) -> None:
    search = ctx.search
    theme.page_header("Set up", "Participants",
                      "Faculty, administrators and other university representatives. Each person gets a private link "
                      "for submitting availability — they never see anyone else's.")
    t1, t2, t3 = st.tabs(["Directory", "Import from CSV", "Meeting locations"])
    with t1:
        _directory(ctx)
    with t2:
        _import(ctx)
    with t3:
        _locations(ctx)


def _directory(ctx) -> None:
    search = ctx.search
    loc = {l.id: l.name for l in search.locations}
    rows = []
    for p in search.participants:
        meets = []
        for c in search.candidates:
            r = next((r for r in c.requirements if r.participant_id == p.id), None)
            if r:
                meets.append(f"{c.name.split(' (')[0]}: {'required' if r.required else 'optional'}")
        rows.append({"id": p.id, "Name": p.name, "Email": p.email or "—", "Role": p.role_title or "",
                     "Office / meeting place": loc.get(p.location_id, ""), "Usual length": p.preferred_duration,
                     "Meets": "; ".join(meets) or "—", "Availability": "✓ Submitted" if p.submitted_at else "▲ Missing",
                     "Active": p.active})
    q = st.text_input("Filter", placeholder="Search by name, role or email", label_visibility="collapsed")
    df = pd.DataFrame(rows)
    if q and not df.empty:
        mask = df[["Name", "Email", "Role"]].apply(lambda col: col.str.contains(q, case=False, na=False)).any(axis=1)
        df = df[mask]
    if df.empty:
        st.info("No participants yet. Add someone below or import a CSV.")
    else:
        ev = st.dataframe(df.drop(columns=["id"]), hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key="ppl_tbl", column_config={
                              "Usual length": st.column_config.NumberColumn(format="%d min"),
                              "Active": st.column_config.CheckboxColumn(),
                          })
        sel = ev.selection.rows if ev and ev.selection else []
        if sel:
            pid = int(df.iloc[sel[0]]["id"])
            p = ctx.s.get(m.Participant, pid)
            _edit(ctx, p)
            return
        st.caption("Select a row to edit, copy their link, or remove them.")
    with st.expander("Add a participant", icon=":material/person_add:"):
        _form(ctx, None)


def _form(ctx, p: m.Participant | None) -> None:
    search = ctx.search
    locs = [None] + [l.id for l in search.locations]
    lname = {l.id: l.name for l in search.locations}
    with st.form(f"pf_{p.id if p else 'new'}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", p.name if p else "")
        email = c2.text_input("Email (optional)", (p.email or "") if p else "",
                              help="Leave blank for guests without a university email; you can copy their link instead.")
        c3, c4, c5 = st.columns([1.4, 1.2, 0.8])
        role = c3.text_input("Departmental role", (p.role_title or "") if p else "")
        loc = c4.selectbox("Office / meeting place", locs, index=locs.index(p.location_id) if p and p.location_id in locs else 0,
                           format_func=lambda i: "—" if i is None else lname[i])
        dur = c5.number_input("Usual meeting (min)", 5, 240, (p.preferred_duration or search.default_meeting_minutes) if p
                              else search.default_meeting_minutes, step=5)
        active = st.checkbox("Active (include in this search)", p.active if p else True)
        add_all = False
        if p is None:
            add_all = st.checkbox("Add as a *required* meeting for every candidate", value=True)
        if st.form_submit_button("Save", type="primary"):
            with write(ctx, "Participant saved"):
                np_ = sv.save_participant(ctx.s, search, ctx.actor, participant=p, name=name, email=email, role_title=role,
                                          location_id=loc, preferred_duration=int(dur), active=active)
                if p is None:
                    for c in search.candidates:
                        sv.upsert_requirement(ctx.s, c, np_, add_all, int(dur))


def _edit(ctx, p: m.Participant) -> None:
    st.markdown(f"#### {p.name}")
    _form(ctx, p)
    st.markdown("**Personal link** — availability, sign-ups for talks and meals, and their published schedule")
    st.code(invite_link(ctx.settings, p.access_token), language=None)
    st.caption("Anyone with this link can answer for this person, so send it only to them.")
    c1, c2 = st.columns(2)
    if c1.button("Issue a new link", icon=":material/key:", help="The old link stops working immediately."):
        with write(ctx, "New link issued"):
            sv.rotate_participant_link(ctx.s, p, ctx.actor)
    if c2.button("Remove participant", icon=":material/delete:"):
        with write(ctx, "Participant removed"):
            sv.delete_participant(ctx.s, p, ctx.actor)


def _import(ctx) -> None:
    st.markdown("Upload a CSV with one person per row. Columns: **name** (required), **email**, and optionally "
                "**role**, **location**, **duration** (minutes), **required_for**, **optional_for** "
                "(candidate names separated by `;`, or `all`). People already in the list are updated, not "
                "duplicated (matched by email, or by name when there is no email).")
    st.download_button("Download a template", CSV_TEMPLATE.encode(), "participants-template.csv", "text/csv",
                       icon=":material/download:")
    up = st.file_uploader("CSV file", type=["csv"])
    if up is not None:
        try:
            preview = pd.read_csv(up)
            st.dataframe(preview.head(50), hide_index=True, width="stretch")
        except Exception as exc:  # pragma: no cover - user file problems
            st.error(f"Couldn't read that file: {exc}")
            return
        if st.button("Import these participants", type="primary"):
            up.seek(0)
            rep = None
            with write(ctx, None, rerun=False):
                rep = sv.import_participants_csv(ctx.s, ctx.search, up.read(), ctx.actor)
            if rep:
                st.success(f"Imported: {rep.created} new, {rep.updated} updated.")
                for e in rep.errors:
                    st.warning(e)


def _locations(ctx) -> None:
    st.caption("Rooms and offices used on itineraries. Use the real room names your department provides.")
    search = ctx.search
    df = pd.DataFrame([{"id": l.id, "Name": l.name, "Details": l.details or ""} for l in search.locations],
                      columns=["id", "Name", "Details"])
    ed = st.data_editor(df, num_rows="dynamic", hide_index=True, width="stretch", key="locs",
                        column_config={"id": None, "Name": st.column_config.TextColumn(required=True)})
    if st.button("Save locations", type="primary"):
        with write(ctx, "Locations saved"):
            keep = set()
            for _, r in ed.iterrows():
                if not str(r["Name"] or "").strip():
                    continue
                if pd.notna(r.get("id")):
                    l = ctx.s.get(m.Location, int(r["id"]))
                    l.name, l.details = r["Name"].strip(), (r["Details"] or None)
                    keep.add(l.id)
                else:
                    l = m.Location(search_id=search.id, name=r["Name"].strip(), details=r["Details"] or None)
                    ctx.s.add(l)
                    ctx.s.flush()
                    keep.add(l.id)
            for l in list(search.locations):
                if l.id not in keep:
                    in_use = any(x.location_id == l.id for c in search.candidates for x in list(c.meetings) + list(c.fixed_events)) \
                        or any(p.location_id == l.id for p in search.participants)
                    if in_use:
                        raise sv.ValidationError(f"“{l.name}” is still used by a meeting, event or participant.")
                    ctx.s.delete(l)
            sv.audit(ctx.s, search.id, ctx.actor, "locations_updated", "Updated meeting locations")
