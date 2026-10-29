"""Database tables (SQLAlchemy 2.0 ORM).

Time convention: every datetime column that describes a meeting or event is a
*naive local wall-clock time* in the search's time zone (America/Chicago by
default). Conversion to UTC / aware datetimes happens only at the edges
(calendar export), via ``timeutil.localize``. Audit/created timestamps are UTC.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> str:
    return secrets.token_urlsafe(24)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- roles / users
ROLE_COORDINATOR = "coordinator"
ROLE_FACULTY = "faculty"


class User(Base):
    """Someone who can sign in with a password (coordinators, optionally faculty)."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default=ROLE_COORDINATOR)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    participant_id: Mapped[int | None] = mapped_column(ForeignKey("participants.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- search setup
class Search(Base):
    """One faculty search (e.g. 'Assistant Professor, 2026'). Everything hangs off it,
    so the app can be reused for future searches with any number of candidates."""

    __tablename__ = "searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(200), default="Department of Communication")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    slot_minutes: Mapped[int] = mapped_column(Integer, default=15)
    default_meeting_minutes: Mapped[int] = mapped_column(Integer, default=30)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=5)
    availability_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    availability_open: Mapped[bool] = mapped_column(Boolean, default=True)
    coordinator_name: Mapped[str | None] = mapped_column(String(200))
    coordinator_email: Mapped[str | None] = mapped_column(String(255))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="Candidate.id"
    )
    participants: Mapped[list["Participant"]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="Participant.name"
    )
    locations: Mapped[list["Location"]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="Location.name"
    )


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id"))
    name: Mapped[str] = mapped_column(String(200))
    details: Mapped[str | None] = mapped_column(String(400))
    search: Mapped[Search] = relationship(back_populates="locations")


# ---------------------------------------------------------------- candidates
CANDIDATE_STATUSES = ["draft", "review", "published"]


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    public_note: Mapped[str | None] = mapped_column(Text)  # shown on the candidate itinerary
    internal_notes: Mapped[str | None] = mapped_column(Text)  # coordinator only
    status: Mapped[str] = mapped_column(String(16), default="draft")
    share_token: Mapped[str] = mapped_column(String(64), default=new_token, unique=True)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __mapper_args__ = {"version_id_col": version_id}

    search: Mapped[Search] = relationship(back_populates="candidates")
    visit_days: Mapped[list["VisitDay"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="VisitDay.date"
    )
    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    fixed_events: Mapped[list["FixedEvent"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="FixedEvent.start"
    )
    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="Meeting.start"
    )
    versions: Mapped[list["ScheduleVersion"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="ScheduleVersion.version_number"
    )


class VisitDay(Base):
    __tablename__ = "visit_days"
    __table_args__ = (UniqueConstraint("candidate_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time, default=time(8, 30))
    end_time: Mapped[time] = mapped_column(Time, default=time(17, 0))
    candidate: Mapped[Candidate] = relationship(back_populates="visit_days")


# ---------------------------------------------------------------- participants
class Participant(Base):
    """A faculty member, administrator, or other university representative."""

    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(255))
    role_title: Mapped[str | None] = mapped_column(String(200))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    preferred_duration: Mapped[int | None] = mapped_column(Integer)
    access_token: Mapped[str] = mapped_column(String(64), default=new_token, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)  # UTC
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version_id}

    search: Mapped[Search] = relationship(back_populates="participants")
    location: Mapped[Location | None] = relationship()
    availability: Mapped[list["Availability"]] = relationship(
        back_populates="participant", cascade="all, delete-orphan", order_by="Availability.start"
    )
    responses: Mapped[list["AvailabilityResponse"]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )


AVAILABLE = "available"
PREFERRED = "preferred"


class Availability(Base):
    """A continuous block of time a participant can meet. Absence of a block on a
    date they responded to means 'unavailable'."""

    __tablename__ = "availability"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    level: Mapped[str] = mapped_column(String(16), default=AVAILABLE)
    participant: Mapped[Participant] = relationship(back_populates="availability")


class AvailabilityResponse(Base):
    """Records that a participant answered for a specific date (even 'not available')."""

    __tablename__ = "availability_responses"
    __table_args__ = (UniqueConstraint("participant_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str | None] = mapped_column(String(500))
    participant: Mapped[Participant] = relationship(back_populates="responses")


# ---------------------------------------------------------------- requirements
class Requirement(Base):
    """'Candidate X should meet participant Y' (one-on-one)."""

    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("candidate_id", "participant_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    candidate: Mapped[Candidate] = relationship(back_populates="requirements")
    participant: Mapped[Participant] = relationship()
    location: Mapped[Location | None] = relationship()


# ---------------------------------------------------------------- fixed events
EVENT_KINDS = {
    "arrival": "Arrival",
    "departure": "Departure",
    "meal": "Meal / lunch",
    "break": "Break",
    "presentation": "Research presentation",
    "teaching": "Teaching demonstration",
    "leadership": "Meeting with leadership",
    "tour": "Campus tour",
    "other": "Other",
}

event_attendees = Table(
    "event_attendees",
    Base.metadata,
    Column("event_id", ForeignKey("fixed_events.id", ondelete="CASCADE"), primary_key=True),
    Column("participant_id", ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
)


class FixedEvent(Base):
    """A reserved block on a candidate's itinerary (talk, lunch, arrival...).
    Attendees listed here are also considered busy at that time."""

    __tablename__ = "fixed_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="other")
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    audience: Mapped[str | None] = mapped_column(String(300))  # e.g. "Department faculty & students"
    public_note: Mapped[str | None] = mapped_column(String(500))
    candidate: Mapped[Candidate] = relationship(back_populates="fixed_events")
    location: Mapped[Location | None] = relationship()
    attendees: Mapped[list[Participant]] = relationship(secondary=event_attendees)


# ---------------------------------------------------------------- meetings
class Meeting(Base):
    """A scheduled one-on-one meeting in the *draft* schedule."""

    __tablename__ = "meetings"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"))
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto
    override_reason: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version_id}

    candidate: Mapped[Candidate] = relationship(back_populates="meetings")
    participant: Mapped[Participant] = relationship()
    location: Mapped[Location | None] = relationship()


# ---------------------------------------------------------------- publishing / audit
class ScheduleVersion(Base):
    """An immutable snapshot of a candidate's itinerary at publication time.
    Faculty and candidate views read ONLY from the latest snapshot."""

    __tablename__ = "schedule_versions"
    __table_args__ = (UniqueConstraint("candidate_id", "version_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_by: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(String(500))
    snapshot: Mapped[dict] = mapped_column(JSON)
    candidate: Mapped[Candidate] = relationship(back_populates="versions")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    search_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict | None] = mapped_column(JSON)


class SchemaInfo(Base):
    """Tiny table that records the schema version (for future migrations)."""

    __tablename__ = "schema_info"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
