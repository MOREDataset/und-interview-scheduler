"""Business operations. Every function that changes data writes an audit entry.

UI code should call these functions rather than editing models directly, so that
validation, audit history and concurrency checks are applied consistently.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import models as m
from .conflicts import ERROR, Issue, detect
from .problem import PMeeting, load_problem
from .timeutil import fmt_date, fmt_range, merge, time_slots


class ServiceError(Exception):
    """A problem the user should see in plain language."""


class ConcurrencyError(ServiceError):
    pass


class ValidationError(ServiceError):
    def __init__(self, message: str, issues: list[Issue] | None = None, needs_override: bool = False):
        super().__init__(message)
        self.issues = issues or []
        self.needs_override = needs_override


# ------------------------------------------------------------------ audit
def audit(s: Session, search_id: int | None, actor: str, action: str, summary: str,
          entity: str | None = None, entity_id: int | None = None, details: dict | None = None) -> None:
    s.add(m.AuditLog(search_id=search_id, actor=actor, action=action, summary=summary,
                     entity=entity, entity_id=entity_id, details=details))


# ------------------------------------------------------------------ searches
def list_searches(s: Session) -> list[m.Search]:
    return list(s.scalars(select(m.Search).order_by(m.Search.created_at.desc())))


def visit_dates(search: m.Search, interview_only: bool = True) -> list[date]:
    """Dates faculty are asked about. Travel (arrival/departure-only) days are skipped."""
    return sorted({d.date for c in search.candidates for d in c.visit_days if d.is_interview or not interview_only})


def day_window(search: m.Search, d: date) -> tuple[time, time]:
    """Union of all candidates' interview hours on a date (what faculty are asked about)."""
    days = [vd for c in search.candidates for vd in c.visit_days if vd.date == d and vd.is_interview]
    if not days:
        return time(8, 0), time(17, 0)
    return min(v.start_time for v in days), max(v.end_time for v in days)


def availability_is_open(search: m.Search, now: datetime | None = None) -> bool:
    """`now` is naive local time in the search's time zone (defaults to the current time there)."""
    if now is None:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(search.timezone)).replace(tzinfo=None)
    if not search.availability_open:
        return False
    if search.availability_deadline and now > search.availability_deadline:
        return False
    return True


# ------------------------------------------------------------------ availability grid encoding
def intervals_to_cells(rows: list[m.Availability], d: date, start: time, end: time, step: int) -> str:
    """Encode one date as a string: one char per slot, '0' no, '1' available, '2' preferred."""
    slots = time_slots(start, end, step)
    out = []
    for t in slots:
        s0 = datetime.combine(d, t)
        e0 = s0 + timedelta(minutes=step)
        code = "0"
        for a in rows:
            if a.start <= s0 and e0 <= a.end:
                code = "2" if a.level == m.PREFERRED else max(code, "1")
        out.append(code)
    return "".join(out)


def cells_to_intervals(cells: str, d: date, start: time, step: int) -> list[tuple[datetime, datetime, str]]:
    """Decode a cells string back to continuous (start, end, level) blocks."""
    out: list[tuple[datetime, datetime, str]] = []
    base = datetime.combine(d, start)
    i = 0
    while i < len(cells):
        c = cells[i]
        if c not in "12":
            i += 1
            continue
        j = i
        while j < len(cells) and cells[j] == c:
            j += 1
        out.append((base + timedelta(minutes=i * step), base + timedelta(minutes=j * step),
                    m.PREFERRED if c == "2" else m.AVAILABLE))
        i = j
    return out


def describe_cells(cells: str, d: date, start: time, step: int) -> str:
    blocks = cells_to_intervals(cells, d, start, step)
    if not blocks:
        return "Not available"
    parts = [fmt_range(s, e) + (" ★ preferred" if lvl == m.PREFERRED else "") for s, e, lvl in blocks]
    return ", ".join(parts)


def save_availability(s: Session, participant: m.Participant, cells_by_date: dict[date, str],
                      actor: str, coordinator_override: bool = False) -> None:
    """Replace a participant's availability for the given dates in one transaction."""
    search = participant.search
    if not coordinator_override and not availability_is_open(search):
        raise ServiceError("Availability collection is closed. Please contact the search coordinator.")
    step = search.slot_minutes
    covered = []
    for d, cells in cells_by_date.items():
        start, _end = day_window(search, d)
        lo, hi = datetime.combine(d, time.min), datetime.combine(d, time.max)
        s.execute(delete(m.Availability).where(
            m.Availability.participant_id == participant.id, m.Availability.start >= lo, m.Availability.start <= hi))
        for bs, be, lvl in cells_to_intervals(cells, d, start, step):
            s.add(m.Availability(participant_id=participant.id, start=bs, end=be, level=lvl))
        resp = s.scalar(select(m.AvailabilityResponse).where(
            m.AvailabilityResponse.participant_id == participant.id, m.AvailabilityResponse.date == d))
        if resp is None:
            s.add(m.AvailabilityResponse(participant_id=participant.id, date=d))
        else:
            resp.submitted_at = m.utcnow()
        covered.append(fmt_date(d, long=False))
    participant.submitted_at = m.utcnow()
    audit(s, search.id, actor, "availability_submitted",
          f"Availability saved for {participant.name} ({', '.join(covered)})",
          "participant", participant.id, {"cells": {str(k): v for k, v in cells_by_date.items()}})


