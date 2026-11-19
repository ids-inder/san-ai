"""SQLite store for clinics, connections, punches and computed days.

Design notes
------------
* Punches are stored as clinic-local wall-clock strings ('YYYY-MM-DD HH:MM:SS').
  Every clinic in scope is in one timezone, so storing naive local time keeps
  reports, shift maths and the vendor's own exports all in the same frame.
* ``punch`` carries UNIQUE(employee_id, punch_at, direction). Re-syncing an
  overlapping date range is therefore free of duplicates - inserts use
  INSERT OR IGNORE, so a nightly job can safely re-pull the last 7 days.
* ``day`` is a derived cache, rebuilt from punches. It is never the source of
  truth and can be dropped and recomputed at any time.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- A physical clinic location.
CREATE TABLE IF NOT EXISTS clinic (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    week_off_dow  INTEGER NOT NULL DEFAULT 6,   -- 0=Mon .. 6=Sun, -1 = none
    created_at    REAL NOT NULL
);

-- A shift definition. end_min may exceed 1440 for shifts crossing midnight.
CREATE TABLE IF NOT EXISTS shift (
    id                INTEGER PRIMARY KEY,
    code              TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    start_min         INTEGER NOT NULL,
    end_min           INTEGER NOT NULL,
    grace_in_min      INTEGER NOT NULL DEFAULT 15,
    grace_out_min     INTEGER NOT NULL DEFAULT 15,
    half_day_minutes  INTEGER NOT NULL DEFAULT 240,
    present_minutes   INTEGER NOT NULL DEFAULT 420,
    full_day_minutes  INTEGER NOT NULL DEFAULT 480,
    created_at        REAL NOT NULL
);

-- A connection to an upstream source of punch data: one vendor login, or a
-- CSV drop. Added and managed from the dashboard. The password is stored as
-- a Fernet token in `secret`, never in plaintext.
CREATE TABLE IF NOT EXISTS account (
    id              INTEGER PRIMARY KEY,
    label           TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL DEFAULT 'etimeoffice',  -- etimeoffice|csv
    base_url        TEXT NOT NULL DEFAULT '',
    corp_id         TEXT NOT NULL DEFAULT '',
    user_id         TEXT NOT NULL DEFAULT '',
    secret          TEXT NOT NULL DEFAULT '',
    admin_flag      TEXT NOT NULL DEFAULT 'true',
    clinic_id       INTEGER REFERENCES clinic(id) ON DELETE SET NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT NOT NULL DEFAULT 'untested',
    last_detail     TEXT NOT NULL DEFAULT '',
    last_checked_at REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

-- Employee codes are only unique within one upstream account.
CREATE TABLE IF NOT EXISTS employee (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER REFERENCES account(id) ON DELETE SET NULL,
    empcode     TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    clinic_id   INTEGER REFERENCES clinic(id) ON DELETE SET NULL,
    shift_id    INTEGER REFERENCES shift(id) ON DELETE SET NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    UNIQUE(account_id, empcode)
);

CREATE TABLE IF NOT EXISTS punch (
    id          INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    punch_at    TEXT NOT NULL,                  -- 'YYYY-MM-DD HH:MM:SS' local
    direction   TEXT NOT NULL DEFAULT '',       -- in|out|'' (unknown)
    device      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    UNIQUE(employee_id, punch_at, direction)
);
CREATE INDEX IF NOT EXISTS idx_punch_emp_at ON punch(employee_id, punch_at);

-- Derived, rebuildable summary of one employee-day.
CREATE TABLE IF NOT EXISTS day (
    employee_id       INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    work_date         TEXT NOT NULL,            -- 'YYYY-MM-DD'
    status            TEXT NOT NULL,
    first_in          TEXT,
    last_out          TEXT,
    worked_minutes    INTEGER NOT NULL DEFAULT 0,
    late_minutes      INTEGER NOT NULL DEFAULT 0,
    early_out_minutes INTEGER NOT NULL DEFAULT 0,
    ot_minutes        INTEGER NOT NULL DEFAULT 0,
    punch_count       INTEGER NOT NULL DEFAULT 0,
    anomalies         TEXT NOT NULL DEFAULT '',
    computed_at       REAL NOT NULL,
    PRIMARY KEY (employee_id, work_date)
);
CREATE INDEX IF NOT EXISTS idx_day_date ON day(work_date);

CREATE TABLE IF NOT EXISTS holiday (
    id        INTEGER PRIMARY KEY,
    clinic_id INTEGER REFERENCES clinic(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,
    name      TEXT NOT NULL DEFAULT '',
    UNIQUE(clinic_id, day)
);

CREATE TABLE IF NOT EXISTS sync_run (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER REFERENCES account(id) ON DELETE SET NULL,
    from_date     TEXT NOT NULL,
    to_date       TEXT NOT NULL,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    fetched       INTEGER NOT NULL DEFAULT 0,
    inserted      INTEGER NOT NULL DEFAULT 0,
    days_computed INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running',  -- running|ok|error
    error         TEXT NOT NULL DEFAULT ''
);

-- Dashboard login sessions.
CREATE TABLE IF NOT EXISTS session (
    token      TEXT PRIMARY KEY,
    csrf       TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""


# Columns added after the first release. Applied on every connect, so an
# existing database upgrades in place instead of needing a dump and reload.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("shift", "present_minutes", "INTEGER NOT NULL DEFAULT 420"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    if fresh:
        # Credentials live here; keep it owner-only.
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return conn


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- reference data

def upsert_clinic(conn: sqlite3.Connection, code: str, name: str,
                  week_off_dow: int = 6) -> int:
    conn.execute(
        """INSERT INTO clinic (code, name, week_off_dow, created_at)
           VALUES (?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name,
                                           week_off_dow=excluded.week_off_dow""",
        (code, name, week_off_dow, now()),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM clinic WHERE code=?", (code,)).fetchone()["id"])


def upsert_shift(conn: sqlite3.Connection, code: str, name: str, start_min: int,
                 end_min: int, grace_in_min: int = 15, grace_out_min: int = 15,
                 half_day_minutes: int = 240, full_day_minutes: int = 480,
                 present_minutes: int = 420) -> int:
    conn.execute(
        """INSERT INTO shift (code, name, start_min, end_min, grace_in_min,
                              grace_out_min, half_day_minutes, present_minutes,
                              full_day_minutes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
               name=excluded.name, start_min=excluded.start_min,
               end_min=excluded.end_min, grace_in_min=excluded.grace_in_min,
               grace_out_min=excluded.grace_out_min,
               half_day_minutes=excluded.half_day_minutes,
               present_minutes=excluded.present_minutes,
               full_day_minutes=excluded.full_day_minutes""",
        (code, name, start_min, end_min, grace_in_min, grace_out_min,
         half_day_minutes, present_minutes, full_day_minutes, now()),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM shift WHERE code=?", (code,)).fetchone()["id"])


def get_or_create_employee(conn: sqlite3.Connection, account_id: int | None,
                           empcode: str, name: str = "",
                           clinic_id: int | None = None,
                           shift_id: int | None = None) -> int:
    """Employees appear automatically the first time they punch.

    An unknown code is never a reason to drop data - the row is created and
    can be assigned to a clinic and shift afterwards from the dashboard.
    """
    row = conn.execute(
        "SELECT id, name FROM employee WHERE account_id IS ? AND empcode=?",
        (account_id, empcode),
    ).fetchone()
    if row:
        if name and not row["name"]:
            conn.execute("UPDATE employee SET name=? WHERE id=?", (name, row["id"]))
            conn.commit()
        return int(row["id"])
    cur = conn.execute(
        """INSERT INTO employee (account_id, empcode, name, clinic_id, shift_id,
                                 active, created_at)
           VALUES (?,?,?,?,?,1,?)""",
        (account_id, empcode, name, clinic_id, shift_id, now()),
    )
    conn.commit()
    return int(cur.lastrowid)


# ---------------------------------------------------------------------- punches

def insert_punches(conn: sqlite3.Connection, rows: Sequence[tuple]) -> int:
    """rows: (employee_id, punch_at, direction, device, source). Returns inserted."""
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO punch
               (employee_id, punch_at, direction, device, source, created_at)
           VALUES (?,?,?,?,?,?)""",
        [(*r, now()) for r in rows],
    )
    conn.commit()
    return conn.total_changes - before


def punches_for(conn: sqlite3.Connection, employee_id: int,
                start: str, end: str) -> list[sqlite3.Row]:
    """Punches with punch_at in [start, end) - both 'YYYY-MM-DD HH:MM:SS'."""
    return list(conn.execute(
        """SELECT punch_at, direction, device FROM punch
           WHERE employee_id=? AND punch_at >= ? AND punch_at < ?
           ORDER BY punch_at""",
        (employee_id, start, end),
    ))


def save_day(conn: sqlite3.Connection, employee_id: int, work_date: str,
             rec: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO day (employee_id, work_date, status, first_in, last_out,
                            worked_minutes, late_minutes, early_out_minutes,
                            ot_minutes, punch_count, anomalies, computed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(employee_id, work_date) DO UPDATE SET
               status=excluded.status, first_in=excluded.first_in,
               last_out=excluded.last_out, worked_minutes=excluded.worked_minutes,
               late_minutes=excluded.late_minutes,
               early_out_minutes=excluded.early_out_minutes,
               ot_minutes=excluded.ot_minutes, punch_count=excluded.punch_count,
               anomalies=excluded.anomalies, computed_at=excluded.computed_at""",
        (employee_id, work_date, rec["status"], rec.get("first_in"),
         rec.get("last_out"), rec.get("worked_minutes", 0),
         rec.get("late_minutes", 0), rec.get("early_out_minutes", 0),
         rec.get("ot_minutes", 0), rec.get("punch_count", 0),
         ",".join(rec.get("anomalies", ())), now()),
    )


def is_holiday(conn: sqlite3.Connection, clinic_id: int | None, day: str) -> str | None:
    row = conn.execute(
        """SELECT name FROM holiday
           WHERE day=? AND (clinic_id IS ? OR clinic_id IS NULL) LIMIT 1""",
        (day, clinic_id),
    ).fetchone()
    return row["name"] if row else None


# ------------------------------------------------------------------- sync audit

def start_sync(conn: sqlite3.Connection, account_id: int | None,
               from_date: str, to_date: str) -> int:
    cur = conn.execute(
        """INSERT INTO sync_run (account_id, from_date, to_date, started_at)
           VALUES (?,?,?,?)""",
        (account_id, from_date, to_date, now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_sync(conn: sqlite3.Connection, run_id: int, status: str,
                fetched: int = 0, inserted: int = 0, days: int = 0,
                error: str = "") -> None:
    conn.execute(
        """UPDATE sync_run SET finished_at=?, status=?, fetched=?, inserted=?,
                               days_computed=?, error=? WHERE id=?""",
        (now(), status, fetched, inserted, days, error[:2000], run_id),
    )
    conn.commit()
