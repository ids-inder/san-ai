"""Pull punches from every enabled connection, then recompute affected days.

Safe to run as often as you like: punch inserts are INSERT OR IGNORE against
a uniqueness constraint, and `day` rows are derived, so re-pulling an
overlapping window converges instead of double-counting. A nightly job that
re-pulls the last week is therefore the right default - late edits made in the
vendor's portal get picked up without any reconciliation logic.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import db
from .config import Settings
from .crypto import SecretBox, SecretError
from .rules import Shift, compute_day, window_for, work_date_of
from .sources.base import ProbeResult, PunchRecord, SourceError
from .sources.csvfile import CsvSource
from .sources.etimeoffice import EtimeOfficeSource


def build_source(account: sqlite3.Row, box: SecretBox, cfg: Settings,
                 csv_path: str | Path | None = None):
    """Turn an account row into a live source adapter."""
    kind = (account["kind"] or "etimeoffice").lower()
    if kind == "csv":
        path = csv_path or account["base_url"]
        if not path:
            raise SourceError(
                f"connection '{account['label']}' is a CSV connection but no "
                "file path was given")
        return CsvSource(path, device_default=account["label"])
    password = box.open(account["secret"] or "")
    return EtimeOfficeSource(
        corp_id=account["corp_id"],
        user_id=account["user_id"],
        password=password,
        base_url=account["base_url"] or cfg.api_base,
        admin_flag=account["admin_flag"] or "true",
        timeout=cfg.http_timeout,
    )


def probe_account(conn: sqlite3.Connection, account: sqlite3.Row, box: SecretBox,
                  cfg: Settings, *, days: int = 3,
                  csv_path: str | Path | None = None) -> ProbeResult:
    """Test one connection and remember the verdict on the account row."""
    today = dt.date.today()
    try:
        source = build_source(account, box, cfg, csv_path)
        result = source.probe(today - dt.timedelta(days=days), today)
    except (SourceError, SecretError) as exc:
        result = ProbeResult(False, "network_error", str(exc))
    conn.execute(
        """UPDATE account SET last_status=?, last_detail=?, last_checked_at=?,
                              updated_at=? WHERE id=?""",
        (result.status, result.detail[:1000], db.now(), db.now(), account["id"]),
    )
    conn.commit()
    return result


# --------------------------------------------------------------- recomputation

def _shift_for(conn: sqlite3.Connection, employee: sqlite3.Row) -> Shift:
    if employee["shift_id"]:
        row = conn.execute("SELECT * FROM shift WHERE id=?",
                           (employee["shift_id"],)).fetchone()
        if row:
            return Shift.from_row(row)
    return Shift()


def recompute_employee(conn: sqlite3.Connection, employee: sqlite3.Row,
                       from_date: dt.date, to_date: dt.date) -> int:
    """Rebuild computed days for one employee across a date range.

    The range is clamped to the employee's first ever punch and to today.
    Without that, a wide backfill window would invent `absent` days for
    dates before the person was employed - which then shows up in every
    report as absence they are not responsible for.
    """
    first = conn.execute(
        "SELECT MIN(punch_at) AS m FROM punch WHERE employee_id=?",
        (employee["id"],)).fetchone()
    if not first or not first["m"]:
        return 0                       # never seen punching; nothing to compute
    earliest = dt.date.fromisoformat(first["m"][:10])
    from_date = max(from_date, earliest)
    to_date = min(to_date, dt.date.today())
    if from_date > to_date:
        return 0

    shift = _shift_for(conn, employee)
    clinic_id = employee["clinic_id"]
    week_off_dow = -1
    if clinic_id:
        row = conn.execute("SELECT week_off_dow FROM clinic WHERE id=?",
                           (clinic_id,)).fetchone()
        if row:
            week_off_dow = int(row["week_off_dow"])

    done = 0
    day = from_date
    while day <= to_date:
        start, end = window_for(shift, day)
        punches = [(r["punch_at"], r["direction"])
                   for r in db.punches_for(conn, employee["id"], start, end)]
        rec = compute_day(
            punches, shift,
            week_off=(week_off_dow >= 0 and day.weekday() == week_off_dow),
            holiday=db.is_holiday(conn, clinic_id, day.isoformat()),
        )
        db.save_day(conn, employee["id"], day.isoformat(), rec.as_dict())
        done += 1
        day += dt.timedelta(days=1)
    conn.commit()
    return done


def recompute_all(conn: sqlite3.Connection, from_date: dt.date,
                  to_date: dt.date, employee_ids: Iterable[int] | None = None) -> int:
    if employee_ids is None:
        rows = list(conn.execute("SELECT * FROM employee WHERE active=1"))
    else:
        ids = list(dict.fromkeys(employee_ids))
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        rows = list(conn.execute(
            f"SELECT * FROM employee WHERE id IN ({marks})", ids))
    return sum(recompute_employee(conn, emp, from_date, to_date) for emp in rows)


# ----------------------------------------------------------------------- sync

def sync_account(conn: sqlite3.Connection, account: sqlite3.Row, box: SecretBox,
                 cfg: Settings, from_date: dt.date, to_date: dt.date,
                 csv_path: str | Path | None = None) -> dict[str, Any]:
    run_id = db.start_sync(conn, account["id"], from_date.isoformat(),
                           to_date.isoformat())
    stats = {"account": account["label"], "fetched": 0, "inserted": 0,
             "days": 0, "status": "ok", "error": ""}
    try:
        source = build_source(account, box, cfg, csv_path)
        records: list[PunchRecord] = list(source.fetch(from_date, to_date))
        stats["fetched"] = len(records)

        touched: set[int] = set()
        rows: list[tuple] = []
        for rec in records:
            emp_id = db.get_or_create_employee(
                conn, account["id"], rec.empcode, rec.name,
                clinic_id=account["clinic_id"])
            touched.add(emp_id)
            rows.append((emp_id, rec.punch_at, rec.direction, rec.device,
                         account["label"]))
        stats["inserted"] = db.insert_punches(conn, rows)

        # An overnight punch can belong to the previous work date, so widen
        # the recompute window by a day on each side.
        stats["days"] = recompute_all(
            conn, from_date - dt.timedelta(days=1),
            to_date + dt.timedelta(days=1), touched)
        db.finish_sync(conn, run_id, "ok", stats["fetched"], stats["inserted"],
                       stats["days"])
        conn.execute(
            """UPDATE account SET last_status='ok', last_detail=?,
                                  last_checked_at=?, updated_at=? WHERE id=?""",
            (f"Synced {stats['inserted']} new punch(es) from "
             f"{stats['fetched']} record(s).", db.now(), db.now(), account["id"]),
        )
        conn.commit()
    except (SourceError, SecretError) as exc:
        stats["status"], stats["error"] = "error", str(exc)
        db.finish_sync(conn, run_id, "error", error=str(exc))
        conn.execute(
            """UPDATE account SET last_status='error', last_detail=?,
                                  last_checked_at=?, updated_at=? WHERE id=?""",
            (str(exc)[:1000], db.now(), db.now(), account["id"]),
        )
        conn.commit()
    return stats


def sync_all(conn: sqlite3.Connection, box: SecretBox, cfg: Settings,
             from_date: dt.date | None = None,
             to_date: dt.date | None = None) -> list[dict[str, Any]]:
    to_date = to_date or dt.date.today()
    from_date = from_date or to_date - dt.timedelta(days=cfg.lookback_days)
    accounts = list(conn.execute(
        "SELECT * FROM account WHERE enabled=1 AND kind!='csv' ORDER BY label"))
    return [sync_account(conn, acc, box, cfg, from_date, to_date)
            for acc in accounts]