def participant_cells(participant: m.Participant, search: m.Search) -> dict[date, str]:
    out = {}
    for d in visit_dates(search):
        start, end = day_window(search, d)
        rows = [a for a in participant.availability if a.start.date() == d]
        out[d] = intervals_to_cells(rows, d, start, end, search.slot_minutes)
    return out


# ------------------------------------------------------------------ meetings
def default_location_id_for(req: m.Requirement | None, participant: m.Participant) -> int | None:
    """Where a one-on-one happens: the meeting's own room, else the person's office,
    else the search's default meeting room."""
    if req and req.location_id:
        return req.location_id
    return participant.location_id or participant.search.default_location_id


def check_meeting(s: Session, search_id: int, candidate_id: int, participant_id: int,
                  start: datetime, end: datetime, ignore_meeting_id: int | None,
                  override_reason: str | None) -> list[Issue]:
    """Return the issues that THIS meeting would cause (blocking and non-blocking)."""
    prob = load_problem(s, search_id)
    prob.meetings = [x for x in prob.meetings if x.id != ignore_meeting_id]
    probe = PMeeting(-999, candidate_id, participant_id, start, end, None, False, override_reason)
    prob.meetings.append(probe)
    return [i for i in detect(prob) if -999 in i.meeting_ids]


def save_meeting(s: Session, *, candidate: m.Candidate, participant: m.Participant, start: datetime,
                 duration: int, location_id: int | None, actor: str, override_reason: str | None = None,
                 meeting: m.Meeting | None = None, expected_version: int | None = None,
                 locked: bool | None = None, notes: str | None = None, source: str = "manual") -> m.Meeting:
    """Create or move a meeting after checking every hard rule.

    * Double-bookings, fixed-event overlaps and times outside the visit day are refused.
    * Times outside submitted availability need an override reason (recorded).
    """
    if duration <= 0:
        raise ValidationError("Duration must be positive.")
    end = start + timedelta(minutes=duration)
    if meeting is not None and expected_version is not None and meeting.version_id != expected_version:
        raise ConcurrencyError("Someone else changed this meeting while you were editing. "
                               "Reload the page to see the latest version.")
    reason = (override_reason or "").strip() or None
    issues = check_meeting(s, candidate.search_id, candidate.id, participant.id, start, end,
                           meeting.id if meeting else None, reason)
    blocking = [i for i in issues if i.severity == ERROR and i.code != "outside_availability"]
    if blocking:
        raise ValidationError(blocking[0].message, blocking)
    if any(i.code == "outside_availability" for i in issues):
        raise ValidationError(
            f"{participant.name} has not marked this time as available. Add an override reason to schedule it anyway.",
            issues, needs_override=True)
    is_new = meeting is None
    if is_new:
        req = s.scalar(select(m.Requirement).where(m.Requirement.candidate_id == candidate.id,
                                                   m.Requirement.participant_id == participant.id))
        meeting = m.Meeting(candidate_id=candidate.id, participant_id=participant.id,
                            requirement_id=req.id if req else None, source=source,
                            location_id=location_id if location_id is not None else default_location_id_for(req, participant))
        candidate.meetings.append(meeting)  # keeps the in-memory collection in sync
    else:
        meeting.location_id = location_id
    old = (meeting.start, meeting.end) if not is_new else None
    meeting.start, meeting.end = start, end
    meeting.override_reason = reason if reason and any(i.code == "availability_override" for i in issues) else None
    if locked is not None:
        meeting.locked = locked
    if notes is not None:
        meeting.notes = notes or None
    meeting.updated_by = actor
    s.flush()
    when = f"{fmt_date(start, long=False)} {fmt_range(start, end)}"
    if is_new:
        summary = f"Scheduled {participant.name} with {candidate.name}, {when}"
    elif old and old != (start, end):
        summary = f"Moved {participant.name} with {candidate.name} to {when}"
    else:
        summary = f"Updated meeting {participant.name} with {candidate.name}"
    audit(s, candidate.search_id, actor, "meeting_saved", summary, "meeting", meeting.id,
          {"start": start.isoformat(), "end": end.isoformat(), "override_reason": meeting.override_reason})
    if meeting.override_reason:
        audit(s, candidate.search_id, actor, "availability_override",
              f"Override for {participant.name}: {meeting.override_reason}", "meeting", meeting.id)
    return meeting


