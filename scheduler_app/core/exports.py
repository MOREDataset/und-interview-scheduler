"""Itinerary exports: printable rows, PDF, CSV and iCalendar (.ics).

All exports work from an *itinerary snapshot* (the dict produced by
services.build_itinerary, or a published ScheduleVersion.snapshot), so a
published export always matches exactly what was published.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .timeutil import fmt_date, fmt_range

UND_GREEN = "#00704A"  # UND-inspired green (configure official palette in theme if needed)
INK = "#1D2A24"
MUTED = "#5B6B63"
BREAK_MIN = 10  # show gaps of at least this many minutes as "Break"


def _p(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def participants_text(item: dict) -> str:
    if item["participants"] and item["type"] == "meeting":
        return "; ".join(f"{p['name']}" + (f", {p['title']}" if p.get("title") else "") for p in item["participants"])
    bits = []
    if item.get("audience"):
        bits.append(item["audience"])
    if item["participants"]:
        bits.append(", ".join(p["name"] for p in item["participants"]))
    return "; ".join(bits) or "—"


def event_title(item: dict) -> str:
    if item["type"] == "meeting":
        return "Individual faculty meeting"
    return item["title"]


def day_rows(day: dict, include_breaks: bool = True) -> list[dict]:
    """Rows for one day: Time | Event | Participants | Location, with breaks filled in."""
    rows = []
    prev_end: datetime | None = None
    for it in day["items"]:
        s, e = _p(it["start"]), _p(it["end"])
        if include_breaks and prev_end and (s - prev_end) >= timedelta(minutes=BREAK_MIN):
            rows.append({"start": prev_end, "end": s, "time": fmt_range(prev_end, s), "event": "Break / free time",
                         "participants": "—", "location": "—", "kind": "gap"})
        rows.append({
            "start": s, "end": e, "time": fmt_range(s, e), "event": event_title(it),
            "participants": participants_text(it),
            "location": it.get("location") or "—", "kind": it["kind"],
            "note": it.get("note", ""),
        })
        prev_end = max(prev_end, e) if prev_end else e
    return rows


# ------------------------------------------------------------------ CSV
def itinerary_csv(snapshots: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Candidate", "Date", "Start", "End", "Event", "Participants", "Location", "Time zone"])
    for snap in snapshots:
        tz = snap["search"]["timezone"]
        for d in snap["days"]:
            for r in day_rows(d, include_breaks=False):
                w.writerow([snap["candidate"]["name"], d["date"], r["start"].strftime("%H:%M"), r["end"].strftime("%H:%M"),
                            r["event"], r["participants"], r["location"], tz])
    return buf.getvalue().encode("utf-8-sig")


def participant_csv(name: str, items: list[dict], tz: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Participant", "Candidate", "Date", "Start", "End", "Event", "Location", "Time zone"])
    for it in items:
        s, e = _p(it["start"]), _p(it["end"])
        w.writerow([name, it["candidate"], s.date().isoformat(), s.strftime("%H:%M"), e.strftime("%H:%M"),
                    event_title(it), it.get("location") or "", tz])
    return buf.getvalue().encode("utf-8-sig")


# ------------------------------------------------------------------ iCalendar
def _ics(events: list[dict], tz: str, calname: str) -> bytes:
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//UND Communication//Interview Scheduler//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calname)
    zone = ZoneInfo(tz)
    stamp = datetime.now(ZoneInfo("UTC"))
    for ev in events:
        e = Event()
        e.add("uid", ev["uid"])
        e.add("dtstamp", stamp)
        # Aware datetimes in America/Chicago: offsets (CDT/CST) are resolved per date.
        e.add("dtstart", _p(ev["start"]).replace(tzinfo=zone))
        e.add("dtend", _p(ev["end"]).replace(tzinfo=zone))
        e.add("summary", ev["summary"])
        if ev.get("location"):
            e.add("location", ev["location"])
        if ev.get("description"):
            e.add("description", ev["description"])
        cal.add_component(e)
    cal.add_missing_timezones()
    return cal.to_ical()


def candidate_ics(snap: dict) -> bytes:
    cid = snap["candidate"]["id"]
    evs = []
    for d in snap["days"]:
        for it in d["items"]:
            who = participants_text(it)
            evs.append({
                "uid": f"c{cid}-{it['type']}-{it['start']}-{'-'.join(str(p['id']) for p in it['participants'])}@und-interview-scheduler",
                "start": it["start"], "end": it["end"],
                "summary": f"{event_title(it)}" + (f" — {who}" if it["type"] == "meeting" else ""),
                "location": it.get("location", ""),
                "description": f"{snap['search']['department']} campus visit. Participants: {who}",
            })
    return _ics(evs, snap["search"]["timezone"], f"Campus visit — {snap['candidate']['name']}")


def participant_ics(name: str, items: list[dict], tz: str, department: str) -> bytes:
    evs = []
    for it in items:
        what = "Interview" if it["type"] == "meeting" else it["title"]
        evs.append({
            "uid": f"p-{it['type']}-{it['start']}-{it['candidate']}@und-interview-scheduler".replace(" ", "_"),
            "start": it["start"], "end": it["end"],
            "summary": f"{what}: {it['candidate']}",
            "location": it.get("location", ""),
            "description": f"{department} faculty search — candidate visit. Scheduled for {name}.",
        })
    return _ics(evs, tz, f"Candidate meetings — {name}")


# ------------------------------------------------------------------ PDF
def _styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "org": ParagraphStyle("org", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9,
                              textColor=HexColor(UND_GREEN), leading=11, spaceAfter=0),
        "unit": ParagraphStyle("unit", parent=base["Normal"], fontName="Helvetica", fontSize=9,
                               textColor=HexColor(MUTED), leading=11),
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20,
                                alignment=0, textColor=HexColor(INK), leading=24, spaceBefore=10, spaceAfter=2),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontSize=10.5, textColor=HexColor(MUTED), leading=14),
        "day": ParagraphStyle("day", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12.5,
                              textColor=HexColor(UND_GREEN), spaceBefore=14, spaceAfter=6, keepWithNext=1),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=9, leading=11.5, textColor=HexColor(INK)),
        "cellb": ParagraphStyle("cellb", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=11.5,
                                textColor=HexColor(INK)),
        "head": ParagraphStyle("head", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.5,
                               textColor=HexColor("#FFFFFF")),
        "note": ParagraphStyle("note", parent=base["Normal"], fontSize=9, textColor=HexColor(MUTED), leading=12),
    }


def _esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _header(st, org: str, unit: str, title: str, subtitle: str, logo_path: str | None):
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.colors import HexColor

    left = [Paragraph(_esc(org.upper()), st["org"]), Paragraph(_esc(unit), st["unit"])]
    flow = []
    if logo_path:
        try:
            img = Image(logo_path, width=1.3 * 72, height=0.55 * 72, kind="proportional")
            t = Table([[img, left]], colWidths=[1.5 * 72, None])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            flow.append(t)
        except Exception:
            flow += left
    else:
        flow += left
    rule = Table([[""]], colWidths=["100%"], rowHeights=[2])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(UND_GREEN))]))
    flow += [Spacer(1, 6), rule, Paragraph(_esc(title), st["title"]), Paragraph(subtitle, st["sub"]), Spacer(1, 4)]
    return flow


def _day_table(st, rows: list[dict]):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph(h, st["head"]) for h in ("TIME", "EVENT / MEETING", "PARTICIPANTS", "LOCATION")]]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(UND_GREEN)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, HexColor("#D5DDD8")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, r in enumerate(rows, start=1):
        gap = r["kind"] == "gap"
        data.append([
            Paragraph(_esc(r["time"]), st["cellb"]),
            Paragraph(_esc(r["event"]) + (f"<br/><font color='{MUTED}'>{_esc(r['note'])}</font>" if r.get("note") else ""),
                      st["cell"] if gap or r["kind"] == "meeting" else st["cellb"]),
            Paragraph(_esc(r["participants"]), st["cell"]),
            Paragraph(_esc(r["location"]), st["cell"]),
        ])
        if gap:
            style.append(("TEXTCOLOR", (0, i), (-1, i), HexColor(MUTED)))
            style.append(("BACKGROUND", (0, i), (-1, i), HexColor("#F4F6F5")))
    t = Table(data, colWidths=[1.25 * 72, 2.0 * 72, 2.2 * 72, 1.55 * 72], repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def _footer_fn(text: str):
    from reportlab.lib.colors import HexColor

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(HexColor(MUTED))
        canvas.drawString(doc.leftMargin, 0.45 * 72, text)
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.45 * 72, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def _tz_label(snap_days: list[dict], tz: str) -> str:
    abbrs = sorted({_p(d["items"][0]["start"]).replace(tzinfo=ZoneInfo(tz)).tzname() for d in snap_days if d["items"]})
    return f"All times are local Grand Forks time ({tz}{', ' + '/'.join(abbrs) if abbrs else ''})."


def candidate_pdf(snap: dict, org: str, unit: str, logo_path: str | None = None, draft: bool = False) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.75 * 72, rightMargin=0.75 * 72,
                            topMargin=0.6 * 72, bottomMargin=0.8 * 72,
                            title=f"Itinerary — {snap['candidate']['name']}", author=unit)
    dates = [date.fromisoformat(d["date"]) for d in snap["days"]]
    span = " & ".join(fmt_date(d) for d in dates) if dates else "Dates to be confirmed"
    title = "Campus Visit Itinerary" + (" — DRAFT" if draft else "")
    flow = _header(st, org, unit, title,
                   f"<b>{_esc(snap['candidate']['name'])}</b><br/>{_esc(snap['search']['name'])}<br/>{_esc(span)}", logo_path)
    if snap["candidate"].get("note"):
        flow += [Paragraph(_esc(snap["candidate"]["note"]), st["note"]), Spacer(1, 4)]
    for d in snap["days"]:
        rows = day_rows(d)
        heading = Paragraph(fmt_date(date.fromisoformat(d["date"])), st["day"])
        if rows:
            flow += [heading, _day_table(st, rows)]
        else:
            flow += [heading, Paragraph("Schedule to be confirmed.", st["note"])]
    flow += [Spacer(1, 12), Paragraph(_tz_label(snap["days"], snap["search"]["timezone"]), st["note"])]
    if snap["search"].get("contact_name") or snap["search"].get("contact_email"):
        flow.append(Paragraph(f"Questions about your visit: {_esc(snap['search'].get('contact_name', ''))} "
                              f"{_esc(snap['search'].get('contact_email', ''))}", st["note"]))
    ver = f"Version {snap['version']} · published {snap['published_at'][:10]}" if snap.get("version") else "Draft — not published"
    doc.build(flow, onFirstPage=_footer_fn(f"{unit} · {ver}"), onLaterPages=_footer_fn(f"{unit} · {ver}"))
    return buf.getvalue()


def participant_pdf(name: str, items: list[dict], tz: str, org: str, unit: str, search_name: str,
                    logo_path: str | None = None) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.75 * 72, rightMargin=0.75 * 72,
                            topMargin=0.6 * 72, bottomMargin=0.8 * 72, title=f"Schedule — {name}")
    flow = _header(st, org, unit, "Your Candidate Meetings", f"<b>{_esc(name)}</b><br/>{_esc(search_name)}", logo_path)
    by_day: dict[str, list[dict]] = {}
    for it in items:
        by_day.setdefault(it["start"][:10], []).append(it)
    if not by_day:
        flow.append(Paragraph("No published meetings yet.", st["note"]))
    for dkey, its in sorted(by_day.items()):
        rows = []
        for it in its:
            s, e = _p(it["start"]), _p(it["end"])
            rows.append({"time": fmt_range(s, e), "event": f"{'Meeting with' if it['type'] == 'meeting' else it['title'] + ' —'} "
                                                           f"{it['candidate']}",
                         "participants": participants_text(it), "location": it.get("location") or "—",
                         "kind": it["kind"]})
        flow += [Paragraph(fmt_date(date.fromisoformat(dkey)), st["day"]), _day_table(st, rows)]
    flow += [Spacer(1, 10), Paragraph(f"All times are local Grand Forks time ({tz}).", st["note"])]
    doc.build(flow, onFirstPage=_footer_fn(unit), onLaterPages=_footer_fn(unit))
    return buf.getvalue()


def master_pdf(snaps: list[dict], org: str, unit: str, logo_path: str | None = None) -> bytes:
    """Coordinator overview: every candidate's itinerary in one document."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.75 * 72, rightMargin=0.75 * 72,
                            topMargin=0.6 * 72, bottomMargin=0.8 * 72, title="Coordinator overview")
    flow = _header(st, org, unit, "Coordinator Overview — Draft schedules",
                   "Internal document. Contains all candidates.", logo_path)
    for i, snap in enumerate(snaps):
        if i:
            flow.append(PageBreak())
        flow.append(Paragraph(_esc(snap["candidate"]["name"]), st["title"]))
        for d in snap["days"]:
            flow += [Paragraph(fmt_date(date.fromisoformat(d["date"])), st["day"]), _day_table(st, day_rows(d))]
    doc.build(flow, onFirstPage=_footer_fn(f"{unit} · INTERNAL"), onLaterPages=_footer_fn(f"{unit} · INTERNAL"))
    return buf.getvalue()
