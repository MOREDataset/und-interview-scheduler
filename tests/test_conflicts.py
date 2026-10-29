"""Conflict detection: blocking errors vs. warnings."""
from __future__ import annotations

from scheduler_app.core.conflicts import ERROR, WARNING, detect
from scheduler_app.core.problem import load_problem

from .conftest import D1, D2


def codes(b, severity=None):
    return {i.code for i in detect(load_problem(b.s, b.search.id)) if severity is None or i.severity == severity}


def test_candidate_double_booking_is_blocking(b):
    c = b.candidate("A", D1)
    p1 = b.person("P1", avail=[(D1, "09:00", "12:00")])
    p2 = b.person("P2", avail=[(D1, "09:00", "12:00")])
    b.meeting(c, p1, D1, "09:00", "09:30")
    b.meeting(c, p2, D1, "09:15", "09:45")
    assert "candidate_double_booked" in codes(b, ERROR)


def test_faculty_double_booking_across_candidates(b):
    a = b.candidate("A", D1)
    c2 = b.candidate("B", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    b.meeting(a, p, D1, "09:00", "09:30")
    b.meeting(c2, p, D1, "09:00", "09:30")
    assert "participant_double_booked" in codes(b, ERROR)


def test_outside_availability_error_and_override_warning(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "09:30")])
    q = b.person("Q", avail=[(D1, "09:00", "09:30")])
    b.meeting(c, p, D1, "10:00", "10:30")
    b.meeting(c, q, D1, "11:00", "11:30", reason="Confirmed by phone")
    issues = detect(load_problem(b.s, b.search.id))
    by_person = {(i.participant_id, i.code, i.severity) for i in issues}
    assert (p.id, "outside_availability", ERROR) in by_person
    assert (q.id, "availability_override", WARNING) in by_person


def test_non_submitter_meeting_flagged(b):
    c = b.candidate("A", D1)
    p = b.person("Silent", submitted=False)
    b.meeting(c, p, D1, "09:00", "09:30")
    msg = next(i.message for i in detect(load_problem(b.s, b.search.id)) if i.code == "outside_availability")
    assert "has not submitted availability" in msg


def test_fixed_event_overlap_and_outside_window(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "08:00", "17:00")])
    q = b.person("Q", avail=[(D1, "08:00", "17:00")])
    b.event(c, D1, "10:00", "11:00")
    b.meeting(c, p, D1, "10:30", "11:00")
    b.meeting(c, q, D1, "13:00", "13:30")  # day ends 12:00
    got = codes(b, ERROR)
    assert {"overlaps_fixed_event", "outside_window"} <= got


def test_meeting_on_non_visit_day(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D2, "09:00", "10:00")])
    b.meeting(c, p, D2, "09:00", "09:30")
    assert "not_visit_day" in codes(b, ERROR)


def test_short_transition_is_a_warning(b):
    b.search.buffer_minutes = 10
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    q = b.person("Q", avail=[(D1, "09:00", "12:00")])
    b.meeting(c, p, D1, "09:00", "09:30")
    b.meeting(c, q, D1, "09:35", "10:05")
    assert "short_transition" in codes(b, WARNING)
    assert "short_transition" not in codes(b, ERROR)


def test_required_unscheduled_is_reported(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    b.need(c, p, required=True)
    assert "required_unscheduled" in codes(b, WARNING)