def delete_meeting(s: Session, meeting: m.Meeting, actor: str) -> None:
    cand = meeting.candidate
    audit(s, cand.search_id, actor, "meeting_deleted",
          f"Removed {meeting.participant.name} from {cand.name}'s draft "
          f"({fmt_date(meeting.start, long=False)} {fmt_range(meeting.start, meeting.end)})",
          "meeting", meeting.id)
    cand.meetings.remove(meeting)  # delete-orphan cascade removes the row
    s.flush()


def set_lock(s: Session, meeting: m.Meeting, locked: bool, actor: str) -> None:
    meeting.locked = locked
    audit(s, meeting.candidate.search_id, actor, "meeting_locked" if locked else "meeting_unlocked",
          f"{'Locked' if locked else 'Unlocked'} {meeting.participant.name} with {meeting.candidate.name}",
          "meeting", meeting.id)


def apply_proposals(s: Session, search_id: int, result, actor: str) -> int:
    """Write a reviewed scheduler proposal into the draft. Re-validates everything
    first, so a proposal that went stale (someone edited meanwhile) is refused."""
    removed = 0
    for mid in result.removed_meeting_ids:
        mt = s.get(m.Meeting, mid)
        if mt is None or mt.locked:
            continue
        mt.candidate.meetings.remove(mt)
        removed += 1
    s.flush()
    added = 0
    for p in result.proposals:
        cand = s.get(m.Candidate, p.candidate_id)
        part = s.get(m.Participant, p.participant_id)
        exists = s.scalar(select(m.Meeting).where(m.Meeting.candidate_id == p.candidate_id,
                                                  m.Meeting.participant_id == p.participant_id))
        if exists:
            continue
        try:
            save_meeting(s, candidate=cand, participant=part, start=p.start,
                         duration=int((p.end - p.start).total_seconds() // 60), location_id=None,
                         actor=actor, source="auto")
        except ValidationError as exc:
            raise ServiceError(f"The proposal is out of date ({exc}). Please run the assistant again.") from exc
        added += 1
    audit(s, search_id, actor, "auto_schedule_applied",
          f"Applied scheduling assistant proposal: {added} added, {removed} replaced",
          details={"objective": result.objective, "mode": result.mode})
    return added


# ------------------------------------------------------------------ itineraries / publishing
def _event_item(e: m.FixedEvent) -> dict:
    return {
        "type": "event", "kind": e.kind, "title": e.title,
        "start": e.start.isoformat(), "end": e.end.isoformat(),
        "participants": [{"id": a.id, "name": a.name, "title": a.role_title or ""} for a in e.attendees],
        "audience": e.audience or "",
        "location": e.location.name if e.location else "",
        "location_details": (e.location.details or "") if e.location else "",
        "note": e.public_note or "",
        "host_label": (e.host_label or "") if e.host else "",
        "host": e.host or "",
    }


def build_itinerary(candidate: m.Candidate) -> dict:
    """The candidate-safe itinerary content for the CURRENT DRAFT (no internal notes,
    no availability, no emails)."""
    days = []
    reqs = {r.participant_id: r for r in candidate.requirements}
    for vd in sorted(candidate.visit_days, key=lambda v: v.date):
        items = []
        for e in candidate.fixed_events:
            if e.start.date() == vd.date:
                items.append(_event_item(e))
        for mt in candidate.meetings:
            if mt.start.date() != vd.date:
                continue
            loc = mt.location or mt.participant.location
            req = reqs.get(mt.participant_id)
            items.append({
                "type": "meeting", "kind": "meeting", "title": "Individual meeting",
                "start": mt.start.isoformat(), "end": mt.end.isoformat(),
                "participants": [{"id": mt.participant.id, "name": mt.participant.name,
                                  "title": mt.participant.role_title or ""}],
                "audience": "",
                "location": loc.name if loc else "",
                "location_details": (loc.details or "") if loc else "",
                "note": (req.public_note or "") if req else "",
                "host_label": "", "host": "",
            })
        items.sort(key=lambda x: x["start"])
        days.append({"date": vd.date.isoformat(), "start": vd.start_time.isoformat(timespec="minutes"),
                     "end": vd.end_time.isoformat(timespec="minutes"), "items": items,
                     "kind": vd.kind or "interview", "note": vd.note or ""})
    search = candidate.search
    return {
        "candidate": {"id": candidate.id, "name": candidate.name, "note": candidate.public_note or "",
                      "affiliation": candidate.affiliation or ""},
        "search": {"name": search.name, "department": search.department, "timezone": search.timezone,
                   "contact_name": search.coordinator_name or "", "contact_email": search.coordinator_email or ""},
        "days": days,
    }


def latest_version(candidate: m.Candidate) -> m.ScheduleVersion | None:
    return candidate.versions[-1] if candidate.versions else None


def has_unpublished_changes(candidate: m.Candidate) -> bool:
    v = latest_version(candidate)
    if v is None:
        return bool(candidate.meetings or candidate.fixed_events)
    cur = build_itinerary(candidate)
    old = _normalized(v.snapshot)
    return old["days"] != cur["days"] or old["candidate"] != cur["candidate"]


_ITEM_DEFAULTS = {"location_details": "", "note": "", "host_label": "", "host": "", "audience": ""}


def _normalized(snap: dict) -> dict:
    """Fill in fields that snapshots published by older app versions don't have, so an
    upgrade alone never makes a published itinerary look changed."""
    days = [{"kind": "interview", "note": "", **d,
             "items": [{**_ITEM_DEFAULTS, **it} for it in d.get("items", [])]} for d in snap.get("days", [])]
    return {"days": days, "candidate": {"affiliation": "", **snap.get("candidate", {})}}


def blocking_issues(s: Session, candidate: m.Candidate) -> list[Issue]:
    prob = load_problem(s, candidate.search_id)
    return [i for i in detect(prob, candidate.id) if i.severity == ERROR]


def set_status(s: Session, candidate: m.Candidate, status: str, actor: str) -> None:
    if status not in m.CANDIDATE_STATUSES:
        raise ServiceError("Unknown status")
    if status == "published":
        raise ServiceError("Use Publish to publish an itinerary.")
    candidate.status = status
    audit(s, candidate.search_id, actor, "status_changed", f"{candidate.name}: status set to {status}",
          "candidate", candidate.id)


def publish(s: Session, candidate: m.Candidate, actor: str, note: str | None = None) -> m.ScheduleVersion:
    errs = blocking_issues(s, candidate)
    if errs:
        raise ValidationError(f"Resolve {len(errs)} blocking issue(s) before publishing: {errs[0].message}", errs)
    num = (s.scalar(select(func.max(m.ScheduleVersion.version_number))
                    .where(m.ScheduleVersion.candidate_id == candidate.id)) or 0) + 1
    snap = build_itinerary(candidate)
    snap["version"] = num
    snap["published_at"] = m.utcnow().isoformat(timespec="seconds")
    v = m.ScheduleVersion(candidate_id=candidate.id, version_number=num, published_by=actor, note=note, snapshot=snap)
    s.add(v)
    candidate.status = "published"
    s.flush()
    s.refresh(candidate)
    audit(s, candidate.search_id, actor, "published",
          f"Published itinerary v{num} for {candidate.name}" + (f" — {note}" if note else ""),
          "candidate", candidate.id, {"version": num})
    return v


def published_items_for_participant(search: m.Search, participant_id: int, include_signups: bool = False) -> list[dict]:
    """Faculty view: only PUBLISHED meetings/events that involve this person.
    With include_signups, group events the person signed up for are added right away
    (it was their own choice, so there is nothing to wait for)."""
    out = []
    for c in search.candidates:
        v = latest_version(c)
        if not v:
            continue
        for d in v.snapshot["days"]:
            for it in d["items"]:
                if any(p["id"] == participant_id for p in it["participants"]):
                    out.append({**it, "candidate": c.name, "version": v.version_number})
    if include_signups:
        seen = {(x["candidate"], x["start"], x.get("title")) for x in out if x["type"] == "event"}
        for e in rsvp_events(search):
            if any(a.id == participant_id for a in e.attendees) and \
                    (e.candidate.name, e.start.isoformat(), e.title) not in seen:
                out.append({**_event_item(e), "candidate": e.candidate.name, "version": None})
    return sorted(out, key=lambda x: x["start"])


# ------------------------------------------------------------------ participants import
@dataclass
class ImportReport:
    created: int
    updated: int
    errors: list[str]


def import_participants_csv(s: Session, search: m.Search, raw: bytes, actor: str) -> ImportReport:
    """CSV columns: name, email, role, location, duration, required_for, optional_for.

    `required_for` / `optional_for` contain candidate names separated by ';', or 'all'."""
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip().lower() for c in (reader.fieldnames or [])}
    if "name" not in cols:
        raise ServiceError("The CSV needs at least a 'name' column (and usually 'email').")
    created = updated = 0
    errors: list[str] = []
    cand_by_name = {c.name.lower(): c for c in search.candidates}
    loc_by_name = {l.name.lower(): l for l in search.locations}
    for n, row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        email = row.get("email", "")
        if not row.get("name"):
            errors.append(f"Row {n}: no name — skipped.")
            continue
        if email and "@" not in email:
            errors.append(f"Row {n}: “{email}” isn't an email address — skipped.")
            continue
        if email:  # match on email; people without one are matched on name
            p = s.scalar(select(m.Participant).where(m.Participant.search_id == search.id,
                                                     func.lower(m.Participant.email) == email.lower()))
        else:
            p = s.scalar(select(m.Participant).where(m.Participant.search_id == search.id,
                                                     func.lower(m.Participant.name) == row["name"].lower()))
        if p is None:
            p = m.Participant(search_id=search.id, name=row["name"], email=email)
            s.add(p)
            created += 1
        else:
            p.name = row["name"]
            updated += 1
        p.role_title = row.get("role") or p.role_title
        if row.get("duration"):
            try:
                p.preferred_duration = int(row["duration"])
            except ValueError:
                errors.append(f"Row {n}: duration '{row['duration']}' is not a number — ignored.")
        if row.get("location"):
            loc = loc_by_name.get(row["location"].lower())
            if loc is None:
                loc = m.Location(search_id=search.id, name=row["location"])
                s.add(loc)
                loc_by_name[row["location"].lower()] = loc
            p.location = loc
        s.flush()
        for field_name, required in (("required_for", True), ("optional_for", False)):
            val = row.get(field_name, "")
            if not val:
                continue
            targets = search.candidates if val.lower() == "all" else []
            if not targets:
                for nm in val.split(";"):
                    c = cand_by_name.get(nm.strip().lower())
                    if c is None:
                        errors.append(f"Row {n}: candidate '{nm.strip()}' not found.")
                    else:
                        targets.append(c)
            for c in targets:
                upsert_requirement(s, c, p, required, p.preferred_duration or search.default_meeting_minutes)
    audit(s, search.id, actor, "participants_imported",
          f"Imported participants from CSV: {created} new, {updated} updated")
    return ImportReport(created, updated, errors)


