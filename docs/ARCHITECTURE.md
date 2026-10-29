# Architecture and design notes

## User journeys

| Who | Journey |
|---|---|
| Coordinator | Sign in → create candidates and visit days → add fixed events → add participants (or import a CSV) → mark who each candidate meets → send availability links → watch responses on the Overview → run the assistant / adjust in the workspace → fix blocking conflicts → publish → share links and exports |
| Faculty | Open personal link → see visit dates and what's being asked → paint availability (or type ranges) → Submit → come back to edit until the deadline → after publishing, see their meetings and add them to their calendar |
| Candidate | Open link → read the published itinerary → download PDF / .ics |

## Layers

```
Streamlit pages (ui/pages)  ──>  services.py  ──>  models.py (SQLAlchemy) ──> SQLite / PostgreSQL
           │                          │
           └── components (JS)        ├── problem.py  (plain snapshot)
                                      ├── conflicts.py (rules)
                                      ├── scheduler.py (CP-SAT)
                                      └── exports.py / notify.py
```

Pages never write to models directly; they call `services`, which validates and writes the audit
log, inside `ui.ctx.write()` (one transaction, rollback on error, friendly messages).

## Data model

| Table | Purpose |
|---|---|
| `searches` | One faculty search; holds defaults (time grid, meeting length, buffer, deadline, time zone). Any number of searches, any number of candidates per search. |
| `users` | Coordinator accounts (bcrypt passwords). Role column allows future roles. |
| `candidates` | Name, email, public note (shown to candidate), internal notes, status (draft / review / published), share token, version counter. |
| `visit_days` | One row per candidate day, with interview hours. Multi-day visits supported. |
| `locations` | Rooms / offices used on itineraries. |
| `participants` | Faculty, administrators, other representatives; office, usual meeting length, personal link token, submission time. |
| `availability` | Continuous blocks (`available` or `preferred`). No block on a responded date = unavailable. |
| `availability_responses` | Which dates a person answered (so "not available" differs from "didn't answer"). |
| `requirements` | Candidate × participant: required or optional, duration, optional location. |
| `fixed_events` (+ `event_attendees`) | Talks, meals, arrival, etc. Attendees are busy at that time. |
| `meetings` | Draft one-on-one meetings: time, location, locked flag, source (manual/auto), override reason, version counter. |
| `schedule_versions` | Immutable JSON snapshot per publication. Faculty and candidate views read only these. |
| `audit_log` | Append-only history of important actions. |
| `schema_info` | Schema version for future migrations. |

Times for meetings and events are stored as local wall-clock times in the search's time zone;
conversion to UTC happens only on export (iCalendar uses `TZID=America/Chicago` with an embedded
VTIMEZONE, so daylight saving is handled by calendar programs correctly).

## Key decisions and trade-offs

- **Streamlit Components v2 instead of third-party calendar widgets.** Existing Streamlit calendar
  wrappers are thinly maintained, and FullCalendar's resource/timeline views need a paid licence.
  Two small inline components (availability grid and timeline) give drag-to-paint and
  drag-to-move without a JavaScript build step. All rules are re-checked in Python, so the
  drag layer is only a convenience; everything can also be done with ordinary form controls.
- **Discrete start grid for the solver.** Starts snap to the configured grid (15 min). This keeps the
  model small (solves in well under a second for this size) and produces tidy itineraries.
- **Required meetings dominate the objective.** The objective choice decides the trade-off among
  preferred times, compactness and spreading across days. It never trades away a required meeting.
- **Publish = snapshot.** Simple, auditable, and guarantees that later draft edits can't leak.
- **Optimistic locking** (`version_id_col`) instead of row locks: works on SQLite and PostgreSQL
  and turns simultaneous edits into a clear message.
- **Personal links for faculty** instead of passwords: lowest effort for faculty. Links can be
  rotated. SSO can be added through Streamlit's OIDC support when UND IT provides a client.

## Possible next steps

- Alembic migrations once the schema starts changing in production.
- Microsoft 365 / Google calendar invitations through their APIs (needs university approval).
- Room availability checks if a room-booking API is available.
- A "max back-to-back meetings before a break" rule for long days.
