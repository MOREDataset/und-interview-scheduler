"""Automatic scheduling assistant (Google OR-Tools CP-SAT).

How it works, in plain terms
----------------------------
1. For every meeting that still needs a time, list every possible start time
   (on the configured grid, e.g. every 15 minutes) that passes ALL hard rules:
     * inside the candidate's interview hours on a visit day,
     * fully inside the time the faculty member *said* they are free
       (no submission = no possible times; we never assume availability),
     * not on top of a fixed event, locked meeting, or any meeting we keep,
       including the transition buffer,
     * not when that faculty member is busy with another candidate.
2. CP-SAT picks at most one start per meeting so that no candidate and no
   faculty member is in two places at once (buffers included).
3. Among valid plans it maximises a weighted score. Required meetings always
   dominate; the chosen objective decides how ties are broken.
4. Every required meeting that could not be placed gets a plain-language reason.

The result is only a *proposal*. Nothing is written to the database here.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .problem import PMeeting, PRequirement, Problem
from .timeutil import covers, fmt_date, fmt_range, merge, overlaps

OBJECTIVES = {
    "required": "Schedule as many required meetings as possible",
    "compact": "Minimize gaps between appointments",
    "preferred": "Maximize faculty preferred-time matches",
}

MODES = {
    "fill": "Keep existing draft meetings; only fill in what's missing",
    "replan": "Re-plan all unlocked draft meetings (locked ones stay put)",
}

W_REQUIRED = 1_000_000
W_OPTIONAL = 100_000


@dataclass
class Proposal:
    requirement_id: int
    candidate_id: int
    participant_id: int
    start: datetime
    end: datetime
    preferred: bool


@dataclass
class Unscheduled:
    requirement_id: int
    candidate_id: int
    participant_id: int
    required: bool
    reason: str


@dataclass
class ScheduleResult:
    proposals: list[Proposal] = field(default_factory=list)
    unscheduled: list[Unscheduled] = field(default_factory=list)
    removed_meeting_ids: list[int] = field(default_factory=list)  # unlocked meetings a re-plan would replace
    status: str = "OK"
    objective: str = "required"
    mode: str = "fill"
    seconds: float = 0.0

    @property
    def required_missing(self) -> list[Unscheduled]:
        return [u for u in self.unscheduled if u.required]


@dataclass
class _Busy:
    start: datetime
    end: datetime
    label: str


def _candidate_starts(prob: Problem, r: PRequirement, cand_busy: list[_Busy], part_busy: list[_Busy]):
    """Yield (start, end, preferred) for each start time that passes every hard rule."""
    part = prob.participants[r.participant_id]
    free = part.free
    dur = timedelta(minutes=r.duration)
    buf = timedelta(minutes=prob.buffer_minutes)
    step = timedelta(minutes=prob.slot_minutes)
    for day in prob.days_for(r.candidate_id):
        s = day.start
        while s + dur <= day.end:
            e = s + dur
            if covers(free, s, e) and not any(
                overlaps(s - buf, e + buf, b.start, b.end) for b in cand_busy
            ) and not any(overlaps(s - buf, e + buf, b.start, b.end) for b in part_busy):
                yield s, e, covers(merge(part.preferred), s, e)
            s += step


def _split_busy(prob: Problem, keep: list[PMeeting]):
    cand_busy: dict[int, list[_Busy]] = {}
    part_busy: dict[int, list[_Busy]] = {}
    pname = {p.id: p.name for p in prob.participants.values()}
    for ev in prob.events:
        cand_busy.setdefault(ev.candidate_id, []).append(_Busy(ev.start, ev.end, f"“{ev.title}”"))
        for pid in ev.attendee_ids:
            part_busy.setdefault(pid, []).append(_Busy(ev.start, ev.end, f"“{ev.title}”"))
    for mt in keep:
        lab = "a locked meeting" if mt.locked else "an existing meeting"
        cand_busy.setdefault(mt.candidate_id, []).append(
            _Busy(mt.start, mt.end, f"{lab} with {pname.get(mt.participant_id, '?')}")
        )
        part_busy.setdefault(mt.participant_id, []).append(
            _Busy(mt.start, mt.end, f"{lab} with {prob.candidates[mt.candidate_id].name}")
        )
    return cand_busy, part_busy


def explain(prob: Problem, r: PRequirement, cand_busy: list[_Busy], part_busy: list[_Busy], had_options: bool) -> str:
    """Plain-language reason why a meeting could not be placed."""
    part = prob.participants[r.participant_id]
    cand = prob.candidates[r.candidate_id]
    days = prob.days_for(r.candidate_id)
    dates = ", ".join(fmt_date(d.date, long=False) for d in days) or "(no visit dates set)"
    if not days:
        return f"{cand.name} has no visit dates yet, so there is nowhere to place this meeting."
    if not part.submitted:
        return (f"{part.name} has not submitted availability yet. The assistant never assumes someone is free; "
                f"send a reminder or schedule manually with an override reason.")
    on_days = [(s, e) for s, e in part.free if any(d.date == s.date() for d in days)]
    if not on_days:
        return f"{part.name} marked no availability on {cand.name}'s visit date(s) ({dates})."
    windows = []
    for d in days:
        for s, e in part.free:
            s2, e2 = max(s, d.start), min(e, d.end)
            if s2 < e2:
                windows.append((s2, e2))
    windows = merge(windows)
    if not windows:
        return (f"{part.name}'s availability on {dates} falls entirely outside {cand.name}'s interview hours.")
    if not any(e - s >= timedelta(minutes=r.duration) for s, e in windows):
        shown = "; ".join(f"{fmt_date(s, long=False)} {fmt_range(s, e)}" for s, e in windows[:4])
        return (f"No compatible {r.duration}-minute slot was found for {part.name} on {cand.name}'s visit date(s). "
                f"Their free time inside interview hours is only: {shown}.")
    if not had_options:
        blockers = set()
        for s, e in windows:
            for b in cand_busy + part_busy:
                if overlaps(s, e, b.start, b.end):
                    blockers.add(b.label)
        listing = ", ".join(sorted(blockers)[:4]) or "other commitments"
        return (f"Every {r.duration}-minute window when {part.name} is free (plus the {prob.buffer_minutes}-minute "
                f"buffer) collides with: {listing}.")
    return (f"{part.name} had compatible times, but they were all needed for other meetings on "
            f"{cand.name}'s itinerary. Unlock or move a meeting, shorten durations, or ask {part.name} for more times.")


def propose(
    prob: Problem,
    candidate_ids: list[int],
    objective: str = "required",
    mode: str = "fill",
    time_limit: float = 10.0,
) -> ScheduleResult:
    from ortools.sat.python import cp_model

    t0 = _time.time()
    res = ScheduleResult(objective=objective, mode=mode)
    targets = set(candidate_ids)

    # Which existing meetings stay fixed?
    keep: list[PMeeting] = []
    for mt in prob.meetings:
        if mt.candidate_id not in targets or mt.locked or mode == "fill":
            keep.append(mt)
        elif mt.id is not None:
            res.removed_meeting_ids.append(mt.id)
    already = {(mt.candidate_id, mt.participant_id) for mt in keep}
    todo = [r for r in prob.requirements if r.candidate_id in targets and (r.candidate_id, r.participant_id) not in already]

    cand_busy, part_busy = _split_busy(prob, keep)

    # Integer time axis: minutes since the earliest visit date.
    all_dates = [d.date for d in prob.days] or [date.today()]
    origin = datetime.combine(min(all_dates), datetime.min.time())

    def mins(dt: datetime) -> int:
        return int((dt - origin).total_seconds() // 60)

    model = cp_model.CpModel()
    buf = prob.buffer_minutes
    choices: dict[int, list[tuple]] = {}
    by_cand: dict[int, list] = {}
    by_part: dict[int, list] = {}
    by_cand_day: dict[tuple[int, date], list] = {}
    objective_terms = []

    for r in todo:
        opts = list(_candidate_starts(prob, r, cand_busy.get(r.candidate_id, []), part_busy.get(r.participant_id, [])))
        choices[r.id] = []
        for s, e, pref in opts:
            x = model.NewBoolVar(f"r{r.id}_{mins(s)}")
            iv = model.NewOptionalFixedSizeIntervalVar(mins(s), r.duration + buf, x, f"iv{r.id}_{mins(s)}")
            choices[r.id].append((x, s, e, pref))
            by_cand.setdefault(r.candidate_id, []).append(iv)
            by_part.setdefault(r.participant_id, []).append(iv)
            by_cand_day.setdefault((r.candidate_id, s.date()), []).append((x, s, e))
            w_pref = 2000 if objective == "preferred" else 200
            if pref:
                objective_terms.append(w_pref * x)
        if choices[r.id]:
            model.AddAtMostOne(c[0] for c in choices[r.id])
            weight = W_REQUIRED if r.required else W_OPTIONAL
            objective_terms.append(weight * sum(c[0] for c in choices[r.id]))

    for ivs in by_cand.values():
        model.AddNoOverlap(ivs)
    for ivs in by_part.values():
        model.AddNoOverlap(ivs)

    # Compactness: minimise the span of each candidate-day (first item start .. last item end).
    w_span = 20 if objective == "compact" else 2
    for (cid, d), xs in by_cand_day.items():
        day = next(dd for dd in prob.days_for(cid) if dd.date == d)
        lo, hi = mins(day.start), mins(day.end)
        first = model.NewIntVar(lo, hi, f"first_{cid}_{d}")
        last = model.NewIntVar(lo, hi, f"last_{cid}_{d}")
        model.Add(first <= last)
        fixed = [b for b in cand_busy.get(cid, []) if b.start.date() == d]
        for b in fixed:
            model.Add(first <= max(lo, mins(b.start)))
            model.Add(last >= min(hi, mins(b.end)))
        big = hi - lo
        for x, s, e in xs:
            model.Add(first <= mins(s) + big * (1 - x))  # type: ignore[operator]
            model.Add(last >= mins(e) - big * (1 - x))  # type: ignore[operator]
        objective_terms.append(-w_span * (last - first))

    # Avoid fragmented days: reward meetings that follow directly on from another
    # meeting or a fixed event (after the buffer, rounded up to the start grid).
    w_adj = 3000 if objective == "compact" else 300
    step = prob.slot_minutes

    def next_grid(t: int, day_start: int) -> int:
        return day_start + -(-(t - day_start) // step) * step

    for (cid, d), xs in by_cand_day.items():
        day = next(dd for dd in prob.days_for(cid) if dd.date == d)
        d0 = mins(day.start)
        fixed = [b for b in cand_busy.get(cid, []) if b.start.date() == d]
        follow_fixed = {next_grid(mins(b.end) + buf, d0) for b in fixed}
        precede_fixed = {mins(b.start) - buf for b in fixed}
        by_start: dict[int, list] = {}
        for x, s_, e_ in xs:
            by_start.setdefault(mins(s_), []).append((x, s_, e_))
        for x, s_, e_ in xs:
            if mins(s_) in follow_fixed or mins(e_) in precede_fixed:
                objective_terms.append(w_adj * x)
            nxt = next_grid(mins(e_) + buf, d0)
            for y, _, _ in by_start.get(nxt, []):
                a = model.NewBoolVar("")
                model.AddImplication(a, x)
                model.AddImplication(a, y)
                objective_terms.append(w_adj * a)

    # Spread multi-day visits: penalise the busiest day.
    for cid in targets:
        cdays = sorted({d for (c, d) in by_cand_day if c == cid})
        if len(cdays) > 1:
            peak = model.NewIntVar(0, 100, f"peak_{cid}")
            for d in cdays:
                model.Add(peak >= sum(x for x, _, _ in by_cand_day[(cid, d)]))
            objective_terms.append(-500 * peak)

    if objective_terms:
        model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 4
    solver.parameters.random_seed = 7
    status = solver.Solve(model)
    res.status = solver.StatusName(status)

    solved = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    for r in todo:
        chosen = None
        if solved:
            for x, s, e, pref in choices[r.id]:
                if solver.Value(x):
                    chosen = Proposal(r.id, r.candidate_id, r.participant_id, s, e, pref)
                    break
        if chosen:
            res.proposals.append(chosen)
        else:
            reason = explain(prob, r, cand_busy.get(r.candidate_id, []), part_busy.get(r.participant_id, []),
                             had_options=bool(choices[r.id]))
            res.unscheduled.append(Unscheduled(r.id, r.candidate_id, r.participant_id, r.required, reason))
    res.proposals.sort(key=lambda p: (p.candidate_id, p.start))
    res.seconds = round(_time.time() - t0, 2)
    return res


def open_slots(prob: Problem, candidate_id: int, participant_id: int, duration: int) -> list[tuple[datetime, datetime, bool]]:
    """Every valid start for one candidate/participant pair given the *current* draft.
    Used by the manual scheduling workspace to suggest times."""
    others = [mt for mt in prob.meetings if not (mt.candidate_id == candidate_id and mt.participant_id == participant_id)]
    cand_busy, part_busy = _split_busy(prob, others)
    r = PRequirement(-1, candidate_id, participant_id, True, duration)
    return list(_candidate_starts(prob, r, cand_busy.get(candidate_id, []), part_busy.get(participant_id, [])))