def upsert_requirement(s: Session, candidate: m.Candidate, participant: m.Participant,
                       required: bool, duration: int) -> m.Requirement:
    r = s.scalar(select(m.Requirement).where(m.Requirement.candidate_id == candidate.id,
                                             m.Requirement.participant_id == participant.id))
    if r is None:
        r = m.Requirement(candidate_id=candidate.id, participant_id=participant.id)
        s.add(r)
    r.required, r.duration_minutes = required, duration
    s.flush()
    return r


# ------------------------------------------------------------------ dashboard numbers
@dataclass
class SearchStats:
    participants: int
    submitted: int
    not_submitted: list[str]
    meetings: int
    required_total: int
    required_unscheduled: int
    errors: int
    warnings: int

    @property
    def pct_submitted(self) -> int:
        return round(100 * self.submitted / self.participants) if self.participants else 0


def invited_participants(search: m.Search) -> list[m.Participant]:
    """People whose availability we need: active and on at least one candidate's meeting list."""
    asked = {r.participant_id for c in search.candidates for r in c.requirements}
    return [p for p in search.participants if p.active and p.id in asked]


def search_stats(s: Session, search: m.Search) -> tuple[SearchStats, list[Issue]]:
    active = invited_participants(search)
    prob = load_problem(s, search.id)
    issues = detect(prob)
    scheduled = {(x.candidate_id, x.participant_id) for x in prob.meetings}
    req = [r for r in prob.requirements if r.required]
    stats = SearchStats(
        participants=len(active),
        submitted=sum(1 for p in active if p.submitted_at),
        not_submitted=[p.name for p in active if not p.submitted_at],
        meetings=len(prob.meetings),
        required_total=len(req),
        required_unscheduled=sum(1 for r in req if (r.candidate_id, r.participant_id) not in scheduled),
        errors=sum(1 for i in issues if i.severity == ERROR),
        warnings=sum(1 for i in issues if i.severity != ERROR),
    )
    return stats, issues


