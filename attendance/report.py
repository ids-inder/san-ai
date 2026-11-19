"""Read-side queries for the dashboard and CSV exports.

All aggregation happens in SQL over the derived `day` table, so a month view
for every clinic is one query rather than a Python loop over employees.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import sqlite3
from typing import Any

from .rules import EXCEPTION_STATUSES, ABSENT, HALF_DAY, INCOMPLETE, PRESENT, SHORT

_AGG = """
    SUM(CASE WHEN d.status='present'    THEN 1 ELSE 0 END) AS present_days,
    SUM(CASE WHEN d.status='half_day'   THEN 1 ELSE 0 END) AS half_days,
    SUM(CASE WHEN d.status='absent'     THEN 1 ELSE 0 END) AS absent_days,
    SUM(CASE WHEN d.status='incomplete' THEN 1 ELSE 0 END) AS incomplete_days,
    SUM(CASE WHEN d.status='short'      THEN 1 ELSE 0 END) AS short_days,
    SUM(CASE WHEN d.status='week_off'   THEN 1 ELSE 0 END) AS week_off_days,
    SUM(CASE WHEN d.status='holiday'    THEN 1 ELSE 0 END) AS holiday_days,
    SUM(CASE WHEN d.late_minutes > 0    THEN 1 ELSE 0 END) AS late_days,
    SUM(d.worked_minutes) AS worked_minutes,
    SUM(d.late_minutes)   AS late_minutes,
    SUM(d.ot_minutes)     AS ot_minutes
"""


def _rng(d1: dt.date, d2: dt.date) -> tuple[str, str]:
    return d1.isoformat(), d2.isoformat()


def totals(conn: sqlite3.Connection, d1: dt.date, d2: dt.date,
           clinic_id: int | None = None) -> dict[str, Any]:
    where = "d.work_date BETWEEN ? AND ?"
    args: list[Any] = list(_rng(d1, d2))
    if clinic_id:
        where += " AND e.clinic_id=?"
        args.append(clinic_id)
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT d.employee_id) AS headcount, {_AGG}
            FROM day d JOIN employee e ON e.id=d.employee_id
            WHERE {where}""", args).fetchone()
    out = dict(row) if row else {}
    for key, value in list(out.items()):
        out[key] = value or 0
    return out


def by_clinic(conn: sqlite3.Connection, d1: dt.date, d2: dt.date) -> list[dict]:
    rows = conn.execute(
        f"""SELECT COALESCE(c.name,'(unassigned)') AS clinic,
                   c.id AS clinic_id,
                   COUNT(DISTINCT d.employee_id) AS headcount, {_AGG}
            FROM day d
            JOIN employee e ON e.id=d.employee_id
            LEFT JOIN clinic c ON c.id=e.clinic_id
            WHERE d.work_date BETWEEN ? AND ?
            GROUP BY c.id ORDER BY clinic""", _rng(d1, d2))
    return [dict(r) for r in rows]


def roster(conn: sqlite3.Connection, d1: dt.date, d2: dt.date,
           clinic_id: int | None = None, search: str = "") -> list[dict]:
    where = "d.work_date BETWEEN ? AND ?"
    args: list[Any] = list(_rng(d1, d2))
    if clinic_id:
        where += " AND e.clinic_id=?"
        args.append(clinic_id)
    if search:
        where += " AND (e.name LIKE ? OR e.empcode LIKE ?)"
        args += [f"%{search}%", f"%{search}%"]
    rows = conn.execute(
        f"""SELECT e.id AS employee_id, e.empcode, e.name,
                   COALESCE(c.name,'(unassigned)') AS clinic,
                   COALESCE(s.name,'General') AS shift, {_AGG}
            FROM day d
            JOIN employee e ON e.id=d.employee_id
            LEFT JOIN clinic c ON c.id=e.clinic_id
            LEFT JOIN shift s ON s.id=e.shift_id
            WHERE {where}
            GROUP BY e.id ORDER BY clinic, e.name, e.empcode""", args)
    return [dict(r) for r in rows]


