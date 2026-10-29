"""Conflict detection.

Every issue is either an ERROR (blocking: the schedule is not valid and cannot be
published) or a WARNING (worth a look, but publishing is allowed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .problem import PMeeting, Problem
from .timeutil import covers, fmt_date, fmt_range, overlaps

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    candidate_id: int | None = None
    participant_id: int | None = None
    meeting_ids: list[int] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.severity == ERROR


def _when(mt: PMeeting) -> str:
    return f"{fmt_date(mt.start, long=False)}, {fmt_range(mt.start, mt.end)}"


def detect(prob: Problem, candidate_id: int | None = None) -> list[Issue]:
    """Return all issues, optionally restricted to one candidate."""
    issues: list[Issue] = []
    cname = {c.id: c.name for c in prob.candidates.values()}
    pname = {p.id: p.name for p in prob.participants.values()}
    meetings = sorted(prob.meetings, key=lambda x: x.start)

    def want(cid: int | None) -> bool:
        return candidate_id is None or cid == candidate_id

    # --- candidate double-booking ---------------------------------------
    for i, a in enumerate(meetings):
        for b in meetings[i + 1 :]:
            if a.candidate_id == b.candidate_id and overlaps(a.start, a.end, b.start, b.end) and want(a.candidate_id):
                issues.append(Issue(
                    ERROR, "candidate_double_booked",
                    f"{cname[a.candidate_id]} has two meetings at the same time: "
                    f"{pname[a.participant_id]} and {pname[b.participant_id]} ({_when(a)}).",
                    a.candidate_id, None, [x for x in (a.id, b.id) if x],
                ))

    # --- participant double-booking (across all candidates) ------------
    for i, a in enumerate(meetings):
        for b in meetings[i + 1 :]:
            if a.participant_id == b.participant_id and overlaps(a.start, a.end, b.start, b.end):
                if want(a.candidate_id) or want(b.candidate_id):
                    issues.append(Issue(
                        ERROR, "participant_double_booked",
                        f"{pname[a.participant_id]} is booked twice at once: with {cname[a.candidate_id]} "
                        f"and with {cname[b.candidate_id]} ({_when(a)}).",
                        a.candidate_id, a.participant_id, [x for x in (a.id, b.id) if x],
                    ))
        for ev in prob.events:
            if a.participant_id in ev.attendee_ids and overlaps(a.start, a.end, ev.start, ev.end) and want(a.candidate_id):
                if ev.candidate_id != a.candidate_id:
                    issues.append(Issue(
                        ERROR, "participant_busy_event",
                        f"{pname[a.participant_id]} is attending “{ev.title}” for {cname[ev.candidate_id]} "
                        f"during their meeting with {cname[a.candidate_id]} ({_when(a)}).",
                        a.candidate_id, a.participant_id, [a.id] if a.id else [],
                    ))

    for mt in meetings:
        if not want(mt.candidate_id):
            continue
        who, cand = pname[mt.participant_id], cname[mt.candidate_id]
        ids = [mt.id] if mt.id else []

        # --- overlaps a fixed event on the candidate's own itinerary ----
        for ev in prob.events:
            if ev.candidate_id == mt.candidate_id and overlaps(mt.start, mt.end, ev.start, ev.end):
                issues.append(Issue(
                    ERROR, "overlaps_fixed_event",
                    f"Meeting with {who} overlaps “{ev.title}” on {cand}'s itinerary ({_when(mt)}).",
                    mt.candidate_id, mt.participant_id, ids,
                ))

        # --- inside the visit window -----------------------------------
        days = [d for d in prob.days_for(mt.candidate_id) if d.date == mt.start.date()]
        if not days:
            issues.append(Issue(
                ERROR, "not_visit_day",
                f"Meeting with {who} is on {fmt_date(mt.start, long=False)}, which is not one of {cand}'s visit days.",
                mt.candidate_id, mt.participant_id, ids,
            ))
        elif not (days[0].start <= mt.start and mt.end <= days[0].end):
            issues.append(Issue(
                ERROR, "outside_window",
                f"Meeting with {who} ({_when(mt)}) falls outside {cand}'s interview hours "
                f"({fmt_range(days[0].start, days[0].end)}).",
                mt.candidate_id, mt.participant_id, ids,
            ))

        # --- submitted availability ------------------------------------
        part = prob.participants.get(mt.participant_id)
        if part is not None and not covers(part.free, mt.start, mt.end):
            if mt.override_reason:
                issues.append(Issue(
                    WARNING, "availability_override",
                    f"Meeting with {who} ({_when(mt)}) is outside their submitted availability "
                    f"— overridden: “{mt.override_reason}”.",
                    mt.candidate_id, mt.participant_id, ids,
                ))
            else:
                why = "has not submitted availability" if not part.submitted else "did not mark this time as available"
                issues.append(Issue(
                    ERROR, "outside_availability",
                    f"Meeting with {who} ({_when(mt)}) is outside their availability: {who} {why}. "
                    "Move it, or record an override reason.",
                    mt.candidate_id, mt.participant_id, ids,
                ))

    # --- transition time between consecutive items -------------------------
    if prob.buffer_minutes > 0:
        buf = timedelta(minutes=prob.buffer_minutes)
        for cid in prob.candidates:
            if not want(cid):
                continue
            items = [(x.start, x.end, "m", x) for x in meetings if x.candidate_id == cid]
            items += [(e.start, e.end, "e", e) for e in prob.events if e.candidate_id == cid]
            items.sort(key=lambda t: t[0])
            for (s1, e1, k1, o1), (s2, e2, k2, o2) in zip(items, items[1:]):
                if "m" not in (k1, k2) or s2.date() != s1.date():
                    continue
                gap = s2 - e1
                if timedelta(0) <= gap < buf:
                    first = pname[o1.participant_id] if k1 == "m" else o1.title
                    second = pname[o2.participant_id] if k2 == "m" else o2.title
                    issues.append(Issue(
                        WARNING, "short_transition",
                        f"Only {int(gap.total_seconds() // 60)} min between {first} and {second} for "
                        f"{cname[cid]} on {fmt_date(s1, long=False)} (buffer is {prob.buffer_minutes} min).",
                        cid, None, [o.id for k, o in ((k1, o1), (k2, o2)) if k == "m" and o.id],
                    ))

    # --- required meetings not yet scheduled -------------------------------
    scheduled = {(x.candidate_id, x.participant_id) for x in meetings}
    for r in prob.requirements:
        if not want(r.candidate_id) or (r.candidate_id, r.participant_id) in scheduled:
            continue
        if r.required:
            issues.append(Issue(
                WARNING, "required_unscheduled",
                f"Required meeting with {pname[r.participant_id]} is not scheduled for {cname[r.candidate_id]}.",
                r.candidate_id, r.participant_id,
            ))
    return issues


def summarize(issues: list[Issue]) -> tuple[int, int]:
    errors = sum(1 for i in issues if i.severity == ERROR)
    return errors, len(issues) - errors