def free_windows_text(participant: m.Participant, d: date) -> str:
    rows = [(a.start, a.end) for a in participant.availability if a.start.date() == d]
    if not rows:
        return "—"
    return ", ".join(fmt_range(a, b) for a, b in merge(rows))


# ------------------------------------------------------------------ setup operations
def save_candidate(s: Session, search: m.Search, actor: str, *, candidate: m.Candidate | None, name: str,
                   email: str | None, public_note: str | None, internal_notes: str | None,
                   expected_version: int | None = None, affiliation: str | None = None) -> m.Candidate:
    if not name.strip():
        raise ValidationError("Candidate name is required.")
    if candidate is not None and expected_version is not None and candidate.version_id != expected_version:
        raise ConcurrencyError("Changed by someone else")
    new = candidate is None
    if new:
        candidate = m.Candidate(search_id=search.id, name=name.strip())
        s.add(candidate)
    candidate.name = name.strip()
    candidate.email = (email or "").strip() or None
    candidate.public_note = (public_note or "").strip() or None
    if affiliation is not None:
        candidate.affiliation = affiliation.strip() or None
    candidate.internal_notes = (internal_notes or "").strip() or None
    s.flush()
    audit(s, search.id, actor, "candidate_created" if new else "candidate_updated",
          f"{'Added' if new else 'Updated'} candidate {candidate.name}", "candidate", candidate.id)
    return candidate


