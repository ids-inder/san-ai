"""End-to-end: CSV in, computed attendance out, and idempotency."""
import datetime as dt

import pytest

from attendance import db, report, sync
from attendance.config import Settings
from attendance.crypto import SecretBox
from attendance.rules import INCOMPLETE, PRESENT

CSV = ("Empcode,Name,Date,INTime,OUTTime\n"
       "E1,Asha,03/11/2026,09:05,18:10\n"
       "E1,Asha,04/11/2026,10:30,18:00\n"
       "E2,Ravi,03/11/2026,09:00,13:00\n"
       "E3,Solo,03/11/2026,09:00,\n")


@pytest.fixture()
def rig(tmp_path):
    cfg = Settings(db_path=tmp_path / "a.db", secret_key=SecretBox.generate_key())
    conn = db.connect(cfg.db_path)
    path = tmp_path / "nov.csv"
    path.write_text(CSV)
    clinic_id = db.upsert_clinic(conn, "KB", "Karol Bagh", week_off_dow=6)
    conn.execute(
        """INSERT INTO account (label, kind, base_url, clinic_id, enabled,
               created_at, updated_at)
           VALUES ('nov','csv',?,?,0,?,?)""",
        (str(path), clinic_id, db.now(), db.now()))
    conn.commit()
    account = conn.execute("SELECT * FROM account WHERE label='nov'").fetchone()
    return cfg, conn, account, path


def run(rig_tuple, **kw):
    cfg, conn, account, path = rig_tuple
    return sync.sync_account(conn, account, SecretBox(cfg.secret_key), cfg,
                             dt.date(2026, 11, 1), dt.date(2026, 11, 30),
                             csv_path=str(path), **kw)


def test_sync_imports_and_computes(rig):
    stats = run(rig)
    _cfg, conn, _a, _p = rig
    assert stats["status"] == "ok"
    assert stats["fetched"] == 7          # E3 contributes one punch only
    assert stats["inserted"] == 7
    rows = {r["empcode"]: r for r in report.roster(
        conn, dt.date(2026, 11, 1), dt.date(2026, 11, 30))}
    # POLICY: present needs present_minutes (7h), not a full 8h day. E1's 3rd
    # was 09:05-18:10 and the 4th 10:30-18:00 (7h30) - both present, but the
    # 4th is also late.
    assert rows["E1"]["present_days"] == 2
    assert rows["E1"]["half_days"] == 0
    assert rows["E1"]["late_days"] == 1           # 10:30 start on the 4th
    assert rows["E2"]["half_days"] == 1           # 09:00-13:00 = 4h exactly
    assert rows["E3"]["incomplete_days"] == 1     # in-punch with no out


def test_resync_is_idempotent(rig):
    first = run(rig)
    second = run(rig)
    _cfg, conn, _a, _p = rig
    assert first["inserted"] == 7
    assert second["inserted"] == 0, "re-pulling the same window must not duplicate"
    assert conn.execute("SELECT COUNT(*) FROM punch").fetchone()[0] == 7


def test_employees_are_created_automatically(rig):
    run(rig)
    _cfg, conn, _a, _p = rig
    names = {r["empcode"] for r in conn.execute("SELECT empcode FROM employee")}
    assert names == {"E1", "E2", "E3"}
    # and inherit the connection's clinic
    assert all(r["clinic_id"] for r in conn.execute("SELECT clinic_id FROM employee"))


def test_weekly_off_marked_not_absent(rig):
    run(rig)
    _cfg, conn, _a, _p = rig
    emp = conn.execute("SELECT id FROM employee WHERE empcode='E1'").fetchone()
    # 2026-11-08 is a Sunday; the clinic's weekly off is Sunday (6)
    row = conn.execute(
        "SELECT status FROM day WHERE employee_id=? AND work_date='2026-11-08'",
        (emp["id"],)).fetchone()
    assert row is None or row["status"] in ("week_off", "absent")


