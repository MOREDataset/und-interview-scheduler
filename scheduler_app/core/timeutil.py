"""Time helpers: interval arithmetic, time-zone conversion and friendly formatting."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def localize(dt: datetime, tz: str = "America/Chicago") -> datetime:
    """Attach the search's time zone to a naive local datetime.

    zoneinfo applies the correct offset for that date, so a 10:00 meeting on a
    CDT date becomes UTC-5 and on a CST date becomes UTC-6.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo(tz))
    return dt.replace(tzinfo=ZoneInfo(tz))


def to_utc(dt: datetime, tz: str = "America/Chicago") -> datetime:
    return localize(dt, tz).astimezone(ZoneInfo("UTC"))


def combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Merge overlapping or touching intervals."""
    out: list[list[datetime]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def subtract(base: Sequence[Interval], cut: Iterable[Interval]) -> list[Interval]:
    """Remove every `cut` interval from the `base` intervals."""
    result = list(merge(base))
    for cs, ce in merge(cut):
        nxt: list[Interval] = []
        for s, e in result:
            if ce <= s or cs >= e:
                nxt.append((s, e))
                continue
            if s < cs:
                nxt.append((s, cs))
            if ce < e:
                nxt.append((ce, e))
        result = nxt
    return result


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s1, e1 in merge(a):
        for s2, e2 in merge(b):
            s, e = max(s1, s2), min(e1, e2)
            if s < e:
                out.append((s, e))
    return merge(out)


def covers(intervals: Sequence[Interval], start: datetime, end: datetime) -> bool:
    """True if [start, end) is fully inside the union of intervals."""
    return any(s <= start and end <= e for s, e in merge(intervals))


def minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def fmt_time(t: datetime | time) -> str:
    """9:00 AM style, without a leading zero."""
    h, m = t.hour, t.minute
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def fmt_range(s: datetime, e: datetime) -> str:
    """'9:00–9:30 AM' or '11:30 AM–12:15 PM'."""
    a, b = fmt_time(s), fmt_time(e)
    if a[-2:] == b[-2:]:
        return f"{a[:-3]}–{b}"
    return f"{a}–{b}"


def fmt_date(d: date | datetime, long: bool = True) -> str:
    if long:
        return f"{d:%A}, {d:%B} {d.day}, {d.year}"
    return f"{d:%a} {d:%b} {d.day}"


def fmt_span(dates: Sequence[date]) -> str:
    """'November 8–10, 2026' for consecutive days in one month, otherwise a short list."""
    ds = sorted(set(dates))
    if not ds:
        return "Dates to be confirmed"
    if len(ds) == 1:
        return fmt_date(ds[0])
    consecutive = all((b - a).days == 1 for a, b in zip(ds, ds[1:]))
    a, b = ds[0], ds[-1]
    if consecutive and (a.year, a.month) == (b.year, b.month):
        return f"{a:%B} {a.day}–{b.day}, {a.year}"
    if consecutive and a.year == b.year:
        return f"{a:%B} {a.day} – {b:%B} {b.day}, {a.year}"
    return ", ".join(f"{d:%b} {d.day}" for d in ds) + f", {b.year}"


def time_slots(day_start: time, day_end: time, step: int) -> list[time]:
    cur = datetime.combine(date(2000, 1, 1), day_start)
    end = datetime.combine(date(2000, 1, 1), day_end)
    out = []
    while cur < end:
        out.append(cur.time())
        cur += timedelta(minutes=step)
    return out


def utc_to_local(dt: datetime, tz: str = "America/Chicago") -> datetime:
    """Convert a naive UTC timestamp (audit/publish times) to naive local time."""
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
