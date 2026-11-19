"""The dashboard application.

A small hand-written ASGI app: routing, cookie sessions, CSRF on every
state-changing POST, and nothing else. Long operations (testing a connection,
running a sync) are pushed to a worker thread so the event loop keeps serving.

Credentials are write-only from the browser's point of view: a password can be
set or replaced, never read back, and the encrypted token never reaches a
template.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import secrets
import sqlite3
import urllib.parse
from typing import Any, Callable

from jinja2 import DictLoader, Environment, select_autoescape

from .. import db, report, sync
from ..config import Settings, settings as load_settings
from ..crypto import SecretBox, SecretError
from ..rules import Shift
from ..sources.etimeoffice import DEFAULT_BASE
from .templates import TEMPLATES

COOKIE = "att_session"
DOWS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --------------------------------------------------------------- jinja filters

def _ts(value: Any) -> str:
    if not value:
        return "-"
    try:
        return dt.datetime.fromtimestamp(float(value)).strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        return "-"


def _hhmm(minutes: Any) -> str:
    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        return "-"
    return f"{minutes // 60}:{minutes % 60:02d}"


def _clock(minutes: Any) -> str:
    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        return "-"
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _env() -> Environment:
    env = Environment(loader=DictLoader(TEMPLATES),
                      autoescape=select_autoescape(default=True,
                                                   default_for_string=True))
    env.filters["ts"] = _ts
    env.filters["hhmm"] = _hhmm
    env.filters["clock"] = _clock
    return env


# ------------------------------------------------------------------- plumbing

class Request:
    def __init__(self, scope: dict, body: bytes) -> None:
        self.scope = scope
        self.method: str = scope["method"]
        self.path: str = scope["path"]
        self.query = urllib.parse.parse_qs(
            scope.get("query_string", b"").decode("latin-1"))
        self.form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        self.headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                        for k, v in scope.get("headers", [])}
        self.cookies = {}
        for part in self.headers.get("cookie", "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                self.cookies[k.strip()] = v.strip()
        self.session: sqlite3.Row | None = None

    def q(self, name: str, default: str = "") -> str:
        return (self.query.get(name) or [default])[0]

    def f(self, name: str, default: str = "") -> str:
        return (self.form.get(name) or [default])[0].strip()

    def f_int(self, name: str) -> int | None:
        raw = self.f(name)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None


def _html(body: str, status: int = 200, extra: list | None = None):
    headers = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"same-origin"),
        (b"content-security-policy", b"default-src 'self'; style-src 'unsafe-inline'"),
        (b"x-frame-options", b"DENY"),
    ]
    return status, headers + (extra or []), body.encode("utf-8")


def _redirect(to: str, extra: list | None = None):
    return 303, [(b"location", to.encode("utf-8")),
                 (b"cache-control", b"no-store")] + (extra or []), b""


def _flash(to: str, message: str, kind: str = "ok") -> str:
    joiner = "&" if "?" in to else "?"
    return f"{to}{joiner}msg={urllib.parse.quote(message[:400])}&kind={kind}"


def _date(raw: str, fallback: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return fallback


def create_app(cfg: Settings | None = None):
    cfg = cfg or load_settings()
    cfg.ensure_dirs()
    env = _env()
    state: dict[str, Any] = {"probe": None}

    def conn() -> sqlite3.Connection:
        return db.connect(cfg.db_path)

    def box() -> SecretBox:
        return SecretBox(cfg.secret_key)

    # ------------------------------------------------------------- sessions
    def new_session(c: sqlite3.Connection) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = db.now()
        c.execute("DELETE FROM session WHERE expires_at < ?", (now,))
        c.execute("""INSERT INTO session (token, csrf, created_at, expires_at)
                     VALUES (?,?,?,?)""",
                  (token, csrf, now, now + cfg.session_hours * 3600))
        c.commit()
        return token, csrf

    def load_session(c: sqlite3.Connection, req: Request) -> sqlite3.Row | None:
        token = req.cookies.get(COOKIE, "")
        if not token:
            return None
        return c.execute(
            "SELECT * FROM session WHERE token=? AND expires_at > ?",
            (token, db.now())).fetchone()

    def render(name: str, req: Request, page: str, **ctx) -> str:
        qs = ""
        keep = {k: req.q(k) for k in ("from", "to", "clinic") if req.q(k)}
        if keep:
            qs = "?" + urllib.parse.urlencode(keep)
        base = {
            "title": ctx.pop("title", page.title()),
            "page": page,
            "qs": qs,
            "csrf": req.session["csrf"] if req.session else "",
            "flash": req.q("msg"),
            "flash_kind": "err" if req.q("kind") == "err" else "ok",
        }
        base.update(ctx)
        return env.get_template(name).render(**base)

    def range_of(req: Request) -> tuple[dt.date, dt.date]:
        today = dt.date.today()
        d2 = _date(req.q("to"), today)
        d1 = _date(req.q("from"), d2 - dt.timedelta(days=29))
        if d1 > d2:
            d1, d2 = d2, d1
        return d1, d2

    # -------------------------------------------------------------- handlers
    def h_login_get(req: Request, c: sqlite3.Connection):
        if not cfg.web_password:
            return _html("<p>ATT_WEB_PASSWORD is not configured on the server.</p>",
                         503)
        return _html(env.get_template("login").render(error=req.q("msg")))

    def h_login_post(req: Request, c: sqlite3.Connection):
        if not cfg.web_password:
            return _html("<p>ATT_WEB_PASSWORD is not configured.</p>", 503)
        supplied = req.f("password")
        if not secrets.compare_digest(supplied, cfg.web_password):
            return _redirect(_flash("/login", "Wrong password.", "err"))
        token, _csrf = new_session(c)
        cookie = (f"{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; "
                  f"Max-Age={cfg.session_hours * 3600}").encode("utf-8")
        return _redirect("/", [(b"set-cookie", cookie)])

    def h_logout(req: Request, c: sqlite3.Connection):
        if req.session:
            c.execute("DELETE FROM session WHERE token=?", (req.session["token"],))
            c.commit()
        return _redirect("/login", [(b"set-cookie",
                                     f"{COOKIE}=; Path=/; Max-Age=0".encode())])

    def h_dash(req: Request, c: sqlite3.Connection):
        d1, d2 = range_of(req)
        clinic_id = req.q("clinic")
        cid = int(clinic_id) if clinic_id.isdigit() else None
        return _html(render(
            "dash", req, "dash", title="Overview",
            d1=d1.isoformat(), d2=d2.isoformat(),
            t=report.totals(c, d1, d2, cid), rows=report.by_clinic(c, d1, d2),
            syncs=report.recent_syncs(c), clinics=report.clinics(c),
            clinic_id=cid, show_search=False, q=""))

    def h_roster(req: Request, c: sqlite3.Connection):
        d1, d2 = range_of(req)
        cid = int(req.q("clinic")) if req.q("clinic").isdigit() else None
        search = req.q("q")
        return _html(render(
            "roster", req, "roster", title="Roster",
            d1=d1.isoformat(), d2=d2.isoformat(),
            rows=report.roster(c, d1, d2, cid, search),
            clinics=report.clinics(c), clinic_id=cid, show_search=True, q=search))

    def h_exceptions(req: Request, c: sqlite3.Connection):
        d1, d2 = range_of(req)
        cid = int(req.q("clinic")) if req.q("clinic").isdigit() else None
        return _html(render(
            "exceptions", req, "exc", title="Exceptions",
            d1=d1.isoformat(), d2=d2.isoformat(),
            rows=report.exceptions(c, d1, d2, cid),
            clinics=report.clinics(c), clinic_id=cid, show_search=False, q=""))

    def h_employee(req: Request, c: sqlite3.Connection, emp_id: str):
        emp = report.employee(c, int(emp_id))
        if not emp:
            return _html("<p>Unknown employee.</p>", 404)
        d1, d2 = range_of(req)
        return _html(render(
            "employee", req, "roster", title=emp.get("name") or emp["empcode"],
            emp=emp, days=report.employee_days(c, int(emp_id), d1, d2),
            clinics=report.clinics(c), shifts=report.shifts(c),
            d1=d1.isoformat(), d2=d2.isoformat()))

    def h_employee_assign(req: Request, c: sqlite3.Connection, emp_id: str):
        emp_id_int = int(emp_id)
        c.execute("UPDATE employee SET clinic_id=?, shift_id=? WHERE id=?",
                  (req.f_int("clinic_id"), req.f_int("shift_id"), emp_id_int))
        c.commit()
        row = c.execute("SELECT * FROM employee WHERE id=?", (emp_id_int,)).fetchone()
        today = dt.date.today()
        sync.recompute_employee(c, row, today - dt.timedelta(days=120), today)
        return _redirect(_flash(f"/employee/{emp_id}",
                                "Saved. Attendance recomputed for the last 120 days."))

    def h_export(req: Request, c: sqlite3.Connection):
        d1, d2 = range_of(req)
        cid = int(req.q("clinic")) if req.q("clinic").isdigit() else None
        body = report.export_csv(c, d1, d2, cid).encode("utf-8")
        name = f"attendance-{d1}-to-{d2}.csv"
        return 200, [(b"content-type", b"text/csv; charset=utf-8"),
                     (b"content-disposition",
                      f'attachment; filename="{name}"'.encode()),
                     (b"cache-control", b"no-store")], body

    # ----------------------------------------------------------- connections
    def h_accounts(req: Request, c: sqlite3.Connection):
        probe = state.pop("probe", None)
        return _html(render("accounts", req, "acct", title="Connections",
                            rows=report.accounts(c), probe=probe))

    def h_account_new(req: Request, c: sqlite3.Connection):
        return _html(render("account_form", req, "acct", title="Add connection",
                            a=None, action="/accounts",
                            clinics=report.clinics(c), default_base=DEFAULT_BASE))

    def h_account_edit(req: Request, c: sqlite3.Connection, acct_id: str):
        row = report.account(c, int(acct_id))
        if not row:
            return _html("<p>Unknown connection.</p>", 404)
        view = dict(row)
        view["has_secret"] = bool(view.pop("secret", ""))
        return _html(render("account_form", req, "acct", title="Edit connection",
                            a=view, action=f"/accounts/{acct_id}",
                            clinics=report.clinics(c), default_base=DEFAULT_BASE))

    def _account_fields(req: Request) -> dict[str, Any]:
        return {
            "label": req.f("label"),
            "kind": req.f("kind", "etimeoffice"),
            "base_url": req.f("base_url"),
            "corp_id": req.f("corp_id"),
            "user_id": req.f("user_id"),
            "admin_flag": req.f("admin_flag", "true"),
            "clinic_id": req.f_int("clinic_id"),
            "enabled": 1 if req.f("enabled") else 0,
        }

    def h_account_create(req: Request, c: sqlite3.Connection):
        v = _account_fields(req)
        if not v["label"]:
            return _redirect(_flash("/accounts/new", "A label is required.", "err"))
        try:
            secret = box().seal(req.f("password"))
        except SecretError as exc:
            return _redirect(_flash("/accounts/new", str(exc), "err"))
        try:
            c.execute(
                """INSERT INTO account (label, kind, base_url, corp_id, user_id,
                       secret, admin_flag, clinic_id, enabled, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (v["label"], v["kind"], v["base_url"], v["corp_id"], v["user_id"],
                 secret, v["admin_flag"], v["clinic_id"], v["enabled"],
                 db.now(), db.now()))
            c.commit()
        except sqlite3.IntegrityError:
            return _redirect(_flash("/accounts/new",
                                    f"A connection named '{v['label']}' already exists.",
                                    "err"))
        return _redirect(_flash("/accounts",
                                f"Connection '{v['label']}' added. Test it to confirm "
                                "the credentials work."))

    def h_account_update(req: Request, c: sqlite3.Connection, acct_id: str):
        row = report.account(c, int(acct_id))
        if not row:
            return _html("<p>Unknown connection.</p>", 404)
        v = _account_fields(req)
        password = req.f("password")
        try:
            secret = box().seal(password) if password else row["secret"]
        except SecretError as exc:
            return _redirect(_flash(f"/accounts/{acct_id}/edit", str(exc), "err"))
        c.execute(
            """UPDATE account SET label=?, kind=?, base_url=?, corp_id=?, user_id=?,
                   secret=?, admin_flag=?, clinic_id=?, enabled=?, updated_at=?
               WHERE id=?""",
            (v["label"], v["kind"], v["base_url"], v["corp_id"], v["user_id"],
             secret, v["admin_flag"], v["clinic_id"], v["enabled"], db.now(),
             int(acct_id)))
        c.commit()
        return _redirect(_flash("/accounts", "Connection updated."))

    def h_account_delete(req: Request, c: sqlite3.Connection, acct_id: str):
        row = report.account(c, int(acct_id))
        if row:
            c.execute("DELETE FROM account WHERE id=?", (int(acct_id),))
            c.commit()
            return _redirect(_flash("/accounts",
                                    f"Connection '{row['label']}' deleted. "
                                    "Punches already imported were kept."))
        return _redirect("/accounts")

    async def h_account_test(req: Request, c: sqlite3.Connection, acct_id: str):
        row = report.account(c, int(acct_id))
        if not row:
            return _html("<p>Unknown connection.</p>", 404)
        label = row["label"]

        def work() -> Any:
            local = db.connect(cfg.db_path)
            try:
                acct = report.account(local, int(acct_id))
                return sync.probe_account(local, acct, box(), cfg)
            finally:
                local.close()
        try:
            result = await asyncio.to_thread(work)
        except SecretError as exc:
            return _redirect(_flash("/accounts", str(exc), "err"))
        state["probe"] = {"label": label, "status": result.status,
                          "detail": result.detail, "sample": result.sample}
        return _redirect(_flash("/accounts", f"{label}: {result.detail}",
                                "ok" if result.ok else "err"))

    async def h_account_sync(req: Request, c: sqlite3.Connection, acct_id: str):
        row = report.account(c, int(acct_id))
        if not row:
            return _html("<p>Unknown connection.</p>", 404)
        label, kind = row["label"], row["kind"]
        if kind == "csv" and not row["base_url"]:
            return _redirect(_flash(
                "/accounts",
                f"'{label}' is a CSV connection with no file path set.", "err"))

        def work() -> dict:
            local = db.connect(cfg.db_path)
            try:
                acct = report.account(local, int(acct_id))
                today = dt.date.today()
                return sync.sync_account(
                    local, acct, box(), cfg,
                    today - dt.timedelta(days=cfg.lookback_days), today)
            finally:
                local.close()
        try:
            stats = await asyncio.to_thread(work)
        except SecretError as exc:
            return _redirect(_flash("/accounts", str(exc), "err"))
        if stats["status"] == "ok":
            msg = (f"{label}: {stats['fetched']} record(s) read, "
                   f"{stats['inserted']} new punch(es), "
                   f"{stats['days']} day(s) recomputed.")
            return _redirect(_flash("/accounts", msg))
        return _redirect(_flash("/accounts", f"{label}: {stats['error']}", "err"))

    # --------------------------------------------------------------- settings
    def h_settings(req: Request, c: sqlite3.Connection):
        return _html(render("settings", req, "set", title="Settings",
                            clinics=report.clinics(c), shifts=report.shifts(c),
                            dows=DOWS))

    def h_settings_clinic(req: Request, c: sqlite3.Connection):
        code, name = req.f("code"), req.f("name")
        if not code or not name:
            return _redirect(_flash("/settings", "Code and name are required.", "err"))
        try:
            dow = int(req.f("week_off_dow", "6"))
        except ValueError:
            dow = 6
        db.upsert_clinic(c, code, name, dow)
        return _redirect(_flash("/settings", f"Clinic '{name}' saved."))

    def h_settings_shift(req: Request, c: sqlite3.Connection):
        def mins(raw: str, fallback: int) -> int:
            try:
                h, m = raw.split(":")[:2]
                return int(h) * 60 + int(m)
            except (ValueError, AttributeError):
                return fallback
        start = mins(req.f("start"), 540)
        end = mins(req.f("end"), 1080)
        if req.f("overnight") or end <= start:
            end += 24 * 60
        try:
            half = int(float(req.f("half", "4")) * 60)
            present = int(float(req.f("present", "7")) * 60)
            full = int(float(req.f("full", "8")) * 60)
            g_in = int(req.f("grace_in", "15") or 15)
            g_out = int(req.f("grace_out", "15") or 15)
        except ValueError:
            return _redirect(_flash("/settings", "Numeric fields were invalid.", "err"))
        code, name = req.f("code"), req.f("name")
        if not code or not name:
            return _redirect(_flash("/settings", "Code and name are required.", "err"))
        db.upsert_shift(c, code, name, start, end, g_in, g_out, half, full,
                        present)
        return _redirect(_flash("/settings", f"Shift '{name}' saved."))

    async def h_settings_import(req: Request, c: sqlite3.Connection):
        path, clinic_id = req.f("path"), req.f_int("clinic_id")
        if not path:
            return _redirect(_flash("/settings", "A file path is required.", "err"))
        label = f"import:{path.rsplit('/', 1)[-1]}"

        def work() -> dict:
            local = db.connect(cfg.db_path)
            try:
                local.execute(
                    """INSERT INTO account (label, kind, base_url, clinic_id,
                           enabled, created_at, updated_at)
                       VALUES (?,'csv',?,?,0,?,?)
                       ON CONFLICT(label) DO UPDATE SET base_url=excluded.base_url,
                           clinic_id=excluded.clinic_id, updated_at=excluded.updated_at""",
                    (label, path, clinic_id, db.now(), db.now()))
                local.commit()
                acct = local.execute("SELECT * FROM account WHERE label=?",
                                     (label,)).fetchone()
                today = dt.date.today()
                return sync.sync_account(local, acct, box(), cfg,
                                         today - dt.timedelta(days=400), today,
                                         csv_path=path)
            finally:
                local.close()
        stats = await asyncio.to_thread(work)
        if stats["status"] == "ok":
            return _redirect(_flash(
                "/settings",
                f"Imported {stats['inserted']} new punch(es) from "
                f"{stats['fetched']} record(s); {stats['days']} day(s) computed."))
        return _redirect(_flash("/settings", stats["error"], "err"))

    # ---------------------------------------------------------------- routing
    ROUTES: list[tuple[str, re.Pattern, Callable, bool]] = [
        ("GET", re.compile(r"^/login$"), h_login_get, False),
        ("POST", re.compile(r"^/login$"), h_login_post, False),
        ("POST", re.compile(r"^/logout$"), h_logout, True),
        ("GET", re.compile(r"^/$"), h_dash, True),
        ("GET", re.compile(r"^/roster$"), h_roster, True),
        ("GET", re.compile(r"^/exceptions$"), h_exceptions, True),
        ("GET", re.compile(r"^/export\.csv$"), h_export, True),
        ("GET", re.compile(r"^/employee/(\d+)$"), h_employee, True),
        ("POST", re.compile(r"^/employee/(\d+)/assign$"), h_employee_assign, True),
        ("GET", re.compile(r"^/accounts$"), h_accounts, True),
        ("GET", re.compile(r"^/accounts/new$"), h_account_new, True),
        ("POST", re.compile(r"^/accounts$"), h_account_create, True),
        ("GET", re.compile(r"^/accounts/(\d+)/edit$"), h_account_edit, True),
        ("POST", re.compile(r"^/accounts/(\d+)$"), h_account_update, True),
        ("POST", re.compile(r"^/accounts/(\d+)/test$"), h_account_test, True),
        ("POST", re.compile(r"^/accounts/(\d+)/sync$"), h_account_sync, True),
        ("POST", re.compile(r"^/accounts/(\d+)/delete$"), h_account_delete, True),
        ("GET", re.compile(r"^/settings$"), h_settings, True),
        ("POST", re.compile(r"^/settings/clinic$"), h_settings_clinic, True),
        ("POST", re.compile(r"^/settings/shift$"), h_settings_shift, True),
        ("POST", re.compile(r"^/settings/import$"), h_settings_import, True),
    ]

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            return

        body = b""
        if scope["method"] in ("POST", "PUT", "PATCH"):
            chunks = []
            while True:
                message = await receive()
                if message["type"] == "http.request":
                    chunks.append(message.get("body", b""))
                    if not message.get("more_body"):
                        break
                elif message["type"] == "http.disconnect":
                    return
            body = b"".join(chunks)

        req = Request(scope, body)
        c = db.connect(cfg.db_path)
        try:
            req.session = load_session(c, req)
            status, headers, out = await _dispatch(req, c)
        except Exception as exc:                      # never leak a traceback
            status, headers, out = _html(
                "<h1>Server error</h1><p>The request could not be completed. "
                "Check the service log.</p>", 500)
            print(f"[attendance] unhandled error on {req.method} {req.path}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
        finally:
            c.close()

        await send({"type": "http.response.start", "status": status,
                    "headers": headers})
        await send({"type": "http.response.body", "body": out})

    async def _dispatch(req: Request, c: sqlite3.Connection):
        for method, pattern, handler, protected in ROUTES:
            match = pattern.match(req.path)
            if not match or method != req.method:
                continue
            if protected and req.session is None:
                return _redirect("/login")
            if req.method == "POST" and req.path != "/login":
                expected = req.session["csrf"] if req.session else ""
                if not expected or not secrets.compare_digest(req.f("_csrf"), expected):
                    return _html("<h1>403</h1><p>Session expired. "
                                 "<a href='/'>Reload</a> and try again.</p>", 403)
            result = handler(req, c, *match.groups())
            if asyncio.iscoroutine(result):
                result = await result
            return result
        return _html("<h1>404</h1><p>No such page.</p>", 404)

    return app
