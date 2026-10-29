"""Workflow rules: manual edits, overrides, publishing, privacy, concurrency, persistence."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from scheduler_app.core import models as m
from scheduler_app.core import services as sv
from scheduler_app.core.db import get_sessionmaker, init_db, make_engine
from scheduler_app.core.problem import load_problem
from scheduler_app.core.scheduler import propose
from scheduler_app.core.seed import seed_demo

from .conftest import D1, D2, Builder, dt


def test_manual_meeting_refuses_double_booking(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    q = b.person("Q", avail=[(D1, "09:00", "12:00")])
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "09:00"), duration=30, location_id=None, actor="t")
    with pytest.raises(sv.ValidationError) as exc:
        sv.save_meeting(b.s, candidate=c, participant=q, start=dt(D1, "09:15"), duration=30, location_id=None, actor="t")
    assert not exc.value.needs_override


def test_override_needs_reason_and_is_audited(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "09:30")])
    with pytest.raises(sv.ValidationError) as exc:
        sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "10:00"), duration=30, location_id=None, actor="t")
    assert exc.value.needs_override
    mt = sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "10:00"), duration=30, location_id=None,
                         actor="coord@example.edu", override_reason="Agreed by email")
    assert mt.override_reason == "Agreed by email"
    actions = [a.action for a in b.s.scalars(select(m.AuditLog))]
    assert "availability_override" in actions and "meeting_saved" in actions


def test_publish_blocked_by_errors(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    q = b.person("Q", avail=[(D1, "09:00", "12:00")])
    b.meeting(c, p, D1, "09:00", "09:30")
    b.meeting(c, q, D1, "09:00", "09:30")
    with pytest.raises(sv.ValidationError):
        sv.publish(b.s, c, "t")


def test_published_snapshot_unaffected_by_later_draft_changes(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    mt = sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "09:00"), duration=30, location_id=None, actor="t")
    v1 = sv.publish(b.s, c, "t")
    assert not sv.has_unpublished_changes(c)
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "10:00"), duration=30, location_id=None, actor="t",
                    meeting=mt)
    assert sv.has_unpublished_changes(c)
    assert v1.snapshot["days"][0]["items"][0]["start"] == dt(D1, "09:00").isoformat()
    # Faculty view still shows the published time.
    items = sv.published_items_for_participant(c.search, p.id)
    assert items[0]["start"] == dt(D1, "09:00").isoformat()
    v2 = sv.publish(b.s, c, "t", "moved")
    assert v2.version_number == 2 and v2.snapshot["days"][0]["items"][0]["start"] == dt(D1, "10:00").isoformat()


def test_candidate_snapshot_has_no_private_data(b):
    a = b.candidate("A", D1)
    other = b.candidate("Other candidate", D1)
    a.internal_notes = "SECRET internal note"
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    b.meeting(a, p, D1, "09:00", "09:30")
    snap = sv.build_itinerary(a)
    text = repr(snap)
    assert "SECRET" not in text
    assert p.email not in text
    assert "Other candidate" not in text
    assert "available" not in text.lower()  # no availability data


def test_availability_closed_after_deadline(b):
    p = b.person("P", submitted=False)
    b.candidate("A", D1)
    b.search.availability_deadline = datetime.now() - timedelta(days=400)
    with pytest.raises(sv.ServiceError):
        sv.save_availability(b.s, p, {D1: "1" * 12}, actor=p.email)
    # the coordinator can still enter it on their behalf
    sv.save_availability(b.s, p, {D1: "1" * 12}, actor="coord", coordinator_override=True)
    assert p.submitted_at is not None


def test_availability_cells_round_trip(b):
    c = b.candidate("A", D1)
    p = b.person("P", submitted=False)
    cells = "0011112200000"[:12]
    sv.save_availability(b.s, p, {D1: cells}, actor=p.email)
    b.s.flush()
    b.s.expire_all()
    assert sv.participant_cells(p, b.search)[D1] == cells
    prob = load_problem(b.s, b.search.id)
    pp = prob.participants[p.id]
    assert pp.available == [(dt(D1, "09:30"), dt(D1, "10:30"))]
    assert pp.preferred == [(dt(D1, "10:30"), dt(D1, "11:00"))]


def test_resubmission_replaces_previous_availability(b):
    b.candidate("A", D1)
    p = b.person("P", submitted=False)
    sv.save_availability(b.s, p, {D1: "1" * 12}, actor=p.email)
    sv.save_availability(b.s, p, {D1: "0" * 12}, actor=p.email)
    b.s.flush()
    b.s.expire_all()
    assert p.availability == [] and p.submitted_at is not None
    assert [r.date for r in p.responses] == [D1]


def test_changes_after_generation_make_meeting_invalid(b):
    """If someone withdraws availability after a meeting was placed, it becomes a blocking issue."""
    c = b.candidate("A", D1)
    p = b.person("P", submitted=False)
    sv.save_availability(b.s, p, {D1: "1" * 12}, actor=p.email)
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "09:00"), duration=30, location_id=None, actor="t")
    sv.save_availability(b.s, p, {D1: "0" * 12}, actor=p.email)
    assert any(i.code == "outside_availability" for i in sv.blocking_issues(b.s, c))


def test_stale_proposal_is_refused(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "09:30")])
    q = b.person("Q", avail=[(D1, "09:00", "09:30")])
    b.need(c, p)
    res = propose(load_problem(b.s, b.search.id), [c.id], time_limit=5)
    # meanwhile someone books the candidate at that exact time
    sv.save_meeting(b.s, candidate=c, participant=q, start=dt(D1, "09:00"), duration=30, location_id=None, actor="x")
    with pytest.raises(sv.ServiceError):
        sv.apply_proposals(b.s, b.search.id, res, "t")


def test_concurrent_edit_detected_with_expected_version(b):
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    mt = sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "09:00"), duration=30, location_id=None, actor="t")
    seen = mt.version_id
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "10:00"), duration=30, location_id=None, actor="u",
                    meeting=mt, expected_version=seen)
    b.s.flush()
    with pytest.raises(sv.ConcurrencyError):
        sv.save_meeting(b.s, candidate=c, participant=p, start=dt(D1, "11:00"), duration=30, location_id=None,
                        actor="t", meeting=mt, expected_version=seen)


def test_concurrent_sessions_cannot_silently_overwrite(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'c.db'}")
    init_db(eng)
    Session = get_sessionmaker(eng)
    s1 = Session()
    b = Builder(s1)
    c = b.candidate("A", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    mid = b.meeting(c, p, D1, "09:00", "09:30").id
    s1.commit()
    a, z = Session(), Session()
    ma, mz = a.get(m.Meeting, mid), z.get(m.Meeting, mid)
    ma.notes = "first"
    a.commit()
    mz.notes = "second"
    with pytest.raises(StaleDataError):
        z.commit()
    for x in (s1, a, z):
        x.close()


def test_data_survives_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'persist.db'}"
    eng = make_engine(url)
    init_db(eng)
    s = get_sessionmaker(eng)()
    b = Builder(s)
    b.candidate("A", D1)
    p = b.person("P", submitted=False)
    sv.save_availability(s, p, {D1: "1111" + "0" * 8}, actor=p.email)
    s.commit()
    s.close()
    eng.dispose()
    eng2 = make_engine(url)  # simulate a server restart
    s2 = get_sessionmaker(eng2)()
    p2 = s2.scalar(select(m.Participant))
    assert p2.submitted_at is not None and len(p2.availability) == 1
    s2.close()


def test_demo_seed_is_fictional_and_idempotent(s):
    search = seed_demo(s)
    again = seed_demo(s)
    assert search.id == again.id and search.is_demo
    assert all(p.email.endswith("@example.edu") for p in search.participants)
    stats, _ = sv.search_stats(s, search)
    assert stats.participants == 14 and stats.submitted == 10


def test_csv_import_creates_updates_and_reports(b):
    a = b.candidate("Cand A", D1)
    b.candidate("Cand B", D2)
    raw = (
        "name,email,role,location,duration,required_for,optional_for\n"
        "New Person,new.person@example.edu,Professor,Room 1,30,Cand A,Cand B\n"
        "Everyone Person,every@example.edu,Lecturer,,45,all,\n"
        "Bad Row,not-an-email,,,,,\n"
        "New Person Renamed,NEW.PERSON@example.edu,Professor,,30,Unknown Cand,\n"
    ).encode()
    rep = sv.import_participants_csv(b.s, b.search, raw, "t")
    assert (rep.created, rep.updated) == (2, 1)
    assert any("isn't an email address" in e for e in rep.errors) and any("Unknown Cand" in e for e in rep.errors)
    names = sorted(p.name for p in b.search.participants)
    assert names == ["Everyone Person", "New Person Renamed"]
    req = {(r.participant.name, r.required) for r in a.requirements}
    assert ("New Person Renamed", True) in req and ("Everyone Person", True) in req


def test_demo_password_follows_current_setting(s, monkeypatch):
    from scheduler_app.core.auth import authenticate
    from scheduler_app.core.seed import DEMO_COORDINATOR_EMAIL, sync_demo_coordinator

    seed_demo(s)
    assert authenticate(s, DEMO_COORDINATOR_EMAIL, "demo-coordinator")
    monkeypatch.setenv("DEMO_COORDINATOR_PASSWORD", "a-new-secret-pw")
    sync_demo_coordinator(s)
    assert authenticate(s, DEMO_COORDINATOR_EMAIL, "a-new-secret-pw")
    monkeypatch.setenv("DEMO_COORDINATOR_PASSWORD", "short")
    sync_demo_coordinator(s)
    assert authenticate(s, DEMO_COORDINATOR_EMAIL, "demo-coordinator")
