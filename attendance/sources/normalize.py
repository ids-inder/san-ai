"""Turn whatever the vendor gives us into PunchRecord rows.

The eTimeOffice API ships no published schema and its CSV exports use the
same field names as its JSON, so one tolerant normalizer serves both. Rather
than hard-coding one spelling, every field is looked up across the spellings
these systems are known to emit, and unparseable rows are skipped rather than
crashing a whole sync.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Iterator

from .base import PunchRecord

FMT = "%Y-%m-%d %H:%M:%S"

EMPCODE_KEYS = ("empcode", "emp_code", "employeecode", "employee_code",
                "paycode", "pay_code", "ecode", "cardno", "card_no")
NAME_KEYS = ("name", "empname", "emp_name", "employeename", "employee_name")
DATE_KEYS = ("date", "attdate", "att_date", "punchdate", "punch_date",
             "inoutdate", "dateofpunch", "attendancedate", "dt")
DATETIME_KEYS = ("datetime", "punchdatetime", "punch_datetime", "inouttime",
                 "logdatetime", "transactiontime", "punchtime", "time")
IN_KEYS = ("intime", "in_time", "in", "timein", "time_in", "firstin", "first_in")
OUT_KEYS = ("outtime", "out_time", "out", "timeout", "time_out", "lastout", "last_out")
DIRECTION_KEYS = ("inout", "in_out", "direction", "punchtype", "type",
                  "ioflag", "io_flag", "status", "mode")
DEVICE_KEYS = ("mcid", "deviceid", "device_id", "machineno", "machine_no",
               "serialnumber", "serial_no", "device", "readerid")

DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d",
    "%d/%b/%Y", "%d-%b-%Y", "%d/%B/%Y", "%d-%B-%Y",
    "%m/%d/%Y",
)
TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%H%M")
IN_WORDS = {"in", "i", "0", "entry", "checkin", "check-in", "c/in"}
OUT_WORDS = {"out", "o", "1", "exit", "checkout", "check-out", "c/out"}


def _key(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum() or ch == "_")


def _flatten(row: dict) -> dict:
    return {_key(k): ("" if v is None else str(v).strip()) for k, v in row.items()}


def _pick(row: dict, keys: Iterable[str]) -> str:
    for k in keys:
        if row.get(k):
            return row[k]
    # tolerate keys with the underscores stripped, e.g. 'empcode' vs 'emp_code'
    squashed = {k.replace("_", ""): v for k, v in row.items()}
    for k in keys:
        bare = k.replace("_", "")
        if squashed.get(bare):
            return squashed[bare]
    return ""


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    value = value.split("T")[0].split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(value: str) -> dt.time | None:
    value = (value or "").strip().upper().replace(".", ":")
    if not value or value in {"--:--", "00:00", "0", "-", "NA", "N/A", "ABSENT"}:
        return None
    for fmt in TIME_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def parse_moment(value: str) -> dt.datetime | None:
    """Parse a combined date+time string in any of the known layouts."""
    value = (value or "").strip().replace("T", " ").replace("_", " ")
    if not value:
        return None
    parts = value.split()
    if len(parts) >= 2:
        d, t = parse_date(parts[0]), parse_time(" ".join(parts[1:]))
        if d and t:
            return dt.datetime.combine(d, t)
    d = parse_date(value)
    return dt.datetime.combine(d, dt.time()) if d else None


def _direction(raw: str) -> str:
    token = (raw or "").strip().lower()
    if token in IN_WORDS:
        return "in"
    if token in OUT_WORDS:
        return "out"
    return ""


def normalize_rows(payload: Any, *, device_default: str = "") -> Iterator[PunchRecord]:
    """Yield PunchRecords from a JSON payload or an iterable of CSV dict rows.

    Accepts three upstream shapes:
      1. one row per punch, with a combined timestamp
      2. one row per punch, with separate date and time columns
      3. one row per employee-day carrying InTime and OutTime (what
         DownloadInOutPunchData is named for) - emitted as two punches
    """
    rows = _unwrap(payload)
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = _flatten(raw)
        empcode = _pick(row, EMPCODE_KEYS)
        if not empcode:
            continue
        name = _pick(row, NAME_KEYS)
        device = _pick(row, DEVICE_KEYS) or device_default
        day = parse_date(_pick(row, DATE_KEYS)) or _fuzzy_date(row)

        in_raw, out_raw = _pick(row, IN_KEYS), _pick(row, OUT_KEYS)
        in_t, out_t = parse_time(in_raw), parse_time(out_raw)
        if day and (in_t or out_t):
            if in_t:
                yield PunchRecord(empcode, name,
                                  dt.datetime.combine(day, in_t).strftime(FMT),
                                  "in", device)
            if out_t:
                when = dt.datetime.combine(day, out_t)
                if in_t and out_t < in_t:      # shift ran past midnight
                    when += dt.timedelta(days=1)
                yield PunchRecord(empcode, name, when.strftime(FMT), "out", device)
            continue

        moment = parse_moment(_pick(row, DATETIME_KEYS)) or _fuzzy_moment(row)
        if moment is None and day:
            t = parse_time(_pick(row, ("time", "punchtime")))
            if t:
                moment = dt.datetime.combine(day, t)
        if moment is None:
            continue
        yield PunchRecord(empcode, name, moment.strftime(FMT),
                          _direction(_pick(row, DIRECTION_KEYS)), device)


def _unwrap(payload: Any) -> Iterable[Any]:
    """Find the row list inside whatever wrapper the vendor used."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return value
        return [payload]
    if isinstance(payload, list):
        return payload
    return list(payload)


# -- last-resort column discovery -------------------------------------------
# The vendor publishes no schema, so an unrecognised column name must not cost
# us a row. These scan for anything date-shaped before the row is dropped.
_SKIP_HINTS = ("update", "create", "modif", "sync", "process")


def _fuzzy_date(row: dict) -> dt.date | None:
    for key, value in row.items():
        if "date" in key and not any(h in key for h in _SKIP_HINTS) and value:
            parsed = parse_date(value)
            if parsed:
                return parsed
    return None


def _fuzzy_moment(row: dict) -> dt.datetime | None:
    for key, value in row.items():
        if any(h in key for h in _SKIP_HINTS) or not value:
            continue
        if "time" in key or "stamp" in key or "punch" in key:
            parsed = parse_moment(value)
            if parsed and parsed.time() != dt.time():
                return parsed
    return None