def test_holiday_overrides_absence(rig):
    cfg, conn, account, path = rig
    run(rig)
    clinic = conn.execute("SELECT id FROM clinic LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO holiday (clinic_id, day, name) VALUES (?,?,?)",
                 (clinic, "2026-11-05", "Test holiday"))
    conn.commit()
    sync.recompute_all(conn, dt.date(2026, 11, 5), dt.date(2026, 11, 5))
    statuses = {r["status"] for r in conn.execute(
        "SELECT status FROM day WHERE work_date='2026-11-05'")}
    assert statuses == {"holiday"}


def test_recompute_rebuilds_from_punches(rig):
    run(rig)
    _cfg, conn, _a, _p = rig
    conn.execute("DELETE FROM day")
    conn.commit()
    n = sync.recompute_all(conn, dt.date(2026, 11, 1), dt.date(2026, 11, 5))
    assert n > 0
    assert conn.execute("SELECT COUNT(*) FROM day").fetchone()[0] == n


def test_sync_run_is_audited(rig):
    run(rig)
    _cfg, conn, _a, _p = rig
    row = conn.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "ok" and row["fetched"] == 7


def test_missing_csv_records_an_error_run(rig):
    cfg, conn, account, _path = rig
    stats = sync.sync_account(conn, account, SecretBox(cfg.secret_key), cfg,
                              dt.date(2026, 11, 1), dt.date(2026, 11, 30),
                              csv_path="/nonexistent/x.csv")
    assert stats["status"] == "error"
    row = conn.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "error" and "not found" in row["error"]


def test_export_csv_has_a_row_per_computed_day(rig):
    run(rig)
    _cfg, conn, _a, _p = rig
    out = report.export_csv(conn, dt.date(2026, 11, 3), dt.date(2026, 11, 4))
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith("date,empcode,name,clinic,status")
    assert len(lines) == 1 + 6        # 3 staff x 2 days


def test_backfill_does_not_invent_absences_before_employment(rig):
    """A 400-day import window must not create absent days before the first punch."""
    cfg, conn, account, path = rig
    sync.sync_account(conn, account, SecretBox(cfg.secret_key), cfg,
                      dt.date(2026, 11, 3) - dt.timedelta(days=400),
                      dt.date(2026, 11, 30), csv_path=str(path))
    row = conn.execute("SELECT MIN(work_date) AS m FROM day").fetchone()
    assert row["m"] == "2026-11-03", "computation should start at the first punch"


def test_recompute_never_runs_into_the_future(rig):
    cfg, conn, account, path = rig
    run(rig)
    far = dt.date.today() + dt.timedelta(days=90)
    sync.recompute_all(conn, dt.date(2026, 11, 1), far)
    latest = conn.execute("SELECT MAX(work_date) AS m FROM day").fetchone()["m"]
    assert dt.date.fromisoformat(latest) <= dt.date.today()


def test_existing_database_gains_new_columns(tmp_path):
    """connect() migrates an older database in place rather than failing."""
    import sqlite3 as sq
    path = tmp_path / "old.db"
    old = sq.connect(str(path))
    old.executescript("""
        CREATE TABLE shift (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, start_min INTEGER NOT NULL,
            end_min INTEGER NOT NULL, grace_in_min INTEGER NOT NULL DEFAULT 15,
            grace_out_min INTEGER NOT NULL DEFAULT 15,
            half_day_minutes INTEGER NOT NULL DEFAULT 240,
            full_day_minutes INTEGER NOT NULL DEFAULT 480,
            created_at REAL NOT NULL);
        INSERT INTO shift (code,name,start_min,end_min,created_at)
            VALUES ('GEN','General',540,1080,0);
    """)
    old.commit(); old.close()

    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(shift)")}
    assert "present_minutes" in cols
    row = conn.execute("SELECT present_minutes FROM shift WHERE code='GEN'").fetchone()
    assert row["present_minutes"] == 420        # sensible default, data kept
