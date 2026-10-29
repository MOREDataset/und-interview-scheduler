"""Faculty page: reached through a personal link. One screen, three steps:
see the visit dates, paint availability, submit. Published meetings appear below."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape

import streamlit as st

from ...core import exports, models as m
from ...core import services as sv
from ...core.timeutil import fmt_date, fmt_range, fmt_time, time_slots
from .. import theme
from ..components.avail_grid import availability_grid
from ..ctx import write


def _header(settings) -> None:
    st.html(f"<div class='eyebrow'>{escape(settings.org_name)} · {escape(settings.unit_name)}</div>")


def render(ctx, part: m.Participant | None) -> None:
    _header(ctx.settings)
    if part is None:
        st.html("<div class='ph-title'>This link isn't valid</div><p class='ph-sub'>The link may have been replaced "
                "with a new one. Please use the most recent email from the search coordinator, or contact them for a new link.</p>")
        return
    search = part.search
    dates = sv.visit_dates(search)
    open_ = sv.availability_is_open(search)
    if search.is_demo:
        theme.demo_ribbon(True)

    st.html(f"<div class='faculty-hero'><div class='ph-title'>Hello, {escape(part.name)}</div>"
            f"<p class='ph-sub' style='margin:0'>{escape(search.department)} faculty search — please tell us when you "
            f"can meet the candidates. It takes about two minutes.</p></div>")

    # --- what we're asking for ---------------------------------------------
    asks = []
    for c in search.candidates:
        req = next((r for r in c.requirements if r.participant_id == part.id), None)
        if req is None or not c.visit_days:
            continue
        when = " & ".join(fmt_date(d.date, long=False) for d in c.visit_days)
        kind = "requested" if req.required else "optional"
        asks.append(f"<li><b>{escape(c.name)}</b> — visiting {escape(when)}; "
                    f"{req.duration_minutes}-minute one-on-one <span class='small-muted'>({kind})</span></li>")
    deadline = ""
    if search.availability_deadline:
        dl = search.availability_deadline
        deadline = f"{fmt_date(dl)} at {fmt_time(dl)}"
    status_html = (theme.chip("ok", f"Open until {deadline}" if deadline else "Open", "●") if open_
                   else theme.chip("error", "Closed", "✕"))
    sub_html = (theme.chip("published", f"Submitted {_local(part.submitted_at, search)}", "✓") if part.submitted_at
                else theme.chip("warning", "Not submitted yet", "▲"))
    st.html(f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px'>{status_html}{sub_html}</div>"
            + (f"<ul style='margin:6px 0 16px 1.1rem;padding:0;line-height:1.7'>{''.join(asks)}</ul>" if asks else ""))

    if not dates:
        st.info("The visit dates haven't been set yet. Please check back soon.")
        return

    tab_avail, tab_mine = st.tabs(["1 · Your availability", "2 · Your scheduled meetings"])
    with tab_avail:
        _availability(ctx, part, search, dates, open_)
    with tab_mine:
        _my_meetings(ctx, part, search)


def _local(utc_dt: datetime | None, search) -> str:
    if utc_dt is None:
        return ""
    from zoneinfo import ZoneInfo

    loc = utc_dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(search.timezone))
    return f"{fmt_date(loc, long=False)}, {fmt_time(loc)}"


def _availability(ctx, part, search, dates: list[date], open_: bool) -> None:
    step = search.slot_minutes
    key = f"draft_{part.id}"
    saved = {d.isoformat(): v for d, v in sv.participant_cells(part, search).items()}
    if key not in st.session_state or set(st.session_state[key]["cells"]) != set(saved):
        st.session_state[key] = {"rev": 1, "cells": dict(saved)}
    draft = st.session_state[key]

    cols = []
    for d in dates:
        ws, we = sv.day_window(search, d)
        n = len(time_slots(ws, we, step))
        who = [c.name.split(" (")[0] for c in search.candidates if any(v.date == d for v in c.visit_days)]
        cols.append({"key": d.isoformat(), "label": fmt_date(d, long=False), "sub": "Visit: " + ", ".join(who),
                     "startMin": ws.hour * 60 + ws.minute, "step": step, "n": n})

    res = availability_grid(cols, draft["cells"], draft["rev"], key=f"grid_{part.id}", readonly=not open_)
    if res and res.get("rev") == draft["rev"]:
        draft["cells"] = {k: v for k, v in res["cells"].items() if k in draft["cells"]}

    if open_:
        with st.expander("Prefer typing? Add a time range", icon=":material/keyboard:"):
            c1, c2, c3, c4 = st.columns([1.3, 1, 1, 1.1])
            d_sel = c1.selectbox("Date", dates, format_func=lambda d: fmt_date(d, long=False), key="rng_d")
            ws, we = sv.day_window(search, d_sel)
            opts = time_slots(ws, we, step) + [we]
            t0 = c2.selectbox("From", opts[:-1], format_func=fmt_time, key="rng_s")
            t1 = c3.selectbox("To", [t for t in opts if t > t0], format_func=fmt_time, key="rng_e")
            lvl = c4.selectbox("Mark as", ["Available", "Preferred", "Not available"], key="rng_l")
            if st.button("Apply to grid", icon=":material/add:"):
                code = {"Available": "1", "Preferred": "2", "Not available": "0"}[lvl]
                cells = list(draft["cells"][d_sel.isoformat()])
                base = datetime.combine(d_sel, ws)
                i0 = int((datetime.combine(d_sel, t0) - base).total_seconds() // 60 // step)
                i1 = int((datetime.combine(d_sel, t1) - base).total_seconds() // 60 // step)
                for i in range(i0, i1):
                    cells[i] = code
                draft["cells"][d_sel.isoformat()] = "".join(cells)
                draft["rev"] += 1
                st.rerun()

    # --- plain-language summary ---------------------------------------------
    st.markdown("##### What you're telling us")
    lines = []
    for d in dates:
        ws, _ = sv.day_window(search, d)
        lines.append(f"<li><b>{fmt_date(d, long=False)}</b> — {escape(sv.describe_cells(draft['cells'][d.isoformat()], d, ws, step))}</li>")
    st.html(f"<ul style='margin:0 0 12px 1.1rem;padding:0;line-height:1.8'>{''.join(lines)}</ul>")

    changed = draft["cells"] != saved
    if not open_:
        st.warning("Availability collection is closed. If something changed, please contact the search coordinator"
                   + (f" ({search.coordinator_email})." if search.coordinator_email else "."))
        return
    c1, c2 = st.columns([1, 2.2], vertical_alignment="center")
    label = "Update availability" if part.submitted_at else "Submit availability"
    if c1.button(label, type="primary", icon=":material/send:", width="stretch",
                 disabled=not changed and part.submitted_at is not None):
        with write(ctx, "Thank you — your availability is saved."):
            sv.save_availability(ctx.s, part, {date.fromisoformat(k): v for k, v in draft["cells"].items()},
                                 actor=part.email)
            st.session_state.pop(key, None)
    if changed:
        c2.html("<span class='chip warning'>▲ You have unsaved changes</span>")
    elif part.submitted_at:
        c2.html("<span class='small-muted'>Saved. You can return to this link to make changes until the deadline. "
                "No times marked = not available that day.</span>")
    else:
        c2.html("<span class='small-muted'>Not available at all? Just press Submit with nothing marked.</span>")


def _my_meetings(ctx, part, search) -> None:
    items = sv.published_items_for_participant(search, part.id)
    if not items:
        st.html("<p class='small-muted'>Nothing published yet. Once the coordinator publishes the itineraries, "
                "your meetings will appear here.</p>")
        return
    by_day: dict[str, list[dict]] = {}
    for it in items:
        by_day.setdefault(it["start"][:10], []).append(it)
    for dkey, its in sorted(by_day.items()):
        rows = []
        for it in its:
            s, e = datetime.fromisoformat(it["start"]), datetime.fromisoformat(it["end"])
            what = f"Meeting with {it['candidate']}" if it["type"] == "meeting" else f"{it['title']} — {it['candidate']}"
            rows.append(f"<tr><td class='t'>{fmt_range(s, e)}</td><td>{escape(what)}</td><td>{escape(it.get('location') or '—')}</td></tr>")
        st.html(f"<div class='dayh'>{fmt_date(date.fromisoformat(dkey))}</div><table class='itin'><thead><tr><th>Time</th>"
                f"<th>Meeting</th><th>Location</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")
    c1, c2, _ = st.columns([1, 1, 2])
    c1.download_button("Add to calendar (.ics)", exports.participant_ics(part.name, items, search.timezone, search.department),
                       file_name="candidate-meetings.ics", mime="text/calendar", icon=":material/calendar_add_on:")
    c2.download_button("Download PDF", exports.participant_pdf(part.name, items, search.timezone, ctx.settings.org_name,
                                                               ctx.settings.unit_name, search.name, ctx.settings.brand_logo_path),
                       file_name="my-candidate-meetings.pdf", mime="application/pdf", icon=":material/picture_as_pdf:")
