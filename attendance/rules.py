"""Attendance computation: raw punches + a shift definition -> one day's verdict.

Why this lives in our code and not in the vendor's settings screen: the punch
endpoints are the only ones their API exposes, and clinic shift policy changes
more often than anyone wants to re-licence software for. Everything here is
pure - no database, no clock - so it is cheap to test and safe to re-run.

Conventions
-----------
late_minutes      minutes past shift start, counted only once the grace period
                  is exceeded (within grace it is 0, not negative)
early_out_minutes minutes before shift end, same grace treatment
worked_minutes    sum of paired in->out intervals, NOT the first-to-last span,
                  so a long midday break is not paid as work
present_minutes   the bar for a full day's attendance; full_day_minutes is a
                  separate, higher number used only as the overtime baseline
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ------------------------------------------------------------------- statuses
PRESENT = "present"
HALF_DAY = "half_day"
SHORT = "short"            # punched, but under the half-day threshold
INCOMPLETE = "incomplete"  # punches exist but cannot be paired (missing out)
ABSENT = "absent"
WEEK_OFF = "week_off"
HOLIDAY = "holiday"

# Statuses an operator should look at rather than file away.
EXCEPTION_STATUSES = (INCOMPLETE, SHORT, ABSENT)

FMT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Shift:
    code: str = "GEN"
    name: str = "General"
    start_min: int = 9 * 60            # 09:00
    end_min: int = 18 * 60             # 18:00; > 1440 means it crosses midnight
    grace_in_min: int = 15
    grace_out_min: int = 15
    half_day_minutes: int = 240
    # The bar for a full 'present' day. Deliberately lower than
    # full_day_minutes: that one is the overtime baseline, and using a single
    # number for both would label a 7h25 day a half day.
    present_minutes: int = 420
    full_day_minutes: int = 480

    @property
    def crosses_midnight(self) -> bool:
        return self.end_min > 24 * 60

    @property
    def boundary_min(self) -> int:
        """Minute-of-day at which one work-date's punch window begins.

        A day shift's window starts at midnight. A night shift's starts a few
        hours before the shift does, so a 22:00 in-punch and the 06:00
        out-punch that follows land on the same work date.
        """
        if not self.crosses_midnight:
            return 0
        return max(0, self.start_min - 180)

    @classmethod
    def from_row(cls, row) -> "Shift":
        """Build from a sqlite3.Row / mapping, falling back to defaults."""
        if row is None:
            return cls()
        get = row.__getitem__ if hasattr(row, "keys") else row.get
        def val(key, default):
            try:
                v = get(key)
            except (KeyError, IndexError):
                return default
            return default if v is None else v
        return cls(
            code=val("code", "GEN"),
            name=val("name", "General"),
            start_min=int(val("start_min", 540)),
            end_min=int(val("end_min", 1080)),
            grace_in_min=int(val("grace_in_min", 15)),
            grace_out_min=int(val("grace_out_min", 15)),
            half_day_minutes=int(val("half_day_minutes", 240)),
            present_minutes=int(val("present_minutes", 420)),
            full_day_minutes=int(val("full_day_minutes", 480)),
        )


@dataclass
class DayRecord:
    status: str = ABSENT
    first_in: str | None = None
    last_out: str | None = None
    worked_minutes: int = 0
    late_minutes: int = 0
    early_out_minutes: int = 0
    ot_minutes: int = 0
    punch_count: int = 0
    anomalies: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "first_in": self.first_in,
            "last_out": self.last_out,
            "worked_minutes": self.worked_minutes,
            "late_minutes": self.late_minutes,
            "early_out_minutes": self.early_out_minutes,
            "ot_minutes": self.ot_minutes,
            "punch_count": self.punch_count,
            "anomalies": list(self.anomalies),
        }


# ------------------------------------------------------------------- utilities

def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, FMT)


def window_for(shift: Shift, work_date: dt.date) -> tuple[str, str]:
    """[start, end) punch window covering one work date, as stored strings."""
    start = dt.datetime.combine(work_date, dt.time()) + dt.timedelta(minutes=shift.boundary_min)
    return start.strftime(FMT), (start + dt.timedelta(days=1)).strftime(FMT)


def work_date_of(shift: Shift, moment: dt.datetime) -> dt.date:
    """Which work date a punch belongs to (night shifts bend backwards)."""
    minute = moment.hour * 60 + moment.minute
    if minute < shift.boundary_min:
        return (moment - dt.timedelta(days=1)).date()
    return moment.date()


def _pair(punches: Sequence[tuple[dt.datetime, str]]) -> tuple[list[tuple[dt.datetime, dt.datetime]], list[str]]:
    """Pair punches into in->out intervals.

    Uses recorded directions when the device supplies them; falls back to
    strict chronological alternation when it does not (many devices export a
    bare timestamp list).
    """
    anomalies: list[str] = []
    pairs: list[tuple[dt.datetime, dt.datetime]] = []
    directed = [p for p in punches if p[1] in ("in", "out")]

    if len(directed) == len(punches) and punches:
        open_in: dt.datetime | None = None
        for moment, direction in punches:
            if direction == "in":
                if open_in is not None:
                    anomalies.append("double_in")
                open_in = moment
            else:  # out
                if open_in is None:
                    anomalies.append("out_without_in")
                    continue
                pairs.append((open_in, moment))
                open_in = None
        if open_in is not None:
            anomalies.append("missing_out")
    else:
        if directed:
            anomalies.append("mixed_directions")
        seq = [p[0] for p in punches]
        for i in range(0, len(seq) - 1, 2):
            pairs.append((seq[i], seq[i + 1]))
        if len(seq) % 2 == 1:
            anomalies.append("missing_out")
    return pairs, anomalies


def compute_day(punches: Iterable[tuple[str, str]], shift: Shift | None = None,
                *, week_off: bool = False, holiday: str | None = None) -> DayRecord:
    """Compute one employee-day.

    punches: iterable of (punch_at, direction) already restricted to this
             work date's window and sorted ascending.
    """
    shift = shift or Shift()
    items = [(parse_ts(ts) if isinstance(ts, str) else ts, (d or "").lower())
             for ts, d in punches]
    items.sort(key=lambda p: p[0])
    rec = DayRecord(punch_count=len(items))

    if not items:
        rec.status = HOLIDAY if holiday else (WEEK_OFF if week_off else ABSENT)
        return rec

    pairs, anomalies = _pair(items)
    rec.anomalies = anomalies
    rec.first_in = items[0][0].strftime(FMT)
    rec.last_out = items[-1][0].strftime(FMT) if len(items) > 1 else None
    rec.worked_minutes = int(sum((b - a).total_seconds() // 60 for a, b in pairs))

    # Lateness is measured against the shift start, forgiven inside the grace.
    first_min = items[0][0].hour * 60 + items[0][0].minute
    if shift.crosses_midnight and first_min < shift.boundary_min:
        first_min += 24 * 60
    if first_min > shift.start_min + shift.grace_in_min:
        rec.late_minutes = first_min - shift.start_min

    if pairs:
        last = pairs[-1][1]
        last_min = last.hour * 60 + last.minute
        if last.date() > items[0][0].date():
            last_min += 24 * 60
        if last_min < shift.end_min - shift.grace_out_min:
            rec.early_out_minutes = shift.end_min - last_min

    if rec.worked_minutes > shift.full_day_minutes:
        rec.ot_minutes = rec.worked_minutes - shift.full_day_minutes

    if not pairs:
        rec.status = INCOMPLETE
    elif rec.worked_minutes >= shift.present_minutes:
        rec.status = PRESENT
    elif rec.worked_minutes >= shift.half_day_minutes:
        rec.status = HALF_DAY
    else:
        rec.status = SHORT

    if "missing_out" in anomalies and rec.status in (PRESENT, HALF_DAY, SHORT):
        rec.status = INCOMPLETE
    return rec


def hhmm(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    minutes = abs(int(minutes))
    return f"{sign}{minutes // 60}h {minutes % 60:02d}m"
