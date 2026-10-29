"""Exports: time zones (including daylight saving), PDF, CSV."""
from __future__ import annotations

from datetime import date

from icalendar import Calendar

from scheduler_app.core import exports
from scheduler_app.core import services as sv
from scheduler_app.core.auth import hash_password, make_session_token, read_session_token, verify_password
from scheduler_app.core.timeutil import localize

from .conftest import dt

CDT_DAY = date(2026, 10, 30)  # before DST ends (Nov 1, 2026)
CST_DAY = date(2026, 11, 3)  # after


def test_localize_handles_dst():
    assert localize(dt(CDT_DAY, "10:00")).utcoffset().total_seconds() == -5 * 3600
    assert localize(dt(CST_DAY, "10:00")).utcoffset().total_seconds() == -6 * 3600


def _published(b):
    c = b.candidate("Cand A", CDT_DAY, CST_DAY)
    p = b.person("Prof P", avail=[(CDT_DAY, "09:00", "12:00"), (CST_DAY, "09:00", "12:00")])
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(CDT_DAY, "10:00"), duration=30, location_id=None, actor="t")
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(CST_DAY, "10:00"), duration=30, location_id=None, actor="t")
    return c, p, sv.publish(b.s, c, "t").snapshot


def test_ics_uses_correct_utc_offsets_across_dst(b):
    _, _, snap = _published(b)
    cal = Calendar.from_ical(exports.candidate_ics(snap))
    starts = sorted(e.decoded("dtstart") for e in cal.walk("VEVENT"))
    assert [s.utcoffset().total_seconds() / 3600 for s in starts] == [-5, -6]
    assert all(s.hour == 10 for s in starts)
    assert cal.walk("VTIMEZONE"), "calendar should embed the America/Chicago definition"


def test_pdf_and_csv_exports(b):
    c, p, snap = _published(b)
    pdf = exports.candidate_pdf(snap, "University of North Dakota", "Department of Communication")
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500
    csv_text = exports.itinerary_csv([snap]).decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("Candidate,Date,Start,End")
    assert "Prof P" in csv_text
    items = sv.published_items_for_participant(c.search, p.id)
    assert len(items) == 2
    assert exports.participant_pdf("Prof P", items, "America/Chicago", "U", "D", "S").startswith(b"%PDF")
    assert b"BEGIN:VCALENDAR" in exports.participant_ics("Prof P", items, "America/Chicago", "D")
    assert exports.master_pdf([snap], "U", "D").startswith(b"%PDF")


def test_breaks_are_inserted_between_items(b):
    _, _, snap = _published(b)
    c = b.candidate("Cand B", CDT_DAY)
    p = b.person("Q", avail=[(CDT_DAY, "09:00", "12:00")])
    sv.save_meeting(b.s, candidate=c, participant=p, start=dt(CDT_DAY, "09:00"), duration=30, location_id=None, actor="t")
    q = b.person("R", avail=[(CDT_DAY, "09:00", "12:00")])
    sv.save_meeting(b.s, candidate=c, participant=q, start=dt(CDT_DAY, "10:00"), duration=30, location_id=None, actor="t")
    rows = exports.day_rows(sv.build_itinerary(c)["days"][0])
    assert [r["kind"] for r in rows] == ["meeting", "gap", "meeting"]


def test_passwords_and_session_tokens():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)
    tok = make_session_token(7, "secret")
    assert read_session_token(tok, "secret") == 7
    assert read_session_token(tok, "other") is None
    assert read_session_token(tok[:-2] + "xx", "secret") is None
    assert read_session_token(make_session_token(7, "secret", hours=-1), "secret") is None
