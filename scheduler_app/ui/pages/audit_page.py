"""Audit history: who did what, when."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy import or_, select

from ...core import models as m
from .. import theme


def render(ctx) -> None:
    search = ctx.search
    theme.page_header("Admin", "Audit history",
                      "Availability submissions, schedule changes, overrides, publications and sign-ins. Entries cannot be edited.")
    rows = ctx.s.scalars(select(m.AuditLog).where(or_(m.AuditLog.search_id == search.id, m.AuditLog.search_id.is_(None)))
                         .order_by(m.AuditLog.ts.desc()).limit(2000)).all()
    tz = ZoneInfo(search.timezone)
    df = pd.DataFrame([{"When": r.ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                        "Who": r.actor, "Action": r.action.replace("_", " "), "Details": r.summary} for r in rows])
    if df.empty:
        st.info("No activity recorded yet.")
        return
    a, b = st.columns([1, 2])
    kinds = sorted(df["Action"].unique())
    pick = a.multiselect("Action", kinds, placeholder="All actions")
    q = b.text_input("Search", placeholder="Name, email or text")
    view = df
    if pick:
        view = view[view["Action"].isin(pick)]
    if q:
        view = view[view.apply(lambda r: q.lower() in (r["Who"] + r["Details"]).lower(), axis=1)]
    st.dataframe(view, hide_index=True, width="stretch", height=560)
    st.download_button("Download as CSV", view.to_csv(index=False).encode("utf-8-sig"), "audit-history.csv", "text/csv")
