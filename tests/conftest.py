"""Shared fixtures: a small, fully controlled scheduling scenario in an in-memory DB."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest

from scheduler_app.core import models as m
from scheduler_app.core import services as sv
from scheduler_app.core.db import get_sessionmaker, init_db, make_engine

D1 = date(2030, 3, 4)  # fictional future dates
D2 = date(2030, 3, 5)


def dt(d: date, hhmm: str) -> datetime:
    h, mi = map(int, hhmm.split(":"))
    return datetime.combine(d, time(h, mi))


@pytest.fixture()
def engine():
    eng = make_engine("sqlite:///:memory:")
    init_db(eng)
    return eng


@pytest.fixture()
def s(engine):
    sess = get_sessionmaker(engine)()
    yield sess
    sess.close()


class Builder:
    """Tiny helper to build scenarios in a few lines."""

    def __init__(self, s, buffer=0, slot=15):
        self.s = s
        self.search = m.Search(name="Test search", slot_minutes=slot, buffer_minutes=buffer,
                               default_meeting_minutes=30, availability_open=True)
        s.add(self.search)
        s.flush()

    def candidate(self, name, *days, start="09:00", end="12:00"):
        c = m.Candidate(search_id=self.search.id, name=name)
        self.s.add(c)
        self.s.flush()
        for d in days:
            c.visit_days.append(m.VisitDay(date=d, start_time=time(*map(int, start.split(":"))),
                                           end_time=time(*map(int, end.split(":")))))
        self.s.flush()
        return c

    def person(self, name, avail=None, pref=None, submitted=True):
        p = m.Participant(search_id=self.search.id, name=name, email=f"{name.lower().replace(' ', '.')}@example.edu")
        self.s.add(p)
        self.s.flush()
        for d, a, b in avail or []:
            self.s.add(m.Availability(participant_id=p.id, start=dt(d, a), end=dt(d, b), level=m.AVAILABLE))
        for d, a, b in pref or []:
            self.s.add(m.Availability(participant_id=p.id, start=dt(d, a), end=dt(d, b), level=m.PREFERRED))
        if submitted:
            p.submitted_at = m.utcnow()
        self.s.flush()
        return p

    def need(self, c, p, required=True, minutes=30):
        return sv.upsert_requirement(self.s, c, p, required, minutes)

    def event(self, c, d, a, b, title="Talk", attendees=()):
        e = m.FixedEvent(candidate_id=c.id, title=title, kind="presentation", start=dt(d, a), end=dt(d, b))
        e.attendees = list(attendees)
        self.s.add(e)
        self.s.flush()
        return e

    def meeting(self, c, p, d, a, b, locked=False, reason=None):
        mt = m.Meeting(candidate_id=c.id, participant_id=p.id, start=dt(d, a), end=dt(d, b), locked=locked,
                       override_reason=reason)
        self.s.add(mt)
        self.s.flush()
        return mt


@pytest.fixture()
def b(s):
    return Builder(s)