def replace_visit_days(s: Session, candidate: m.Candidate, rows: list[tuple], actor: str) -> None:
    """rows: (date, start, end) or (date, start, end, kind, note)."""
    rows = [tuple(r) + (("interview", None)[len(r) - 3:] if len(r) < 5 else ()) for r in rows]
    seen = set()
    for d, a, b, *_ in rows:
        if d in seen:
            raise ValidationError(f"{fmt_date(d, long=False)} is listed twice.")
        if not (a and b) or b <= a:
            raise ValidationError(f"Check the hours for {fmt_date(d, long=False)}: the end must be after the start.")
        seen.add(d)
    for vd in list(candidate.visit_days):
        candidate.visit_days.remove(vd)
    s.flush()
    for d, a, b, kind, note in sorted(rows, key=lambda r: r[0]):
        candidate.visit_days.append(m.VisitDay(date=d, start_time=a, end_time=b,
                                               kind="travel" if kind == "travel" else "interview",
                                               note=(note or "").strip() or None))
    s.flush()
    audit(s, candidate.search_id, actor, "visit_days_updated",
          f"Visit days for {candidate.name}: " + (", ".join(fmt_date(r[0], long=False) for r in sorted(rows, key=lambda r: r[0])) or "none"),
          "candidate", candidate.id)


def save_event(s: Session, candidate: m.Candidate, actor: str, *, event: m.FixedEvent | None, title: str, kind: str,
               start: datetime, end: datetime, location_id: int | None, audience: str | None,
               attendee_ids: list[int], public_note: str | None = None, host_label: str | None = None,
               host: str | None = None, needs_host: bool = False, rsvp_open: bool = False) -> m.FixedEvent:
    if not title.strip():
        raise ValidationError("Give the event a title.")
    if end <= start:
        raise ValidationError("The event must end after it starts.")
    if start.date() not in {d.date for d in candidate.visit_days}:
        raise ValidationError("Fixed events must be on one of the candidate's visit days.")
    new = event is None
    if new:
        event = m.FixedEvent(candidate_id=candidate.id)
        candidate.fixed_events.append(event)
    event.title, event.kind, event.start, event.end = title.strip(), kind, start, end
    event.location_id, event.audience = location_id, (audience or "").strip() or None
    event.public_note = (public_note or "").strip() or None
    event.host_label = (host_label or "").strip() or None
    event.host = (host or "").strip() or None
    event.needs_host, event.rsvp_open = bool(needs_host), bool(rsvp_open)
    event.attendees = [s.get(m.Participant, i) for i in attendee_ids]
    s.flush()
    audit(s, candidate.search_id, actor, "event_saved",
          f"{'Added' if new else 'Updated'} “{event.title}” for {candidate.name} "
          f"({fmt_date(start, long=False)} {fmt_range(start, end)})", "event", event.id)
    return event


def delete_event(s: Session, event: m.FixedEvent, actor: str) -> None:
    cand = event.candidate
    audit(s, cand.search_id, actor, "event_deleted", f"Removed “{event.title}” from {cand.name}", "event", event.id)
    cand.fixed_events.remove(event)
    s.flush()


def save_requirements(s: Session, candidate: m.Candidate, rows: list[tuple], actor: str) -> None:
    """rows: (participant_id, 'Required'|'Optional'|'Not meeting', duration[, itinerary note])."""
    changed = 0
    for row in rows:
        pid, choice, dur = row[0], row[1], row[2]
        note = row[3] if len(row) > 3 else None
        req = next((r for r in candidate.requirements if r.participant_id == pid), None)
        if choice == "Not meeting":
            if req is not None:
                candidate.requirements.remove(req)
                changed += 1
            continue
        if dur is None or int(dur) < 5:
            raise ValidationError("Meeting durations must be at least 5 minutes.")
        if req is None:
            req = m.Requirement(participant_id=pid)
            candidate.requirements.append(req)
        if req.required != (choice == "Required") or req.duration_minutes != int(dur):
            changed += 1
        req.required, req.duration_minutes = choice == "Required", int(dur)
        if len(row) > 3:
            note = (note or "").strip() or None
            if note != req.public_note:
                changed += 1
            req.public_note = note
    s.flush()
    audit(s, candidate.search_id, actor, "requirements_updated",
          f"Updated meeting list for {candidate.name} ({changed} change(s))", "candidate", candidate.id)


