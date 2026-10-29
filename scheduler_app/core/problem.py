"""A plain-Python snapshot of a search, used by conflict detection and the
automatic scheduler. Keeping this independent of the database makes the
scheduling rules easy to unit-test."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from . import models as m
from .timeutil import Interval, merge


@dataclass
class PCandidate:
    id: int
    name: str


@dataclass
class PDay:
    candidate_id: int
    date: date
    start: datetime
    end: datetime


@dataclass
class PEvent:
    id: int
    candidate_id: int
    title: str
    start: datetime
    end: datetime
    attendee_ids: set[int] = field(default_factory=set)


@dataclass
class PRequirement:
    id: int
    candidate_id: int
    participant_id: int
    required: bool
    duration: int  # minutes


@dataclass
class PParticipant:
    id: int
    name: str
    submitted: bool
    available: list[Interval] = field(default_factory=list)
    preferred: list[Interval] = field(default_factory=list)
    responded_dates: set[date] = field(default_factory=set)

    @property
    def free(self) -> list[Interval]:
        """Every time they said they can meet (available OR preferred)."""
        return merge(self.available + self.preferred)


@dataclass
class PMeeting:
    id: int | None
    candidate_id: int
    participant_id: int
    start: datetime
    end: datetime
    requirement_id: int | None = None
    locked: bool = False
    override_reason: str | None = None


@dataclass
class Problem:
    candidates: dict[int, PCandidate]
    participants: dict[int, PParticipant]
    days: list[PDay]
    events: list[PEvent]
    requirements: list[PRequirement]
    meetings: list[PMeeting]
    slot_minutes: int = 15  # start-time step used by the scheduler
    buffer_minutes: int = 5

    def days_for(self, candidate_id: int) -> list[PDay]:
        return [d for d in self.days if d.candidate_id == candidate_id]


def load_problem(s: Session, search_id: int) -> Problem:
    """Build a Problem from the database. Busy-time from *all* candidates is
    always loaded so a faculty member is never double-booked across candidates."""
    s.flush()
    s.expire_all()  # make sure collections reflect everything written in this transaction
    search = s.get(m.Search, search_id)
    assert search is not None
    cands = {c.id: PCandidate(c.id, c.name) for c in search.candidates}
    days, events, reqs, meetings = [], [], [], []
    for c in search.candidates:
        for d in c.visit_days:
            if not d.is_interview:
                continue  # travel days: no one-on-one meetings
            days.append(PDay(c.id, d.date, datetime.combine(d.date, d.start_time), datetime.combine(d.date, d.end_time)))
        for e in c.fixed_events:
            events.append(PEvent(e.id, c.id, e.title, e.start, e.end, {a.id for a in e.attendees}))
        for r in c.requirements:
            if r.participant.active:
                reqs.append(PRequirement(r.id, c.id, r.participant_id, r.required, r.duration_minutes))
        for mt in c.meetings:
            meetings.append(
                PMeeting(mt.id, c.id, mt.participant_id, mt.start, mt.end, mt.requirement_id, mt.locked, mt.override_reason)
            )
    parts: dict[int, PParticipant] = {}
    for p in search.participants:
        pp = PParticipant(p.id, p.name, p.submitted_at is not None)
        for a in p.availability:
            (pp.preferred if a.level == m.PREFERRED else pp.available).append((a.start, a.end))
        pp.responded_dates = {r.date for r in p.responses}
        parts[p.id] = pp
    step = search.start_step_minutes or search.slot_minutes
    return Problem(cands, parts, days, events, reqs, meetings, step, search.buffer_minutes)
