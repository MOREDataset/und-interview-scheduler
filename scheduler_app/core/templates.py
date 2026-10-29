"""Search templates: save a whole search setup to a JSON file and load it again.

Useful for (a) starting a real search from a prepared file without retyping
everything, and (b) reusing last year's structure for the next search.

Format (all keys optional unless noted):
{
  "search": {"name"*, "department", "timezone", "slot_minutes", "start_step_minutes", "buffer_minutes",
             "default_meeting_minutes", "default_location", "availability_deadline" (YYYY-MM-DDTHH:MM),
             "coordinator_name", "coordinator_email"},
  "locations": [{"name"*, "details"}],
  "participants": [{"name"*, "email", "role", "location", "duration"}],
  "candidates": [{
      "name"*, "email", "affiliation", "public_note", "internal_notes",
      "days": [{"date"* (YYYY-MM-DD), "start", "end" (HH:MM), "kind": "interview"|"travel", "note"}],
      "events": [{"date"*, "start"*, "end"*, "title"*, "kind", "location", "audience", "note",
                  "host_label", "host", "needs_host", "rsvp", "attendees": [participant names]}],
      "meetings": [{"participant"*, "required", "duration", "location", "note"}],
      "draft_meetings": [{"participant"*, "date"*, "start"*, "end"*, "location", "reason"}]
  }]
}
Names (participants, locations) are matched case-insensitively within the file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from . import models as m
from .services import ValidationError, audit


DRAFT_NOTE = "Time taken from the itinerary draft (not confirmed yet)"


@dataclass
class TemplateReport:
    search: m.Search | None = None
    counts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _t(v: str | None, default: time | None = None) -> time | None:
    if not v:
        return default
    h, mi = str(v).strip().split(":")[:2]
    return time(int(h), int(mi))


def _d(v: str) -> date:
    return date.fromisoformat(str(v).strip())


def import_template(s: Session, raw: bytes | str, actor: str) -> TemplateReport:
    """Create a NEW search from a template. Never modifies existing searches."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"That file isn't valid JSON (line {exc.lineno}): {exc.msg}") from exc
    if not isinstance(data, dict) or not data.get("search", {}).get("name"):
        raise ValidationError("The template needs a \"search\" section with a \"name\".")
    rep = TemplateReport()
    sd = data["search"]
    search = m.Search(
        name=sd["name"], department=sd.get("department") or "Department of Communication",
        timezone=sd.get("timezone") or "America/Chicago", slot_minutes=int(sd.get("slot_minutes") or 15),
        start_step_minutes=int(sd["start_step_minutes"]) if sd.get("start_step_minutes") else None,
        buffer_minutes=int(sd.get("buffer_minutes") or 0),
        default_meeting_minutes=int(sd.get("default_meeting_minutes") or 30),
        availability_deadline=datetime.fromisoformat(sd["availability_deadline"]) if sd.get("availability_deadline") else None,
        coordinator_name=sd.get("coordinator_name"), coordinator_email=sd.get("coordinator_email"),
    )
    s.add(search)
    s.flush()

    locs: dict[str, m.Location] = {}

    def loc(name: str | None) -> int | None:
        if not name:
            return None
        key = name.strip().lower()
        if key not in locs:
            locs[key] = m.Location(search_id=search.id, name=name.strip())
            s.add(locs[key])
            s.flush()
        return locs[key].id

    for l in data.get("locations", []):
        lid = loc(l["name"])
        locs[l["name"].strip().lower()].details = l.get("details")
    if sd.get("default_location"):
        search.default_location_id = loc(sd["default_location"])

    people: dict[str, m.Participant] = {}
    for p in data.get("participants", []):
        part = m.Participant(search_id=search.id, name=p["name"].strip(), email=(p.get("email") or "").strip(),
                             role_title=p.get("role"), location_id=loc(p.get("location")),
                             preferred_duration=int(p["duration"]) if p.get("duration") else None)
        s.add(part)
        people[part.name.lower()] = part
    s.flush()

    def person(name: str, where: str) -> m.Participant | None:
        hit = people.get(name.strip().lower())
        if hit is None:
            rep.warnings.append(f"{where}: “{name}” is not in the participants list — skipped.")
        return hit

    n_ev = n_meet = n_draft = 0
    for cd in data.get("candidates", []):
        c = m.Candidate(search_id=search.id, name=cd["name"], email=cd.get("email"), affiliation=cd.get("affiliation"),
                        public_note=cd.get("public_note"), internal_notes=cd.get("internal_notes"))
        s.add(c)
        s.flush()
        for dd in cd.get("days", []):
            c.visit_days.append(m.VisitDay(date=_d(dd["date"]), start_time=_t(dd.get("start"), time(8, 30)),
                                           end_time=_t(dd.get("end"), time(17, 0)),
                                           kind="travel" if dd.get("kind") == "travel" else "interview",
                                           note=dd.get("note")))
        s.flush()
        day_set = {v.date for v in c.visit_days}
        for ev in cd.get("events", []):
            d = _d(ev["date"])
            if d not in day_set:
                rep.warnings.append(f"{c.name}: event “{ev['title']}” is on {d}, which isn't a visit day — skipped.")
                continue
            e = m.FixedEvent(title=ev["title"], kind=ev.get("kind") or "other",
                             start=datetime.combine(d, _t(ev["start"])), end=datetime.combine(d, _t(ev["end"])),
                             location_id=loc(ev.get("location")), audience=ev.get("audience"),
                             public_note=ev.get("note"), host_label=ev.get("host_label"), host=ev.get("host"),
                             needs_host=bool(ev.get("needs_host")), rsvp_open=bool(ev.get("rsvp")))
            e.attendees = [p for p in (person(n, f"{c.name} / {ev['title']}") for n in ev.get("attendees", [])) if p]
            c.fixed_events.append(e)
            n_ev += 1
        for mt in cd.get("meetings", []):
            p = person(mt["participant"], f"{c.name} meeting list")
            if p is None:
                continue
            c.requirements.append(m.Requirement(participant_id=p.id, required=mt.get("required", True) is not False,
                                                duration_minutes=int(mt.get("duration") or p.preferred_duration
                                                                     or search.default_meeting_minutes),
                                                location_id=loc(mt.get("location")), public_note=mt.get("note")))
            n_meet += 1
        s.flush()
        for dm in cd.get("draft_meetings", []):
            p = person(dm["participant"], f"{c.name} draft meeting")
            if p is None:
                continue
            d = _d(dm["date"])
            req = next((r for r in c.requirements if r.participant_id == p.id), None)
            c.meetings.append(m.Meeting(
                participant_id=p.id, requirement_id=req.id if req else None,
                start=datetime.combine(d, _t(dm["start"])), end=datetime.combine(d, _t(dm["end"])),
                location_id=loc(dm.get("location")) or (req.location_id if req else None) or p.location_id
                or search.default_location_id,
                override_reason=dm.get("reason") or DRAFT_NOTE,
                source="manual", updated_by=actor))
            n_draft += 1
        s.flush()
    rep.search = search
    rep.counts = {"candidates": len(data.get("candidates", [])), "participants": len(people), "locations": len(locs),
                  "events": n_ev, "meetings to schedule": n_meet, "draft meetings": n_draft}
    audit(s, search.id, actor, "template_imported",
          f"Created search “{search.name}” from a template ({n_ev} events, {len(people)} participants)",
          details=rep.counts)
    return rep