def rotate_candidate_link(s: Session, candidate: m.Candidate, actor: str) -> None:
    candidate.share_token = m.new_token()
    audit(s, candidate.search_id, actor, "link_rotated", f"Issued a new itinerary link for {candidate.name}",
          "candidate", candidate.id)


def rotate_participant_link(s: Session, p: m.Participant, actor: str) -> None:
    p.access_token = m.new_token()
    audit(s, p.search_id, actor, "link_rotated", f"Issued a new availability link for {p.name}", "participant", p.id)


def delete_candidate(s: Session, candidate: m.Candidate, actor: str) -> None:
    audit(s, candidate.search_id, actor, "candidate_deleted", f"Deleted candidate {candidate.name}", "candidate", candidate.id)
    s.delete(candidate)
    s.flush()


def save_participant(s: Session, search: m.Search, actor: str, *, participant: m.Participant | None, name: str,
                     email: str, role_title: str | None, location_id: int | None, preferred_duration: int | None,
                     active: bool = True) -> m.Participant:
    email = (email or "").strip()
    if not name.strip():
        raise ValidationError("A name is required.")
    if email and "@" not in email:
        raise ValidationError(f"“{email}” doesn't look like an email address. Leave it blank if there isn't one.")
    dup = s.scalar(select(m.Participant).where(m.Participant.search_id == search.id,
                                               func.lower(m.Participant.email) == email.lower())) if email else None
    if dup is not None and dup is not participant:
        raise ValidationError(f"{email} is already in the participant list ({dup.name}).")
    new = participant is None
    if new:
        participant = m.Participant(search_id=search.id, name=name.strip(), email=email.strip())
        s.add(participant)
    participant.name, participant.email = name.strip(), email.strip()
    participant.role_title = (role_title or "").strip() or None
    participant.location_id, participant.preferred_duration = location_id, preferred_duration
    participant.active = active
    s.flush()
    audit(s, search.id, actor, "participant_saved", f"{'Added' if new else 'Updated'} participant {participant.name}",
          "participant", participant.id)
    return participant


def delete_participant(s: Session, p: m.Participant, actor: str) -> None:
    n = s.scalar(select(func.count()).select_from(m.Meeting).where(m.Meeting.participant_id == p.id))
    if n:
        raise ValidationError(f"{p.name} has {n} scheduled meeting(s). Remove those first, or mark them inactive instead.")
    for r in s.scalars(select(m.Requirement).where(m.Requirement.participant_id == p.id)):
        s.delete(r)
    audit(s, p.search_id, actor, "participant_deleted", f"Removed participant {p.name}", "participant", p.id)
    s.delete(p)
    s.flush()


# ------------------------------------------------------------------ logistics, RSVPs, copying
def open_logistics(search: m.Search) -> list[m.FixedEvent]:
    """Events that still need a driver / escort / host."""
    return [e for c in search.candidates for e in c.fixed_events if e.needs_host and not (e.host or "").strip()]


def rsvp_events(search: m.Search) -> list[m.FixedEvent]:
    return sorted([e for c in search.candidates for e in c.fixed_events if e.rsvp_open], key=lambda e: e.start)


def set_rsvp(s: Session, participant: m.Participant, event: m.FixedEvent, attending: bool, actor: str) -> None:
    if not event.rsvp_open:
        raise ServiceError("Sign-up for this event is closed.")
    here = participant in event.attendees
    if attending and not here:
        event.attendees.append(participant)
    elif not attending and here:
        event.attendees.remove(participant)
    else:
        return
    s.flush()
    audit(s, participant.search_id, actor, "rsvp",
          f"{participant.name} {'will attend' if attending else 'will not attend'} “{event.title}” "
          f"({event.candidate.name}, {fmt_date(event.start, long=False)})", "event", event.id)


def _shift(dt_: datetime, src_day: date, dst_day: date) -> datetime:
    return datetime.combine(dst_day, dt_.time()) if dt_.date() == src_day else dt_ + (dst_day - src_day)


