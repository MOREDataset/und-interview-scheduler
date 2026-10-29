"""Travel days, logistics, sign-ups, copying, templates, logos and safe upgrades."""
from __future__ import annotations

import io
import json
from datetime import date, time
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from scheduler_app.core import exports, models as m
from scheduler_app.core import services as sv
from scheduler_app.core.branding import load_brand, prepare_logo
from scheduler_app.core.conflicts import detect
from scheduler_app.core.db import get_sessionmaker, init_db, make_engine
from scheduler_app.core.problem import load_problem
from scheduler_app.core.templates import export_template, import_template
from scheduler_app.core.timeutil import fmt_span

from .conftest import D1, D2, Builder, dt

EXAMPLE = Path(__file__).resolve().parents[1] / "templates" / "example-search.json"
D0 = date(2030, 3, 3)


@pytest.fixture()
def b(s):
    return Builder(s)


def _travel(b, c, d=D0):
    c.visit_days.append(m.VisitDay(date=d, start_time=time(14), end_time=time(20), kind="travel"))
    b.s.flush()


# ------------------------------------------------------------------ travel days
def test_travel_days_hold_events_but_never_meetings(b):
    c = b.candidate("Cand", D1)
    _travel(b, c)
    p = b.person("P", avail=[(D0, "14:00", "20:00"), (D1, "09:00", "12:00")])
    b.need(c, p)
    prob = load_problem(b.s, b.search.id)
    assert [d.date for d in prob.days_for(c.id)] == [D1]  # the scheduler only sees interview days
    assert sv.visit_dates(b.search) == [D1]
    assert sv.visit_dates(b.search, interview_only=False) == [D0, D1]
    b.meeting(c, p, D0, "15:00", "15:30")
    codes = {i.code for i in detect(load_problem(b.s, b.search.id))}
    assert "not_visit_day" in codes


def test_itinerary_includes_travel_day_and_details(b):
    c = b.candidate("Cand", D1)
    _travel(b, c)
    c.visit_days[0].note = "Breakfast at the hotel."
    c.affiliation = "Instructor, Example College"
    sv.save_event(b.s, c, "t", event=None, title="Airport pick-up", kind="arrival", start=dt(D0, "14:30"),
                  end=dt(D0, "15:00"), location_id=None, audience=None, public_note=None, attendee_ids=[],
                  host_label="Driver", host="Pat Driver", needs_host=True)
    snap = sv.build_itinerary(c)
    assert [d["kind"] for d in snap["days"]] == ["travel", "interview"]
    assert snap["candidate"]["affiliation"] == "Instructor, Example College"
    row = exports.day_rows(snap["days"][0])[0]
    assert row["host"] == "Driver: Pat Driver"
    assert exports.day_heading(snap["days"][0]).endswith("Travel day")
    pdf = exports.candidate_pdf(snap, "Org", "Unit")
    assert pdf.startswith(b"%PDF")


def test_host_line_wording():
    assert exports.host_line({"host_label": "Escorted by", "host": "Pat"}) == "Escorted by Pat"
    assert exports.host_line({"host_label": "Driver", "host": "Pat"}) == "Driver: Pat"
    assert exports.host_line({"host_label": "Driver", "host": ""}) == ""


def test_fmt_span():
    assert fmt_span([date(2026, 11, 9), date(2026, 11, 10)]) == "November 9–10, 2026"
    assert fmt_span([date(2026, 11, 9)]) == "Monday, November 9, 2026"
    assert fmt_span([]) == "Dates to be confirmed"


