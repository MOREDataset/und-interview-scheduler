# UND Communication — Faculty Interview Scheduler

A web app for coordinating on-campus interviews for faculty candidates in the
University of North Dakota Department of Communication. It is built with Python and Streamlit.

- **Faculty** open a personal link, drag across a time grid to mark when they're free, and press Submit.
- **The coordinator** sees who has answered, builds itineraries on a visual timeline (or lets the
  scheduling assistant propose one), fixes conflicts, and publishes.
- **Candidates** get a read-only link to their own published itinerary, with PDF and calendar downloads.

It also handles the parts of a campus visit around the one-on-ones: **travel days** (arrival and
departure), **drivers, escorts and hosts** that still need someone, **talks and meals faculty can sign
up for** (with head counts), copying one candidate's schedule to the next, and loading a whole search
from a **template file**.

> **Demo data:** the first time you run the app it loads a demonstration search. Every name, email,
> room and date in it is **fictional**, and the app shows a "DEMO DATA" banner while you're in it.
> See [Using it for a real search](#4-using-it-for-a-real-search) to switch it off.

---

## 1. Quick start on your own computer

You need Python 3.10 or newer.

```bash
# 1) get into the project folder
cd und-interview-scheduler

# 2) create a private Python environment (one time)
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

# 3) install the libraries (one time)
pip install -r requirements.txt

# 4) settings (one time): copy the example file
cp .env.example .env          # Windows: copy .env.example .env

# 5) start the app
streamlit run app.py
```

Your browser opens at <http://localhost:8501>. Sign in with the demo coordinator account:

| Email | Password |
|---|---|
| `coordinator@example.edu` | `demo-coordinator` |

You can change the demo password with `DEMO_COORDINATOR_PASSWORD` in `.env`.

To stop the app, press `Ctrl+C` in the terminal.

### A five-minute tour of the demo

1. **Overview**: the dashboard lists what needs attention, for example "4 participants have not
   submitted availability".
2. **Availability responses**: shows who answered. Click a row to see that person's grid.
   The reminder section prepares emails for the people who haven't answered.
3. **Participants**: select anyone and copy their personal link. Open it in a private window to
   see exactly what a faculty member sees.
4. **Scheduling assistant**: press **Generate proposal**, read the explanations for meetings that
   couldn't be placed, then press **Apply to draft**.
5. **Scheduling workspace**: drag a green meeting sideways to move it, click it to edit, or click
   an empty spot on a candidate's lane to add one. The app refuses double-bookings. If you move a
   meeting outside someone's availability, it asks you for a reason.
6. **Itineraries & export**: publish, download the PDF, calendar (.ics) and CSV files, and copy
   the candidate's link. **Coordinator exports** has a logistics sheet (drivers, escorts, head counts).
7. **Candidates → Fixed events**: open an event to see the logistics fields (role, person, "someone
   is needed") and the "let faculty sign up" switch.

---

## 2. Who sees what

| Person | How they get in | What they can see |
|---|---|---|
| Coordinator | Email + password (or university SSO if configured) | Everything in the search |
| Faculty / staff | Personal link `…/?invite=<token>` from the coordinator | Visit dates, **their own** availability, **their own published** meetings, and talks/meals they can sign up for |
| Candidate | Read-only link `…/?itinerary=<token>` | **Only their own published itinerary.** No internal notes, availability, emails or other candidates |

- Faculty never see other people's availability.
- Draft changes are never shown to faculty or candidates. They see the last **published**
  version until you publish again.
- If a link leaks, use **Issue a new link** (Participants page or Candidates page). The old link stops
  working immediately.

---

## 3. How the scheduling works

**Hard rules.** These are never broken automatically:

- A candidate can't be in two meetings at once.
- A faculty member can't be in two meetings at once. This is checked **across all candidates**.
- Meetings stay inside each visit day's interview hours.
- Meetings stay inside the time a person **actually submitted**. If someone hasn't answered, they
  are never treated as free.
- Fixed events (talks, meals, arrival, etc.) and locked meetings are never moved, and nothing is
  placed on top of them. People listed as attendees of a fixed event count as busy during it.
- The transition buffer (default 5 minutes) is kept between items.

**Interview days and travel days.** Each visit day is either an *interview day* (one-on-ones can be
placed inside its interview hours, and faculty are asked for availability on it) or a *travel day*
(arrival or departure only: it shows on the itinerary with its events, but nobody is asked for
availability and no meetings are placed on it). Fixed events can be outside the interview hours,
for example a 7 PM dinner.

**Start times.** The availability grid size (e.g. 15 minutes) and the start-time step for meetings
(e.g. every 10 minutes, so meetings can start at 9:40 or 10:10) are set separately in Search settings.

**Preferences** are scored. Required meetings always come first, then optional ones. After that,
the objective you pick decides the trade-off between preferred times and compact days with few gaps.
Meetings that follow directly after another item score higher, and multi-day visits are spread out.

The solver is Google OR-Tools CP-SAT. It returns a **proposal** that you review. Nothing is written
until you press **Apply to draft**. The proposal is refused if anything changed after it was generated.
For each meeting it couldn't place, it says why, for example:

> No compatible 25-minute slot was found for Dr. Harper Testwell on Dr. Avery Placeholder's visit
> date(s). Their free time inside interview hours is only: Thu Nov 12 9:00–9:15 AM.

**Manual overrides.** In the workspace you can schedule someone outside their availability, but
only with a written reason. The reason is shown on the meeting and stored in the audit history.

**Conflicts** are labelled **Blocking** (✕, prevents publishing) or **Warning** (▲, allowed).
Blocking: double-bookings, overlaps with fixed events, meetings outside the visit hours or outside
availability without a reason. Warnings: short transition time, overrides with a reason, and
required meetings not yet scheduled.

---

## 4. Using it for a real search

1. In `.env`, set `DEMO_MODE=false`, and set `SECRET_KEY` to a long random value:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Create your own coordinator account: **Search settings → Coordinators → Add a coordinator**, or
   `python manage.py create-coordinator your.name@und.edu "Your Name"`.
3. Start a search: **Search settings → New search** (blank, or from a template file; see below).
   You can delete the demo search on **Search settings → This search**.
4. The **Overview** page shows a step-by-step checklist and links to the next thing to do:
   1. **Candidates**: visit days (interview or travel day), fixed events, and who they should meet.
      **Copy another candidate's schedule** when you add the second candidate: days, events and the
      meeting list come along, shifted to the new dates.
   2. **Participants**: one at a time or by CSV import. Email is optional (for guests without a
      university address you can copy their personal link instead).
   3. **Availability responses**: send each person their personal link. The first message is an
      invitation, later ones are reminders.
   4. **Scheduling assistant**, then fine-tune in the **Scheduling workspace**.
   5. **Itineraries & export**: publish and share.
5. When the deadline passes, collection closes automatically. You can also close it by hand.

Set `APP_BASE_URL` to the address people will use, so that the links in emails are correct.

**Logistics and sign-ups.** When you edit a fixed event you can set a logistics role (Driver,
Airport pick-up, Escorted by, Tour by, ...), the person, and tick **Someone is needed**. Until a
person is filled in, the Overview lists it under *Needs attention*. Turn on **Let faculty sign up**
for talks and meals: faculty then see the event on their own page with an *I'll attend* switch, the
head count appears on the Overview, and the full list is in the logistics sheet.

**Templates.** **Search settings → Templates** downloads the whole setup of a search (rooms, people,
candidates, visit days, events, meeting lists and placed meetings) as a `.json` file, and loads such
a file as a new search. Loading never changes an existing search. A fictional example is in
[`templates/example-search.json`](templates/example-search.json). Meeting times typed in from a
draft itinerary can be included as `draft_meetings`; they are marked "not confirmed yet" and show
as warnings until people's availability confirms them. **Keep real template files out of this
repository** (they contain names and emails).

**Logo.** No university logo ships with the app. Download the official files from the
[UND brand downloads page](https://campus.und.edu/brand/downloads.html) (the *Digital PNG*
versions) and upload them under **Search settings → Logo**: the full-color version for white
backgrounds (PDFs, itineraries, sign-in page) and, optionally, the white/reverse version for the
dark sidebar. Follow the university's logo guidelines; for a department lockup, ask University
Marketing. The logo is stored in the database, so it survives redeploys. (`BRAND_LOGO_PATH` still
works as a fallback for PDFs.) The colours can be changed in `.streamlit/config.toml`.

**Email.** If you fill in the `SMTP_*` settings, reminders and itinerary links can be sent from
the app, but only when you press the send button. Without them, the app gives you ready-to-send
`mailto:` links that open in your own email program. Nothing is ever sent automatically.

---

## 5. Security notes

- Passwords are stored as bcrypt hashes. After 5 failed sign-ins, that email is locked for 15 minutes.
- Faculty and candidate links are long random tokens that can be rotated.
- A signed cookie keeps the coordinator signed in for 12 hours, so reloading the page doesn't sign
  you out. **Sign out** clears it.
- Secrets live in `.env` or `.streamlit/secrets.toml`. Both are excluded from git. Never commit them.
- **Simultaneous edits:** each meeting, candidate and participant has a version number. If two
  coordinators change the same record, the second save is refused with a "someone else changed
  this" message instead of silently overwriting.
- All permanent data is in the database. Nothing important lives in the browser session, so
  reloads and server restarts don't lose anything (this is covered by the automated tests).
- The **Audit history** page records availability submissions, meeting changes, overrides,
  publications, reminders, settings changes and sign-ins.
- **University SSO (optional):** if UND IT provides an OpenID Connect client, copy
  `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, fill it in, and a "Sign in with
  university SSO" button appears. The signed-in email must match a coordinator account.
- Candidate information is confidential. Don't deploy this on a public hosting service without
  sign-in in front of it, and talk to UND IT about where it should run.

---

## 6a. Putting it online with Streamlit Community Cloud (free)

Streamlit Cloud runs the app straight from this GitHub repository. **Important:** on Streamlit Cloud,
local files are wiped whenever the app restarts, so you must use a hosted PostgreSQL database. If you
use the default local file, submitted availability will eventually be lost.

**Step 1: create a free PostgreSQL database (about 3 minutes)**
1. Sign up at <https://neon.tech> (or <https://supabase.com>) and create a project.
2. Copy its *connection string*. It looks like
   `postgresql://user:password@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require`.

**Step 2: deploy the app**
1. Go to <https://share.streamlit.io>, sign in with GitHub, and click **Create app**.
2. Pick the repository `MOREDataset/und-interview-scheduler`, branch `main`, main file `app.py`.
3. Under **Advanced settings → Secrets**, paste the following, using your own values:

   ```toml
   DATABASE_URL = "postgresql://user:password@ep-xxxx.neon.tech/neondb?sslmode=require"
   SECRET_KEY = "paste-a-long-random-string-here"
   APP_BASE_URL = "https://your-app-name.streamlit.app"
   DEMO_MODE = "true"          # set to "false" when you start the real search
   DEMO_COORDINATOR_PASSWORD = "choose-a-password"   # sign in as coordinator@example.edu with THIS password (8+ characters)
   ```
   To get a random `SECRET_KEY`, run `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
4. Click **Deploy**. The tables are created automatically on first start.
5. Once you know the app's address, update `APP_BASE_URL` in Secrets so the faculty and candidate
   links point to the right place.

**Step 3: switch from demo to real use**
- In Secrets, set `DEMO_MODE = "false"`.
- To create your own coordinator account, run this once on your own computer with the same
  `DATABASE_URL` in your `.env`: `python manage.py create-coordinator you@und.edu "Your Name"`.
  Then delete the demo search on the **Search settings** page.

**Updating the app.** Push to `main`, then use **Manage app → Reboot** on Streamlit Cloud. When a new
version adds database columns, they are added automatically at start-up; existing data is never
changed or removed. (Tested against databases created by the first release on both PostgreSQL and
SQLite.)

**Privacy:** the app's pages need a sign-in or a secret link, but the app itself is on the public
internet. Keep the GitHub repository private, and check with UND IT whether hosting candidate
information on Streamlit Cloud and Neon is acceptable. If it isn't, use the Docker / university-server
route in section 6.

## 6. Deployment for several users

SQLite (the default) is fine on one computer. For a shared server, use PostgreSQL:

```bash
pip install -r requirements-postgres.txt
# in .env:
DATABASE_URL=postgresql+psycopg://scheduler_user:PASSWORD@db-host:5432/scheduler
APP_BASE_URL=https://scheduler.your-department.und.edu
python manage.py init-db
python manage.py create-coordinator you@und.edu "Your Name"
streamlit run app.py --server.port 8501 --server.headless true
```

Put it behind the university's HTTPS reverse proxy (nginx or Apache). A `Dockerfile` is included:

```bash
docker build -t und-interview-scheduler .
docker run -p 8501:8501 --env-file .env und-interview-scheduler
```

**Backups:** for SQLite, run `python manage.py backup backups/scheduler-YYYY-MM-DD.db`. For
PostgreSQL, use `pg_dump`.

**Database schema:** `python manage.py init-db` creates all tables and is safe to run repeatedly.
The schema version is recorded in the `schema_info` table. If the schema changes in the future,
add Alembic migrations (see `docs/ARCHITECTURE.md`).

---

## 7. Tests

```bash
python -m pytest
```

There are 55 tests. They cover the scheduling rules and the realistic scenarios from the brief:
- people available for only one candidate
- overlapping availability
- slots too short to fit a meeting
- locked meetings
- availability changing after a schedule was built
- people who haven't submitted
- two people editing the same record at once
- daylight-saving time in calendar files
- PDF, CSV and .ics exports
- data surviving a server restart
- travel days (never used for meetings), logistics, faculty sign-ups, copying a candidate
- template import/export round trip, logo upload
- upgrading a database created by an older version without losing data

---

## 8. Project layout

```
app.py                       Entry point and router (coordinator / faculty link / candidate link)
manage.py                    Command-line helpers (init-db, seed-demo, create-coordinator, backup)
scheduler_app/
  core/                      No Streamlit code here; easy to test and reuse
    models.py                Database tables (SQLAlchemy)
    db.py                    Engine, sessions, initialisation
    services.py              All operations that change data (+ audit log, validation, publishing)
    problem.py               Plain-Python snapshot of a search used by the rules below
    conflicts.py             Conflict detection (blocking vs. warning)
    scheduler.py             OR-Tools CP-SAT scheduling assistant + explanations
    exports.py               PDF (ReportLab), CSV and iCalendar
    notify.py                Email drafts and optional SMTP sending
    auth.py                  Passwords, link tokens, signed sign-in cookie
    seed.py                  Fictional demo data
    templates.py             Save / load a whole search as a JSON template
    branding.py              Uploaded logo handling
  ui/
    theme.py                 Visual design (typography, status chips, dashboard pieces)
    ctx.py                   Per-page context and safe "write" helper
    components/              Custom interactive pieces (no build step needed)
      avail_grid.py          Click-and-drag availability grid (mouse, touch, keyboard)
      timeline.py            Scheduling timeline with drag-to-move
    pages/                   One file per page
static/fonts/                Public Sans + Source Serif 4 (SIL Open Font License)
templates/example-search.json  Fictional example template
tests/                       Automated tests (pytest)
docs/ARCHITECTURE.md         Design notes, data model, trade-offs
```
