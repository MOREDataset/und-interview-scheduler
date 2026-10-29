"""Automatic scheduling assistant: propose → review → apply."""
from __future__ import annotations

import hashlib
from datetime import datetime
from html import escape

import streamlit as st

from ...core import services as sv
from ...core.problem import load_problem
from ...core.scheduler import MODES, OBJECTIVES, propose
from ...core.timeutil import fmt_date, fmt_range
from .. import theme
from ..components.timeline import timeline
from ..ctx import write

OBJ_HELP = {
    "required": "Fill the itinerary with as many required (then optional) meetings as possible.",
    "compact": "Same priorities, but pack meetings together so candidates have fewer idle gaps.",
    "preferred": "Same priorities, but lean strongly toward times faculty marked as preferred.",
}


def _fingerprint(prob) -> str:
    """Changes whenever meetings, events or availability change — used to refuse stale proposals."""
    parts = [f"m{x.id}:{x.start}:{x.end}:{x.locked}" for x in sorted(prob.meetings, key=lambda x: x.id or 0)]
    parts += [f"e{e.id}:{e.start}:{e.end}" for e in prob.events]
    parts += [f"p{p.id}:{p.free}" for p in prob.participants.values()]
    parts += [f"r{r.id}:{r.required}:{r.duration}" for r in prob.requirements]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def render(ctx) -> None:
    search = ctx.search
    theme.page_header("Coordinate", "Scheduling assistant",
                      "Proposes conflict-free meetings from submitted availability. You review the proposal before "
                      "anything changes. It never assumes someone is free, never moves locked meetings or fixed events, "
                      "and never touches a published itinerary — published versions change only when you republish.")
    cands = {c.id: c for c in search.candidates}
    if not cands:
        st.info("Add candidates first.")
        return

    with st.container(border=True):
        a, b = st.columns([1, 1], gap="large")
        with a:
            targets = st.multiselect("Candidates to schedule", list(cands), default=list(cands),
                                     format_func=lambda i: cands[i].name)
            objective = st.radio("Priority", list(OBJECTIVES), format_func=lambda k: OBJECTIVES[k],
                                 captions=[OBJ_HELP[k] for k in OBJECTIVES])
        with b:
            mode = st.radio("What to do with meetings already in the draft", list(MODES), format_func=lambda k: MODES[k])
            st.html(f"<p class='small-muted'>Rules in force: meetings start every "
                    f"{search.start_step_minutes or search.slot_minutes} minutes · "
                    f"{search.buffer_minutes}-minute buffer between items · interview hours per visit day · "
                    "no double-booking of candidates or faculty across all candidates.</p>")
            limit = st.slider("Maximum thinking time (seconds)", 2, 60, 10)

    prob = load_problem(ctx.s, search.id)
    placed = {(x.candidate_id, x.participant_id) for x in prob.meetings}
    req_left = [r for r in prob.requirements if r.candidate_id in targets and r.required and (r.candidate_id, r.participant_id) not in placed]
    no_resp = sorted({prob.participants[r.participant_id].name for r in prob.requirements
                      if r.candidate_id in targets and not prob.participants[r.participant_id].submitted})
    info = f"{len(req_left)} required meeting(s) not yet placed for the selected candidate(s)."
    if no_resp:
        info += f" {len(no_resp)} person(s) haven't submitted availability and will not be auto-scheduled: {', '.join(no_resp)}."
    st.caption(info)

    if st.button("Generate proposal", type="primary", icon=":material/auto_awesome:", disabled=not targets):
        with st.spinner("Searching for the best conflict-free plan…"):
            res = propose(prob, targets, objective=objective, mode=mode, time_limit=float(limit))
        st.session_state["proposal"] = {"res": res, "fp": _fingerprint(prob), "at": datetime.now(), "targets": targets}

    prop = st.session_state.get("proposal")
    if not prop:
        return
    res = prop["res"]
    st.divider()
    stale = _fingerprint(prob) != prop["fp"]
    pname = {p.id: p.name for p in search.participants}
    n_pref = sum(1 for p in res.proposals if p.preferred)
    theme.page_header("Proposal", f"{len(res.proposals)} meeting(s) proposed",
                      f"Generated {prop['at']:%-I:%M %p} · solver status {escape(res.status)} in {res.seconds}s · "
                      f"{n_pref} at a preferred time · {len(res.required_missing)} required meeting(s) could not be placed"
                      + (f" · would replace {len(res.removed_meeting_ids)} unlocked draft meeting(s)" if res.removed_meeting_ids else ""))
    if stale:
        st.warning("The draft or someone's availability changed after this proposal was generated. "
                   "Generate a new proposal before applying.", icon=":material/sync_problem:")

    # Preview timeline: existing kept items + proposals (dashed).
    sections = []
    removed = set(res.removed_meeting_ids)
    for cid in prop["targets"]:
        c = cands.get(cid)
        if c is None:
            continue
        for vd in c.visit_days:
            if not vd.is_interview:
                continue
            d = vd.date
            items = []
            for e in c.fixed_events:
                if e.start.date() == d:
                    items.append({"type": "event", "cls": f"event {e.kind}", "s": e.start.hour * 60 + e.start.minute,
                                  "e": e.end.hour * 60 + e.end.minute, "t": e.title})
            for mt in c.meetings:
                if mt.start.date() == d and mt.id not in removed:
                    items.append({"type": "kept", "cls": "meeting" + (" locked" if mt.locked else ""),
                                  "s": mt.start.hour * 60 + mt.start.minute, "e": mt.end.hour * 60 + mt.end.minute,
                                  "icon": "🔒" if mt.locked else "", "t": pname.get(mt.participant_id, "")})
            for p in res.proposals:
                if p.candidate_id == cid and p.start.date() == d:
                    items.append({"type": "proposal", "cls": "proposal", "s": p.start.hour * 60 + p.start.minute,
                                  "e": p.end.hour * 60 + p.end.minute, "t": ("★ " if p.preferred else "") + pname[p.participant_id],
                                  "u": fmt_range(p.start, p.end)})
            lo = (min([vd.start_time.hour * 60 + vd.start_time.minute] + [i["s"] for i in items]) - 30) // 60 * 60
            hi = -(-(max([vd.end_time.hour * 60 + vd.end_time.minute] + [i["e"] for i in items]) + 30) // 60) * 60
            sections.append({"key": f"{cid}-{d}", "label": f"{c.name.split(' (')[0]} — {fmt_date(d)}",
                             "meta": "dashed = proposed", "startMin": lo, "endMin": hi,
                             "rows": [{"kind": "cand", "label": "Itinerary", "items": items}]})
    timeline(sections, key="prop_tl", step=search.start_step_minutes or search.slot_minutes)

    left, right = st.columns([1.1, 1], gap="large")
    with left:
        st.markdown("#### Proposed meetings")
        if not res.proposals:
            st.html("<p class='small-muted'>Nothing new could be placed.</p>")
        rows = []
        for p in res.proposals:
            rows.append(f"<tr><td class='t'>{fmt_date(p.start, long=False)}<br>{fmt_range(p.start, p.end)}</td>"
                        f"<td>{escape(pname[p.participant_id])}{' <b>★</b>' if p.preferred else ''}</td>"
                        f"<td>{escape(cands[p.candidate_id].name.split(' (')[0])}</td></tr>")
        if rows:
            st.html("<table class='itin'><thead><tr><th>When</th><th>Participant</th><th>Candidate</th></tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table>")
    with right:
        st.markdown("#### Not scheduled — and why")
        if not res.unscheduled:
            st.html("<p class='small-muted'>Every requested meeting fits.</p>")
        theme.attention_list([("error" if u.required else "info", f"{pname[u.participant_id]} → "
                               f"{cands[u.candidate_id].name.split(' (')[0]}", u.reason,
                               "Required" if u.required else "Optional") for u in res.unscheduled])

    published = [cands[c].name for c in prop["targets"] if c in cands and cands[c].status == "published"]
    needs_confirm = bool(res.removed_meeting_ids) or bool(published)
    ok = True
    if needs_confirm:
        msg = []
        if res.removed_meeting_ids:
            msg.append(f"replace {len(res.removed_meeting_ids)} unlocked meeting(s) in the draft")
        if published:
            msg.append(f"change the draft for already-published itineraries ({', '.join(published)}); the published "
                       "version stays as it is until you republish")
        ok = st.checkbox("I understand this will " + " and ".join(msg) + ".")
    a, b, _ = st.columns([1, 1, 2])
    if a.button("Apply to draft", type="primary", disabled=stale or not ok or not (res.proposals or res.removed_meeting_ids),
                icon=":material/check:"):
        with write(ctx, f"Applied {len(res.proposals)} meeting(s) to the draft"):
            sv.apply_proposals(ctx.s, search.id, res, ctx.actor)
            st.session_state.pop("proposal", None)
    if b.button("Discard proposal"):
        st.session_state.pop("proposal", None)
        st.rerun()