def export_template(search: m.Search) -> dict:
    """The search's setup as a template (no availability, no audit history, no links)."""
    ln = {l.id: l.name for l in search.locations}
    pn = {p.id: p.name for p in search.participants}
    out = {
        "search": {
            "name": search.name, "department": search.department, "timezone": search.timezone,
            "slot_minutes": search.slot_minutes, "start_step_minutes": search.start_step_minutes,
            "buffer_minutes": search.buffer_minutes, "default_meeting_minutes": search.default_meeting_minutes,
            "default_location": ln.get(search.default_location_id),
            "availability_deadline": search.availability_deadline.isoformat(timespec="minutes")
            if search.availability_deadline else None,
            "coordinator_name": search.coordinator_name, "coordinator_email": search.coordinator_email,
        },
        "locations": [{"name": l.name, "details": l.details} for l in search.locations],
        "participants": [{"name": p.name, "email": p.email or None, "role": p.role_title,
                          "location": ln.get(p.location_id), "duration": p.preferred_duration}
                         for p in search.participants],
        "candidates": [],
    }
    for c in search.candidates:
        out["candidates"].append({
            "name": c.name, "email": c.email, "affiliation": c.affiliation, "public_note": c.public_note,
            "days": [{"date": d.date.isoformat(), "start": d.start_time.strftime("%H:%M"),
                      "end": d.end_time.strftime("%H:%M"), "kind": d.kind or "interview", "note": d.note}
                     for d in c.visit_days],
            "events": [{"date": e.start.date().isoformat(), "start": e.start.strftime("%H:%M"),
                        "end": e.end.strftime("%H:%M"), "title": e.title, "kind": e.kind,
                        "location": ln.get(e.location_id), "audience": e.audience, "note": e.public_note,
                        "host_label": e.host_label, "host": e.host, "needs_host": e.needs_host,
                        "rsvp": e.rsvp_open, "attendees": [a.name for a in e.attendees]}
                       for e in c.fixed_events],
            "meetings": [{"participant": pn[r.participant_id], "required": r.required,
                          "duration": r.duration_minutes, "location": ln.get(r.location_id), "note": r.public_note}
                         for r in c.requirements],
            "draft_meetings": [{"participant": pn[mt.participant_id], "date": mt.start.date().isoformat(),
                                "start": mt.start.strftime("%H:%M"), "end": mt.end.strftime("%H:%M"),
                                "location": ln.get(mt.location_id), "reason": mt.override_reason}
                               for mt in c.meetings],
        })
    return out