# ------------------------------------------------------------------ logistics and sign-ups
def test_open_logistics_and_rsvp(b):
    c = b.candidate("Cand", D1)
    p = b.person("Faculty")
    e = b.event(c, D1, "10:00", "11:00", "Talk")
    e.rsvp_open = True
    pick = b.event(c, D1, "08:40", "08:55", "Pick-up")
    pick.needs_host, pick.host_label = True, "Driver"
    b.s.flush()
    assert sv.open_logistics(b.search) == [pick]
    pick.host = "Someone"
    assert sv.open_logistics(b.search) == []

    sv.set_rsvp(b.s, p, e, True, "t")
    assert p in e.attendees
    # Visible on the person's own page straight away, before anything is published.
    items = sv.published_items_for_participant(b.search, p.id, include_signups=True)
    assert [i["title"] for i in items] == ["Talk"]
    assert sv.published_items_for_participant(b.search, p.id) == []
    sv.set_rsvp(b.s, p, e, False, "t")
    assert p not in e.attendees
    e.rsvp_open = False
    with pytest.raises(sv.ServiceError):
        sv.set_rsvp(b.s, p, e, True, "t")


def test_participant_without_email(b):
    p = sv.save_participant(b.s, b.search, "t", participant=None, name="Guest", email="", role_title=None,
                            location_id=None, preferred_duration=None)
    q = sv.save_participant(b.s, b.search, "t", participant=None, name="Guest 2", email="  ", role_title=None,
                            location_id=None, preferred_duration=None)
    assert p.email == "" and q.email == ""
    with pytest.raises(sv.ValidationError):
        sv.save_participant(b.s, b.search, "t", participant=None, name="Bad", email="nope", role_title=None,
                            location_id=None, preferred_duration=None)


# ------------------------------------------------------------------ copying
def test_clone_candidate_copies_structure_not_signups(b):
    src = b.candidate("Cand 1", D1, D2)
    p, q = b.person("P"), b.person("Q")
    b.need(src, p)
    ev = b.event(src, D2, "10:00", "11:00", "Talk", attendees=[p])
    ev.rsvp_open = True
    fixed = b.event(src, D1, "09:00", "09:30", "Chair meeting", attendees=[q])
    b.s.flush()
    new = sv.clone_candidate(b.s, src, "Cand 2", date(2030, 3, 11), "t")
    assert [d.date for d in new.visit_days] == [date(2030, 3, 11), date(2030, 3, 12)]
    assert {r.participant_id for r in new.requirements} == {p.id}
    by_title = {e.title: e for e in new.fixed_events}
    assert by_title["Talk"].start == dt(date(2030, 3, 12), "10:00") and by_title["Talk"].attendees == []
    assert by_title["Chair meeting"].attendees == [q]
    assert fixed.candidate_id == src.id  # source untouched


def test_copy_events_needs_matching_day_count(b):
    a = b.candidate("A", D1, D2)
    c = b.candidate("C", date(2030, 4, 1))
    b.event(a, D1, "09:00", "10:00")
    with pytest.raises(sv.ValidationError):
        sv.copy_events(b.s, a, c, "t")


# ------------------------------------------------------------------ templates
def test_example_template_round_trip(s):
    rep = import_template(s, EXAMPLE.read_text(), "t")
    assert not rep.warnings
    assert rep.counts["events"] == 5 and rep.counts["draft meetings"] == 1
    search = rep.search
    c = search.candidates[0]
    assert [d.kind for d in c.visit_days] == ["travel", "interview"]
    guest = next(p for p in search.participants if p.name == "Pat Guest")
    assert guest.email == ""
    # Draft times are kept but flagged for review rather than blocking.
    issues = detect(load_problem(s, search.id))
    draft = [i for i in issues if i.code == "availability_override"]
    assert draft and all(i.severity == "warning" for i in draft)

    again = import_template(s, json.dumps(export_template(search)), "t")
    assert again.counts == rep.counts and again.search.id != search.id


def test_template_errors_are_friendly(s):
    with pytest.raises(sv.ValidationError):
        import_template(s, "{not json", "t")
    with pytest.raises(sv.ValidationError):
        import_template(s, json.dumps({"search": {}}), "t")
    rep = import_template(s, json.dumps({"search": {"name": "X"}, "candidates": [
        {"name": "C", "days": [{"date": "2030-01-01"}], "meetings": [{"participant": "Nobody"}]}]}), "t")
    assert any("Nobody" in w for w in rep.warnings)


