"""Fictional demonstration data.

EVERY name, email, room and date here is invented for demonstration. Emails use
the reserved example.edu domain so nothing can be sent to a real person.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .auth import create_coordinator
from .services import audit, save_availability, intervals_to_cells, day_window, upsert_requirement

DEMO_COORDINATOR_EMAIL = "coordinator@example.edu"
DEMO_COORDINATOR_PASSWORD_DEFAULT = "demo-coordinator"

A_DAY = date(2026, 11, 12)
B_DAY1 = date(2026, 11, 16)
B_DAY2 = date(2026, 11, 17)

# name, role, office, (required_for_A, required_for_B) None = not meeting, duration
PEOPLE = [
    ("Dr. Morgan Sample", "Department Chair", "Demo Office 101", (True, True), 40),
    ("Dr. Riley Example", "Director of Graduate Studies", "Demo Office 102", (True, True), 25),
    ("Dr. Quinn Placeholder", "Professor", "Demo Office 103", (True, True), 25),
    ("Dr. Harper Testwell", "Associate Professor", "Demo Office 104", (True, True), 25),
    ("Dr. Rowan Mockford", "Assistant Professor", "Demo Office 105", (True, True), 25),
    ("Dr. Emerson Fictive", "Associate Professor", "Demo Office 106", (True, None), 25),
    ("Dr. Sage Specimen", "Professor", "Demo Office 107", (None, True), 25),
    ("Dr. Kai Prototype", "Assistant Professor", "Demo Office 108", (False, False), 25),
    ("Dr. Jamie Draftley", "Lecturer", "Demo Office 109", (False, False), 25),
    ("Dr. Taylor Mockingham", "Associate Dean (demo college office)", "Demo Dean's Suite", (True, True), 25),
    ("Dr. Parker Notreal", "Professor", "Demo Office 110", (True, True), 25),
    ("Dr. Casey Sampleton", "Associate Professor", "Demo Office 111", (True, False), 25),
    ("Dr. Blake Pending", "Assistant Professor", "Demo Office 112", (False, True), 25),
    ("Drew Officeworth", "Department Administrative Officer", "Demo Main Office", (False, False), 15),
]

# Availability: name -> list of (date, "HH:MM-HH:MM", level). People missing here have NOT submitted.
AVAIL = {
    "Dr. Morgan Sample": [(A_DAY, "08:30-12:00", "available"), (A_DAY, "13:00-15:00", "preferred"),
                          (B_DAY1, "09:00-12:00", "available"), (B_DAY1, "14:30-16:00", "preferred"),
                          (B_DAY2, "08:30-10:00", "available")],
    "Dr. Riley Example": [(A_DAY, "09:00-10:30", "preferred"), (A_DAY, "13:00-17:00", "available"),
                          (B_DAY1, "09:00-17:00", "available")],
    "Dr. Quinn Placeholder": [(A_DAY, "13:00-16:30", "available"), (B_DAY1, "10:00-12:00", "preferred"),
                              (B_DAY2, "08:30-10:00", "available")],
    # Only a 20-minute window on Candidate A's day -> scheduler must explain.
    "Dr. Harper Testwell": [(A_DAY, "09:00-09:20", "available"), (B_DAY1, "14:30-17:00", "available")],
    "Dr. Rowan Mockford": [(A_DAY, "09:00-17:00", "available"), (B_DAY1, "09:00-12:00", "available"),
                           (B_DAY1, "15:00-16:00", "preferred")],
    # Available only for Candidate A's visit.
    "Dr. Emerson Fictive": [(A_DAY, "13:00-15:30", "preferred")],
    # Available only for Candidate B's visit.
    "Dr. Sage Specimen": [(B_DAY1, "09:15-11:00", "available"), (B_DAY2, "08:30-09:30", "preferred")],
    "Dr. Kai Prototype": [(A_DAY, "14:00-16:30", "available"), (B_DAY1, "15:00-17:00", "available")],
    "Dr. Jamie Draftley": [(A_DAY, "09:00-10:00", "available")],
    "Dr. Taylor Mockingham": [(A_DAY, "13:30-14:30", "preferred"), (B_DAY1, "10:00-11:00", "available"),
                              (B_DAY2, "09:00-10:00", "available")],
}


def _dt(d: date, hhmm: str) -> datetime:
    h, mm = map(int, hhmm.split(":"))
    return datetime.combine(d, time(h, mm))


def sync_demo_coordinator(s: Session) -> None:
    """Make sure the demo coordinator exists and uses the CURRENT demo password
    (DEMO_COORDINATOR_PASSWORD, or the default). Called at every app start in demo
    mode, so changing the password in .env / Streamlit Secrets takes effect after a restart."""
    from .auth import hash_password, verify_password

    pw = os.getenv("DEMO_COORDINATOR_PASSWORD", DEMO_COORDINATOR_PASSWORD_DEFAULT).strip()
    if len(pw) < 8:
        pw = DEMO_COORDINATOR_PASSWORD_DEFAULT  # too short to be accepted; fall back
    u = s.scalar(select(m.User).where(m.User.email == DEMO_COORDINATOR_EMAIL))
    if u is None:
        create_coordinator(s, DEMO_COORDINATOR_EMAIL, "Demo Coordinator", pw)
    elif not verify_password(pw, u.password_hash) or not u.active:
        u.password_hash, u.active = hash_password(pw), True
    s.flush()


def seed_demo(s: Session) -> m.Search:
    """Create the demo search (idempotent: does nothing if it already exists)."""
    existing = s.scalar(select(m.Search).where(m.Search.is_demo.is_(True)))
    if existing:
        return existing

    search = m.Search(
        name="DEMO — Assistant Professor search (fictional data)",
        department="Department of Communication",
        timezone="America/Chicago", slot_minutes=15, default_meeting_minutes=25, buffer_minutes=5,
        availability_deadline=datetime(2026, 11, 6, 17, 0), availability_open=True,
        coordinator_name="Search Coordinator (demo)", coordinator_email=DEMO_COORDINATOR_EMAIL, is_demo=True,
    )
    s.add(search)
    s.flush()

    locs = {}
    for name, details in [
        ("Demo Main Office", "Placeholder location — replace with the real room"),
        ("Demo Conference Room", "Placeholder location"),
        ("Demo Lecture Hall", "Placeholder location"),
        ("Demo Dining Room", "Placeholder location"),
    ] + [(f"Demo Office {n}", "Placeholder location") for n in range(101, 113)] + [("Demo Dean's Suite", "Placeholder")]:
        locs[name] = m.Location(search_id=search.id, name=name, details=details)
        s.add(locs[name])
    s.flush()

    cand_a = m.Candidate(search_id=search.id, name="Dr. Avery Placeholder (Demo Candidate A)",
                         email="candidate.a@example.edu",
                         public_note="Welcome! Please check in at the Demo Main Office on arrival. (Demo text)",
                         internal_notes="Demo internal note: hotel booked for two nights.")
    cand_b = m.Candidate(search_id=search.id, name="Dr. Jordan Hypothetical (Demo Candidate B)",
                         email="candidate.b@example.edu",
                         public_note="Your visit spans two days. Lunch is provided both days. (Demo text)")
    s.add_all([cand_a, cand_b])
    s.flush()
    s.add_all([
        m.VisitDay(candidate_id=cand_a.id, date=A_DAY, start_time=time(8, 30), end_time=time(17, 0)),
        m.VisitDay(candidate_id=cand_b.id, date=B_DAY1, start_time=time(9, 0), end_time=time(17, 0)),
        m.VisitDay(candidate_id=cand_b.id, date=B_DAY2, start_time=time(8, 30), end_time=time(12, 0)),
    ])

    people = {}
    for name, role, office, (req_a, req_b), dur in PEOPLE:
        slug = name.lower().replace("dr. ", "").replace(" ", ".")
        p = m.Participant(search_id=search.id, name=name, email=f"{slug}@example.edu", role_title=role,
                          location_id=locs[office].id, preferred_duration=dur)
        s.add(p)
        s.flush()
        people[name] = p
        for cand, req in ((cand_a, req_a), (cand_b, req_b)):
            if req is not None:
                upsert_requirement(s, cand, p, req, dur)
    s.flush()

    def ev(c, d, a, b, title, kind, loc, attendees=(), audience=None):
        e = m.FixedEvent(candidate_id=c.id, title=title, kind=kind, start=_dt(d, a), end=_dt(d, b),
                         location_id=locs[loc].id if loc else None, audience=audience)
        e.attendees = [people[n] for n in attendees]
        s.add(e)

    ev(cand_a, A_DAY, "08:30", "09:00", "Arrival and welcome", "arrival", "Demo Main Office")
    ev(cand_a, A_DAY, "10:30", "11:30", "Research presentation", "presentation", "Demo Lecture Hall",
       audience="Department faculty and students")
    ev(cand_a, A_DAY, "12:00", "13:00", "Lunch with search committee", "meal", "Demo Dining Room",
       attendees=["Dr. Riley Example", "Dr. Quinn Placeholder"])
    ev(cand_a, A_DAY, "16:30", "17:00", "Wrap-up and departure", "departure", "Demo Main Office")
    ev(cand_b, B_DAY1, "09:00", "09:15", "Arrival and welcome", "arrival", "Demo Main Office")
    ev(cand_b, B_DAY1, "12:00", "13:00", "Lunch with faculty", "meal", "Demo Dining Room",
       attendees=["Dr. Harper Testwell", "Dr. Rowan Mockford"])
    ev(cand_b, B_DAY1, "13:30", "14:30", "Teaching demonstration", "teaching", "Demo Lecture Hall",
       audience="Undergraduate class (demo)")
    ev(cand_b, B_DAY2, "10:00", "11:00", "Research presentation", "presentation", "Demo Lecture Hall",
       audience="Department faculty and students")
    ev(cand_b, B_DAY2, "11:45", "12:00", "Departure", "departure", "Demo Main Office")
    s.flush()
    s.refresh(search)

    for name, blocks in AVAIL.items():
        p = people[name]
        rows = [m.Availability(participant_id=p.id, start=_dt(d, r.split("-")[0]), end=_dt(d, r.split("-")[1]), level=lvl)
                for d, r, lvl in blocks]
        cells = {}
        for d in sorted({b[0] for b in blocks} | {A_DAY, B_DAY1, B_DAY2}):
            st_, en_ = day_window(search, d)
            cells[d] = intervals_to_cells([x for x in rows if x.start.date() == d], d, st_, en_, search.slot_minutes)
        save_availability(s, p, cells, actor=f"{p.email} (demo seed)", coordinator_override=True)

    s.flush()
    # One locked meeting to demonstrate locks.
    chair = people["Dr. Morgan Sample"]
    s.add(m.Meeting(candidate_id=cand_a.id, participant_id=chair.id, start=_dt(A_DAY, "09:00"),
                    end=_dt(A_DAY, "09:40"), location_id=chair.location_id, locked=True, source="manual",
                    requirement_id=next(r.id for r in cand_a.requirements if r.participant_id == chair.id),
                    updated_by="demo seed"))

    sync_demo_coordinator(s)
    audit(s, search.id, "system", "demo_seeded", "Loaded fictional demonstration data")
    s.flush()
    return search