def copy_events(s: Session, src: m.Candidate, dst: m.Candidate, actor: str, replace: bool = False) -> int:
    """Copy fixed events from one candidate to another, matching visit days in order
    (1st day → 1st day, 2nd → 2nd ...). Attendees, hosts and instructions come along."""
    sd = sorted(d.date for d in src.visit_days)
    dd = sorted(d.date for d in dst.visit_days)
    if not sd or not dd:
        raise ValidationError("Both candidates need visit days first.")
    if len(sd) != len(dd):
        raise ValidationError(f"{src.name} has {len(sd)} visit day(s) but {dst.name} has {len(dd)}. "
                              "Give them the same number of days first.")
    mapping = dict(zip(sd, dd))
    if replace:
        for e in list(dst.fixed_events):
            dst.fixed_events.remove(e)
        s.flush()
    n = 0
    for e in src.fixed_events:
        day = mapping.get(e.start.date())
        if day is None:
            continue
        ne = m.FixedEvent(title=e.title, kind=e.kind, start=_shift(e.start, e.start.date(), day),
                          end=_shift(e.end, e.start.date(), day), location_id=e.location_id, audience=e.audience,
                          public_note=e.public_note, host_label=e.host_label, host=e.host, needs_host=e.needs_host,
                          rsvp_open=e.rsvp_open)
        ne.attendees = [] if e.rsvp_open else list(e.attendees)  # RSVPs are per event, so start empty
        dst.fixed_events.append(ne)
        n += 1
    s.flush()
    audit(s, dst.search_id, actor, "events_copied", f"Copied {n} event(s) from {src.name} to {dst.name}",
          "candidate", dst.id)
    return n


def clone_candidate(s: Session, src: m.Candidate, name: str, first_day: date, actor: str) -> m.Candidate:
    """New candidate with the same visit structure as `src`, starting on `first_day`:
    visit days (same spacing, hours, travel/interview, notes), fixed events and meeting list."""
    if not src.visit_days:
        raise ValidationError(f"{src.name} has no visit days to copy.")
    c = save_candidate(s, src.search, actor, candidate=None, name=name, email=None,
                       public_note=src.public_note, internal_notes=None)
    base = min(d.date for d in src.visit_days)
    for vd in sorted(src.visit_days, key=lambda v: v.date):
        c.visit_days.append(m.VisitDay(date=first_day + (vd.date - base), start_time=vd.start_time,
                                       end_time=vd.end_time, kind=vd.kind, note=vd.note))
    for r in src.requirements:
        c.requirements.append(m.Requirement(participant_id=r.participant_id, required=r.required,
                                            duration_minutes=r.duration_minutes, location_id=r.location_id,
                                            public_note=r.public_note))
    s.flush()
    copy_events(s, src, c, actor)
    return c


# ------------------------------------------------------------------ app settings (logo)
LOGO_LIGHT = "logo_light"  # for white / light backgrounds (PDFs, itineraries)
LOGO_DARK = "logo_dark"  # for the dark sidebar


def get_setting_blob(s: Session, key: str) -> bytes | None:
    row = s.get(m.AppSetting, key)
    return row.blob_value if row else None


def set_setting_blob(s: Session, key: str, data: bytes | None, actor: str) -> None:
    row = s.get(m.AppSetting, key)
    if data is None:
        if row:
            s.delete(row)
    else:
        if row is None:
            row = m.AppSetting(key=key)
            s.add(row)
        row.blob_value = data
    audit(s, None, actor, "branding_updated", f"{'Removed' if data is None else 'Uploaded'} {key.replace('_', ' ')}")


# ------------------------------------------------------------------ setup progress
@dataclass
class Step:
    title: str
    done: bool
    hint: str
    page: str


def setup_steps(search: m.Search) -> list[Step]:
    cands = search.candidates
    invited = invited_participants(search)
    submitted = [p for p in invited if p.submitted_at]
    reqs = [r for c in cands for r in c.requirements if r.required]
    placed = {(mt.candidate_id, mt.participant_id) for c in cands for mt in c.meetings}
    return [
        Step("Add candidates and visit days", bool(cands) and all(c.visit_days for c in cands),
             "Name, dates, interview hours; mark arrival/departure-only days as travel days.", "candidates"),
        Step("Enter fixed events", bool(cands) and all(c.fixed_events for c in cands),
             "Talks, teaching demo, meals, pick-ups. Copy one candidate's events to the other.", "candidates"),
        Step("Add participants", bool(search.participants), "Faculty, administrators, guests (CSV import works).",
             "participants"),
        Step("Choose who meets whom", bool(reqs), "Required vs optional one-on-ones per candidate.", "candidates"),
        Step("Send availability links", bool(invited) and all(p.last_reminded_at or p.submitted_at for p in invited),
             "Everyone on a meeting list gets a personal link.", "responses"),
        Step("Collect availability", bool(invited) and len(submitted) >= 0.8 * len(invited),
             f"{len(submitted)} of {len(invited)} have answered.", "responses"),
        Step("Build the schedule", bool(reqs) and all((r.candidate_id, r.participant_id) in placed for r in reqs),
             "Use the scheduling assistant, then fine-tune in the workspace.", "assistant"),
        Step("Publish itineraries", bool(cands) and all(c.versions for c in cands),
             "Faculty and candidates only see published versions.", "itineraries"),
    ]