# ------------------------------------------------------------------ logos
def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (3000, 600), (0, 112, 74, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_logo_upload_is_resized_and_used_in_pdf(s):
    data = prepare_logo(_png())
    from PIL import Image

    assert Image.open(io.BytesIO(data)).size[0] <= 1200
    sv.set_setting_blob(s, sv.LOGO_LIGHT, data, "t")
    assert load_brand(s).light == data and load_brand(s).dark is None
    snap = {"candidate": {"id": 1, "name": "C", "note": ""}, "search": {"name": "S", "department": "D",
            "timezone": "America/Chicago"}, "days": []}
    assert exports.candidate_pdf(snap, "Org", "Unit", data).startswith(b"%PDF")
    with pytest.raises(sv.ValidationError):
        prepare_logo(b"not an image")


# ------------------------------------------------------------------ upgrades
NEW_COLUMNS = {
    "searches": ["start_step_minutes", "default_location_id"],
    "candidates": ["affiliation"],
    "visit_days": ["kind", "note"],
    "requirements": ["public_note"],
    "fixed_events": ["host_label", "host", "needs_host", "rsvp_open"],
}


def test_old_database_is_upgraded_without_losing_data(tmp_path):
    """Simulate a database made by the first version: drop the new columns and table,
    then start the app again."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = make_engine(url)
    init_db(eng)
    with eng.begin() as c:
        for table, cols in NEW_COLUMNS.items():
            for col in cols:
                c.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
        c.execute(text("DROP TABLE app_settings"))
        c.execute(text("INSERT INTO searches (name, department, timezone, slot_minutes, default_meeting_minutes, "
                       "buffer_minutes, availability_open, is_demo, created_at) VALUES "
                       "('Old search', 'Dept', 'America/Chicago', 15, 30, 5, 1, 0, '2026-01-01')"))
        sid = c.execute(text("SELECT id FROM searches")).scalar()
        c.execute(text(f"INSERT INTO candidates (search_id, name, status, share_token, version_id, created_at) "
                       f"VALUES ({sid}, 'Old cand', 'draft', 'tok', 1, '2026-01-01')"))
        cid = c.execute(text("SELECT id FROM candidates")).scalar()
        c.execute(text(f"INSERT INTO visit_days (candidate_id, date, start_time, end_time) "
                       f"VALUES ({cid}, '2030-03-04', '09:00:00', '12:00:00')"))
    eng.dispose()

    eng = make_engine(url)
    init_db(eng)
    init_db(eng)  # running twice is harmless
    insp = inspect(eng)
    for table, cols in NEW_COLUMNS.items():
        have = {x["name"] for x in insp.get_columns(table)}
        assert set(cols) <= have, table
    assert insp.has_table("app_settings")
    s = get_sessionmaker(eng)()
    c = s.query(m.Candidate).one()
    assert c.name == "Old cand" and c.visit_days[0].is_interview
    assert sv.visit_dates(c.search) == [date(2030, 3, 4)]
    s.close()


def test_upgrade_alone_does_not_mark_published_itineraries_changed(b):
    c = b.candidate("Cand", D1)
    p = b.person("P", avail=[(D1, "09:00", "12:00")])
    b.need(c, p)
    b.meeting(c, p, D1, "09:00", "09:30")
    sv.publish(b.s, c, "t")
    v = sv.latest_version(c)
    # Strip the fields an older app version didn't write.
    old = json.loads(json.dumps(v.snapshot))
    old["candidate"].pop("affiliation")
    for d in old["days"]:
        d.pop("kind"), d.pop("note")
        for it in d["items"]:
            for k in ("host_label", "host", "location_details"):
                it.pop(k, None)
    v.snapshot = old
    b.s.flush()
    assert not sv.has_unpublished_changes(c)
