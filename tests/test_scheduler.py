"""Hard constraints and explanations of the automatic scheduling assistant."""
from __future__ import annotations

from itertools import combinations

from scheduler_app.core.problem import load_problem
from scheduler_app.core.scheduler import open_slots, propose
from scheduler_app.core.timeutil import covers, overlaps

from .conftest import D1, D2, Builder, dt


def _run(b: Builder, cands, **kw):
    prob = load_problem(b.s, b.search.id)
    return prob, propose(prob, [c.id for c in cands], time_limit=5, **kw)


def test_never_assumes_availability_for_non_submitters(b):
    c = b.candidate("Cand A", D1)
    ghost = b.person("Ghost", submitted=False)
    b.need(c, ghost)
    _, res = _run(b, [c])
    assert res.proposals == []
    assert "has not submitted availability" in res.unscheduled[0].reason


def test_meetings_stay_inside_submitted_availability(b):
    c = b.candidate("Cand A", D1)
    people = [b.person(f"P{i}", avail=[(D1, f"{9 + i % 3:02d}:00", f"{10 + i % 3:02d}:00")]) for i in range(5)]
    for p in people:
        b.need(c, p)
    prob, res = _run(b, [c])
    assert res.proposals
    for pr in res.proposals:
        assert covers(prob.participants[pr.participant_id].free, pr.start, pr.end)


def test_no_double_booking_of_candidate_or_faculty_across_candidates(b):
    a = b.candidate("Cand A", D1)
    c2 = b.candidate("Cand B", D1)  # same day: faculty must not be in two places
    people = [b.person(f"P{i}", avail=[(D1, "09:00", "12:00")]) for i in range(4)]
    for p in people:
        b.need(a, p)
        b.need(c2, p)
    _, res = _run(b, [a, c2])
    assert len(res.proposals) == 8
    for x, y in combinations(res.proposals, 2):
        if x.candidate_id == y.candidate_id or x.participant_id == y.participant_id:
            assert not overlaps(x.start, x.end, y.start, y.end), (x, y)


def test_fixed_events_and_buffers_are_respected(b):
    b.search.buffer_minutes = 15
    c = b.candidate("Cand A", D1)
    talk = b.event(c, D1, "10:00", "11:00")
    ps = [b.person(f"P{i}", avail=[(D1, "09:00", "12:00")]) for i in range(3)]
    for p in ps:
        b.need(c, p)
    _, res = _run(b, [c])
    items = sorted([(p.start, p.end) for p in res.proposals] + [(talk.start, talk.end)])
    for (s1, e1), (s2, e2) in zip(items, items[1:]):
        assert (s2 - e1).total_seconds() >= 15 * 60
    for p in res.proposals:
        assert not overlaps(p.start, p.end, talk.start, talk.end)


def test_event_attendees_are_busy_for_other_candidates(b):
    a = b.candidate("Cand A", D1)
    c2 = b.candidate("Cand B", D1)
    p = b.person("Busy", avail=[(D1, "09:00", "10:00")])
    b.event(a, D1, "09:00", "10:00", title="Lunch", attendees=[p])
    b.need(c2, p)
    _, res = _run(b, [c2])
    assert res.proposals == []
    assert "Lunch" in res.unscheduled[0].reason


def test_locked_meetings_survive_replan_and_unlocked_are_replaced(b):
    c = b.candidate("Cand A", D1)
    p1 = b.person("Locked", avail=[(D1, "09:00", "12:00")])
    p2 = b.person("Loose", avail=[(D1, "09:00", "12:00")])
    b.need(c, p1)
    b.need(c, p2)
    locked = b.meeting(c, p1, D1, "10:00", "10:30", locked=True)
    loose = b.meeting(c, p2, D1, "11:00", "11:30")
    _, res = _run(b, [c], mode="replan")
    assert locked.id not in res.removed_meeting_ids
    assert loose.id in res.removed_meeting_ids
    assert all(pr.participant_id != p1.id for pr in res.proposals)
    for pr in res.proposals:
        assert not overlaps(pr.start, pr.end, locked.start, locked.end)


def test_fill_mode_keeps_existing_meetings(b):
    c = b.candidate("Cand A", D1)
    p1 = b.person("A", avail=[(D1, "09:00", "12:00")])
    p2 = b.person("B", avail=[(D1, "09:00", "12:00")])
    b.need(c, p1)
    b.need(c, p2)
    b.meeting(c, p1, D1, "09:00", "09:30")
    _, res = _run(b, [c], mode="fill")
    assert res.removed_meeting_ids == []
    assert [p.participant_id for p in res.proposals] == [p2.id]


def test_insufficient_slot_is_explained(b):
    c = b.candidate("Cand B", D1)
    p = b.person("Professor X", avail=[(D1, "09:00", "09:20")])
    b.need(c, p, minutes=30)
    _, res = _run(b, [c])
    assert res.proposals == []
    assert "No compatible 30-minute slot was found for Professor X" in res.unscheduled[0].reason


def test_available_for_only_one_candidate(b):
    a = b.candidate("Cand A", D1)
    c2 = b.candidate("Cand B", D2)
    p = b.person("Only A", avail=[(D1, "09:00", "10:00")])
    for c in (a, c2):
        b.need(c, p)
    _, res = _run(b, [a, c2])
    assert [x.candidate_id for x in res.proposals] == [a.id]
    reason = next(u.reason for u in res.unscheduled if u.candidate_id == c2.id)
    assert "marked no availability" in reason


def test_overlapping_availability_competition_is_explained(b):
    c = b.candidate("Cand A", D1)
    p1 = b.person("Early 1", avail=[(D1, "09:00", "09:30")])
    p2 = b.person("Early 2", avail=[(D1, "09:00", "09:30")])
    b.need(c, p1)
    b.need(c, p2)
    _, res = _run(b, [c])
    assert len(res.proposals) == 1
    assert "needed for other meetings" in res.unscheduled[0].reason


def test_required_beats_optional(b):
    c = b.candidate("Cand A", D1)
    req = b.person("Required person", avail=[(D1, "09:00", "09:30")])
    opt = b.person("Optional person", avail=[(D1, "09:00", "09:30")])
    b.need(c, req, required=True)
    b.need(c, opt, required=False)
    _, res = _run(b, [c])
    assert [p.participant_id for p in res.proposals] == [req.id]


def test_preferred_objective_uses_preferred_time(b):
    c = b.candidate("Cand A", D1)
    p = b.person("Pref", avail=[(D1, "09:00", "11:00")], pref=[(D1, "11:00", "11:30")])
    b.need(c, p)
    _, res = _run(b, [c], objective="preferred")
    assert res.proposals[0].start == dt(D1, "11:00") and res.proposals[0].preferred


def test_open_slots_for_manual_scheduling(b):
    c = b.candidate("Cand A", D1)
    p = b.person("P", avail=[(D1, "09:00", "10:00")])
    slots = open_slots(load_problem(b.s, b.search.id), c.id, p.id, 30)
    assert [x[0] for x in slots] == [dt(D1, "09:00"), dt(D1, "09:15"), dt(D1, "09:30")]
