"""Command line for the attendance service.

Stdlib argparse rather than typer, so the service has no CLI dependency.
Everything the dashboard can do is also reachable here, which is what cron
and first-time setup need.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import db, report, sync
from .config import settings
from .crypto import SecretBox, SecretError
from .rules import Shift
from .sources.etimeoffice import DEFAULT_BASE, KNOWN_ENDPOINTS, EtimeOfficeSource


def _conn(cfg):
    cfg.ensure_dirs()
    return db.connect(cfg.db_path)


def _box(cfg) -> SecretBox:
    return SecretBox(cfg.secret_key)


def _days(args) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    to_date = dt.date.fromisoformat(args.to) if args.to else today
    if args.since:
        from_date = dt.date.fromisoformat(args.since)
    else:
        from_date = to_date - dt.timedelta(days=args.lookback)
    return from_date, to_date


def _table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


# ------------------------------------------------------------------- commands

def cmd_keygen(args, cfg) -> int:
    print(SecretBox.generate_key())
    print("\nPut this in the service environment as ATT_SECRET_KEY.",
          file=sys.stderr)
    print("Keep a copy: stored passwords cannot be decrypted without it.",
          file=sys.stderr)
    return 0


def cmd_initdb(args, cfg) -> int:
    conn = _conn(cfg)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"database ready at {cfg.db_path}")
    print("tables:", ", ".join(tables))
    return 0


def cmd_clinic_add(args, cfg) -> int:
    conn = _conn(cfg)
    cid = db.upsert_clinic(conn, args.code, args.name, args.week_off)
    print(f"clinic {args.code} -> id {cid}")
    return 0


def cmd_shift_add(args, cfg) -> int:
    conn = _conn(cfg)
    def mins(raw: str) -> int:
        h, m = raw.split(":")
        return int(h) * 60 + int(m)
    start, end = mins(args.start), mins(args.end)
    if end <= start:
        end += 24 * 60
    sid = db.upsert_shift(conn, args.code, args.name, start, end,
                          args.grace_in, args.grace_out,
                          int(args.half * 60), int(args.full * 60),
                          int(args.present * 60))
    print(f"shift {args.code} -> id {sid}")
    return 0


def cmd_account_add(args, cfg) -> int:
    conn = _conn(cfg)
    import getpass
    password = args.password or (
        getpass.getpass("Password (not echoed): ") if args.kind != "csv" else "")
    try:
        secret = _box(cfg).seal(password)
    except SecretError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    clinic_id = None
    if args.clinic:
        row = conn.execute("SELECT id FROM clinic WHERE code=?",
                           (args.clinic,)).fetchone()
        if not row:
            print(f"error: no clinic with code {args.clinic}", file=sys.stderr)
            return 2
        clinic_id = int(row["id"])
    conn.execute(
        """INSERT INTO account (label, kind, base_url, corp_id, user_id, secret,
               admin_flag, clinic_id, enabled, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,1,?,?)
           ON CONFLICT(label) DO UPDATE SET kind=excluded.kind,
               base_url=excluded.base_url, corp_id=excluded.corp_id,
               user_id=excluded.user_id, secret=excluded.secret,
               admin_flag=excluded.admin_flag, clinic_id=excluded.clinic_id,
               updated_at=excluded.updated_at""",
        (args.label, args.kind, args.base_url or "", args.corp_id or "",
         args.user_id or "", secret, args.admin_flag, clinic_id,
         db.now(), db.now()))
    conn.commit()
    print(f"connection '{args.label}' saved")
    return 0


def cmd_account_list(args, cfg) -> int:
    conn = _conn(cfg)
    rows = report.accounts(conn)
    for r in rows:
        r["secret?"] = "yes" if r["has_secret"] else "no"
    _table(rows, ["id", "label", "kind", "corp_id", "user_id", "secret?",
                  "enabled", "employee_count", "last_status"])
    return 0


def cmd_account_test(args, cfg) -> int:
    conn = _conn(cfg)
    rows = [report.account(conn, args.id)] if args.id else list(
        conn.execute("SELECT * FROM account ORDER BY label"))
    rc = 0
    for acct in rows:
        if acct is None:
            print("error: no such connection", file=sys.stderr)
            return 2
        result = sync.probe_account(conn, acct, _box(cfg), cfg,
                                    csv_path=args.csv)
        mark = "OK " if result.ok else "FAIL"
        print(f"[{mark}] {acct['label']}: {result.status} - {result.detail}")
        if args.verbose and result.sample:
            print("        raw:", result.sample[:400].replace("\n", " "))
        rc = rc or (0 if result.ok else 1)
    return rc


def cmd_sync(args, cfg) -> int:
    conn = _conn(cfg)
    from_date, to_date = _days(args)
    if args.id:
        acct = report.account(conn, args.id)
        if acct is None:
            print("error: no such connection", file=sys.stderr)
            return 2
        results = [sync.sync_account(conn, acct, _box(cfg), cfg, from_date,
                                     to_date, csv_path=args.csv)]
    else:
        results = sync.sync_all(conn, _box(cfg), cfg, from_date, to_date)
    if not results:
        print("no enabled API connections to sync")
        return 0
    rc = 0
    for s in results:
        if s["status"] == "ok":
            print(f"[OK ] {s['account']}: fetched {s['fetched']}, "
                  f"new {s['inserted']}, days {s['days']}")
        else:
            print(f"[ERR] {s['account']}: {s['error']}", file=sys.stderr)
            rc = 1
    return rc


def cmd_import(args, cfg) -> int:
    conn = _conn(cfg)
    label = args.label or f"import:{args.path.rsplit('/', 1)[-1]}"
    clinic_id = None
    if args.clinic:
        row = conn.execute("SELECT id FROM clinic WHERE code=?",
                           (args.clinic,)).fetchone()
        if not row:
            print(f"error: no clinic with code {args.clinic}", file=sys.stderr)
            return 2
        clinic_id = int(row["id"])
    conn.execute(
        """INSERT INTO account (label, kind, base_url, clinic_id, enabled,
               created_at, updated_at)
           VALUES (?,'csv',?,?,0,?,?)
           ON CONFLICT(label) DO UPDATE SET base_url=excluded.base_url,
               clinic_id=excluded.clinic_id, updated_at=excluded.updated_at""",
        (label, args.path, clinic_id, db.now(), db.now()))
    conn.commit()
    acct = conn.execute("SELECT * FROM account WHERE label=?", (label,)).fetchone()
    today = dt.date.today()
    stats = sync.sync_account(conn, acct, _box(cfg), cfg,
                              today - dt.timedelta(days=args.history), today,
                              csv_path=args.path)
    if stats["status"] != "ok":
        print(f"error: {stats['error']}", file=sys.stderr)
        return 1
    print(f"imported: {stats['fetched']} record(s) read, "
          f"{stats['inserted']} new punch(es), {stats['days']} day(s) computed")
    return 0


def cmd_recompute(args, cfg) -> int:
    conn = _conn(cfg)
    from_date, to_date = _days(args)
    n = sync.recompute_all(conn, from_date, to_date)
    print(f"recomputed {n} employee-day(s) from {from_date} to {to_date}")
    return 0


def cmd_probe(args, cfg) -> int:
    """Hit the vendor API directly with ad-hoc credentials and dump the reply."""
    source = EtimeOfficeSource(args.corp_id, args.user_id, args.password,
                               base_url=args.base_url or DEFAULT_BASE,
                               admin_flag=args.admin_flag,
                               endpoint=args.endpoint,
                               timeout=cfg.http_timeout)
    today = dt.date.today()
    from_date = today - dt.timedelta(days=args.lookback)
    result = source.probe(from_date, today)
    print(f"status : {result.status}")
    print(f"detail : {result.detail}")
    if result.sample:
        print("--- first 600 bytes of the response ---")
        print(result.sample)
    return 0 if result.ok else 1


def cmd_serve(args, cfg) -> int:
    try:
        import uvicorn
    except ImportError:
        print("error: uvicorn is not installed", file=sys.stderr)
        return 2
    from .web import create_app
    if not cfg.web_password:
        print("warning: ATT_WEB_PASSWORD is empty - the dashboard will refuse "
              "logins until it is set", file=sys.stderr)
    uvicorn.run(create_app(cfg), host=args.host or cfg.web_host,
                port=args.port or cfg.web_port, log_level="info")
    return 0


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="attendance",
        description="Clinic attendance: pull punches, compute days, serve a dashboard.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="print a new credential-encryption key").set_defaults(fn=cmd_keygen)
    sub.add_parser("initdb", help="create or migrate the database").set_defaults(fn=cmd_initdb)

    s = sub.add_parser("serve", help="run the dashboard")
    s.add_argument("--host"), s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("clinic-add", help="add or update a clinic")
    s.add_argument("code"), s.add_argument("name")
    s.add_argument("--week-off", type=int, default=6,
                   help="0=Mon .. 6=Sun, -1 for none (default 6)")
    s.set_defaults(fn=cmd_clinic_add)

    s = sub.add_parser("shift-add", help="add or update a shift")
    s.add_argument("code"), s.add_argument("name")
    s.add_argument("--start", default="09:00"), s.add_argument("--end", default="18:00")
    s.add_argument("--grace-in", type=int, default=15)
    s.add_argument("--grace-out", type=int, default=15)
    s.add_argument("--half", type=float, default=4.0, help="half-day hours")
    s.add_argument("--present", type=float, default=7.0,
                   help="hours needed to count as present (default 7)")
    s.add_argument("--full", type=float, default=8.0,
                   help="full-day hours, the overtime baseline (default 8)")
    s.set_defaults(fn=cmd_shift_add)

    s = sub.add_parser("account-add", help="add or update a connection")
    s.add_argument("label")
    s.add_argument("--kind", choices=["etimeoffice", "csv"], default="etimeoffice")
    s.add_argument("--corp-id"), s.add_argument("--user-id")
    s.add_argument("--password", help="omit to be prompted without echo")
    s.add_argument("--base-url"), s.add_argument("--admin-flag", default="true")
    s.add_argument("--clinic", help="clinic code these staff belong to")
    s.set_defaults(fn=cmd_account_add)

    sub.add_parser("account-list", help="list connections").set_defaults(fn=cmd_account_list)

    s = sub.add_parser("account-test", help="test one or all connections")
    s.add_argument("--id", type=int), s.add_argument("--csv")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_account_test)

    s = sub.add_parser("sync", help="pull punches and recompute")
    s.add_argument("--id", type=int, help="only this connection")
    s.add_argument("--since"), s.add_argument("--to"), s.add_argument("--csv")
    s.add_argument("--lookback", type=int, default=7)
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("import-csv", help="backfill from a portal export")
    s.add_argument("path")
    s.add_argument("--clinic"), s.add_argument("--label")
    s.add_argument("--history", type=int, default=400,
                   help="how many days back to accept (default 400)")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("recompute", help="rebuild computed days from stored punches")
    s.add_argument("--since"), s.add_argument("--to")
    s.add_argument("--lookback", type=int, default=30)
    s.set_defaults(fn=cmd_recompute)

    s = sub.add_parser("probe", help="call the vendor API with ad-hoc credentials")
    s.add_argument("corp_id"), s.add_argument("user_id"), s.add_argument("password")
    s.add_argument("--base-url"), s.add_argument("--admin-flag", default="true")
    s.add_argument("--endpoint", default=KNOWN_ENDPOINTS[0], choices=KNOWN_ENDPOINTS)
    s.add_argument("--lookback", type=int, default=3)
    s.set_defaults(fn=cmd_probe)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = settings()
    try:
        return int(args.fn(args, cfg) or 0)
    except SecretError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