def employee(conn: sqlite3.Connection, employee_id: int) -> dict | None:
    row = conn.execute(
        """SELECT e.*, COALESCE(c.name,'(unassigned)') AS clinic,
                  COALESCE(s.name,'General') AS shift,
                  COALESCE(a.label,'-') AS account
           FROM employee e
           LEFT JOIN clinic c ON c.id=e.clinic_id
           LEFT JOIN shift s ON s.id=e.shift_id
           LEFT JOIN account a ON a.id=e.account_id
           WHERE e.id=?""", (employee_id,)).fetchone()
    return dict(row) if row else None


def employee_days(conn: sqlite3.Connection, employee_id: int,
                  d1: dt.date, d2: dt.date) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM day WHERE employee_id=? AND work_date BETWEEN ? AND ?
           ORDER BY work_date""", (employee_id, *_rng(d1, d2)))
    return [dict(r) for r in rows]


def day_punches(conn: sqlite3.Connection, employee_id: int,
                work_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT punch_at, direction, device, source FROM punch
           WHERE employee_id=? AND punch_at LIKE ? ORDER BY punch_at""",
        (employee_id, f"{work_date}%"))
    return [dict(r) for r in rows]


def exceptions(conn: sqlite3.Connection, d1: dt.date, d2: dt.date,
               clinic_id: int | None = None) -> list[dict]:
    """Days an operator should look at: unpaired punches, shortfalls, absences."""
    marks = ",".join("?" * len(EXCEPTION_STATUSES))
    where = f"d.work_date BETWEEN ? AND ? AND (d.status IN ({marks}) OR d.anomalies != '')"
    args: list[Any] = [*_rng(d1, d2), *EXCEPTION_STATUSES]
    if clinic_id:
        where += " AND e.clinic_id=?"
        args.append(clinic_id)
    rows = conn.execute(
        f"""SELECT d.*, e.empcode, e.name, e.id AS employee_id,
                   COALESCE(c.name,'(unassigned)') AS clinic
            FROM day d
            JOIN employee e ON e.id=d.employee_id
            LEFT JOIN clinic c ON c.id=e.clinic_id
            WHERE {where}
            ORDER BY d.work_date DESC, clinic, e.name""", args)
    return [dict(r) for r in rows]


def clinics(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM clinic ORDER BY name")]


def shifts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM shift ORDER BY name")]


def accounts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*, COALESCE(c.name,'(all clinics)') AS clinic_name,
                  (SELECT COUNT(*) FROM employee e WHERE e.account_id=a.id)
                      AS employee_count
           FROM account a LEFT JOIN clinic c ON c.id=a.clinic_id
           ORDER BY a.label""")
    out = []
    for r in rows:
        d = dict(r)
        d["has_secret"] = bool(d.pop("secret", ""))   # never leaks the token
        out.append(d)
    return out


def account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM account WHERE id=?", (account_id,)).fetchone()


def recent_syncs(conn: sqlite3.Connection, limit: int = 15) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, COALESCE(a.label,'(deleted)') AS account
           FROM sync_run s LEFT JOIN account a ON a.id=s.account_id
           ORDER BY s.started_at DESC LIMIT ?""", (limit,))
    return [dict(r) for r in rows]


def export_csv(conn: sqlite3.Connection, d1: dt.date, d2: dt.date,
               clinic_id: int | None = None) -> str:
    where = "d.work_date BETWEEN ? AND ?"
    args: list[Any] = list(_rng(d1, d2))
    if clinic_id:
        where += " AND e.clinic_id=?"
        args.append(clinic_id)
    rows = conn.execute(
        f"""SELECT d.work_date, e.empcode, e.name,
                   COALESCE(c.name,'') AS clinic, d.status, d.first_in,
                   d.last_out, d.worked_minutes, d.late_minutes,
                   d.early_out_minutes, d.ot_minutes, d.punch_count, d.anomalies
            FROM day d
            JOIN employee e ON e.id=d.employee_id
            LEFT JOIN clinic c ON c.id=e.clinic_id
            WHERE {where}
            ORDER BY d.work_date, clinic, e.name""", args)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "empcode", "name", "clinic", "status", "first_in",
                     "last_out", "worked_minutes", "late_minutes",
                     "early_out_minutes", "ot_minutes", "punches", "anomalies"])
    for r in rows:
        writer.writerow(list(r))
    return buf.getvalue()
