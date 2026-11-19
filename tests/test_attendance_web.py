"""Dashboard: auth, CSRF, and that credentials go in encrypted and never come back."""
import asyncio
import urllib.parse

import pytest

from attendance import db
from attendance.config import Settings
from attendance.crypto import SecretBox
from attendance.web import create_app

PASSWORD = "dashboard-secret"


def call(app, method, path, form=None, cookie="", query=""):
    body = urllib.parse.urlencode(form or {}).encode() if form else b""
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if body:
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))
    scope = {"type": "http", "method": method, "path": path,
             "query_string": query.encode(), "headers": headers}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    out = {"body": b"", "headers": []}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = msg["headers"]
        else:
            out["body"] += msg.get("body", b"")

    asyncio.run(app(scope, receive, send))
    out["text"] = out["body"].decode("utf-8", "replace")
    out["location"] = next((v.decode() for k, v in out["headers"]
                            if k == b"location"), "")
    return out


@pytest.fixture()
def rig(tmp_path):
    cfg = Settings(db_path=tmp_path / "web.db",
                   secret_key=SecretBox.generate_key(),
                   web_password=PASSWORD)
    app = create_app(cfg)
    conn = db.connect(cfg.db_path)
    return app, cfg, conn


def login(app):
    res = call(app, "POST", "/login", {"password": PASSWORD})
    cookie = next((v.decode() for k, v in res["headers"] if k == b"set-cookie"), "")
    return cookie.split(";")[0]


def csrf_of(conn):
    return conn.execute("SELECT csrf FROM session LIMIT 1").fetchone()["csrf"]


# ------------------------------------------------------------------- auth

def test_dashboard_requires_login(rig):
    app, _cfg, _conn = rig
    res = call(app, "GET", "/")
    assert res["status"] == 303 and res["location"] == "/login"


def test_wrong_password_rejected(rig):
    app, _cfg, _conn = rig
    res = call(app, "POST", "/login", {"password": "nope"})
    assert res["status"] == 303 and "/login" in res["location"]
    assert "kind=err" in res["location"]


def test_login_then_dashboard(rig):
    app, _cfg, _conn = rig
    cookie = login(app)
    assert cookie.startswith("att_session=")
    res = call(app, "GET", "/", cookie=cookie)
    assert res["status"] == 200
    assert "Overview" in res["text"]


def test_session_cookie_is_httponly_and_samesite(rig):
    app, _cfg, _conn = rig
    res = call(app, "POST", "/login", {"password": PASSWORD})
    raw = next(v.decode() for k, v in res["headers"] if k == b"set-cookie")
    assert "HttpOnly" in raw and "SameSite=Lax" in raw


def test_post_without_csrf_is_refused(rig):
    app, _cfg, _conn = rig
    cookie = login(app)
    res = call(app, "POST", "/accounts", {"label": "x"}, cookie=cookie)
    assert res["status"] == 403


def test_unknown_path_404(rig):
    app, _cfg, _conn = rig
    cookie = login(app)
    assert call(app, "GET", "/nope", cookie=cookie)["status"] == 404


# -------------------------------------------------------------- connections

def test_add_connection_encrypts_the_password(rig):
    app, cfg, conn = rig
    cookie = login(app)
    res = call(app, "POST", "/accounts", {
        "_csrf": csrf_of(conn), "label": "Karol Bagh", "kind": "etimeoffice",
        "corp_id": "ACME", "user_id": "admin", "password": "super-secret",
        "admin_flag": "true", "enabled": "1"}, cookie=cookie)
    assert res["status"] == 303

    row = conn.execute("SELECT * FROM account WHERE label='Karol Bagh'").fetchone()
    assert row["corp_id"] == "ACME"
    assert row["secret"] and "super-secret" not in row["secret"]
    assert SecretBox(cfg.secret_key).open(row["secret"]) == "super-secret"


def test_stored_password_is_never_rendered(rig):
    app, cfg, conn = rig
    cookie = login(app)
    call(app, "POST", "/accounts", {
        "_csrf": csrf_of(conn), "label": "C1", "kind": "etimeoffice",
        "corp_id": "ACME", "user_id": "admin", "password": "leak-me"},
        cookie=cookie)
    acct_id = conn.execute("SELECT id FROM account WHERE label='C1'").fetchone()["id"]
    for path in ("/accounts", f"/accounts/{acct_id}/edit"):
        text = call(app, "GET", path, cookie=cookie)["text"]
        assert "leak-me" not in text
        stored = conn.execute("SELECT secret FROM account WHERE id=?",
                              (acct_id,)).fetchone()["secret"]
        assert stored not in text, "the encrypted token must not reach the page either"


def test_edit_without_password_keeps_the_old_one(rig):
    app, cfg, conn = rig
    cookie = login(app)
    call(app, "POST", "/accounts", {
        "_csrf": csrf_of(conn), "label": "C2", "corp_id": "A", "user_id": "u",
        "password": "keep-me"}, cookie=cookie)
    acct_id = conn.execute("SELECT id FROM account WHERE label='C2'").fetchone()["id"]
    call(app, "POST", f"/accounts/{acct_id}", {
        "_csrf": csrf_of(conn), "label": "C2 renamed", "corp_id": "A",
        "user_id": "u", "password": ""}, cookie=cookie)
    row = conn.execute("SELECT * FROM account WHERE id=?", (acct_id,)).fetchone()
    assert row["label"] == "C2 renamed"
    assert SecretBox(cfg.secret_key).open(row["secret"]) == "keep-me"


def test_duplicate_label_is_reported_not_crashed(rig):
    app, _cfg, conn = rig
    cookie = login(app)
    form = {"_csrf": csrf_of(conn), "label": "Dup", "corp_id": "A", "user_id": "u",
            "password": "p"}
    call(app, "POST", "/accounts", form, cookie=cookie)
    res = call(app, "POST", "/accounts", form, cookie=cookie)
    assert res["status"] == 303 and "kind=err" in res["location"]
    assert conn.execute(
        "SELECT COUNT(*) FROM account WHERE label='Dup'").fetchone()[0] == 1


def test_delete_connection_keeps_imported_punches(rig):
    app, _cfg, conn = rig
    cookie = login(app)
    call(app, "POST", "/accounts", {
        "_csrf": csrf_of(conn), "label": "Gone", "corp_id": "A", "user_id": "u",
        "password": "p"}, cookie=cookie)
    acct_id = conn.execute("SELECT id FROM account WHERE label='Gone'").fetchone()["id"]
    emp = db.get_or_create_employee(conn, acct_id, "E1", "Asha")
    db.insert_punches(conn, [(emp, "2026-11-03 09:00:00", "in", "d", "Gone")])

    call(app, "POST", f"/accounts/{acct_id}/delete", {"_csrf": csrf_of(conn)},
         cookie=cookie)
    assert conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM punch").fetchone()[0] == 1


# ------------------------------------------------------------------ settings

def test_add_clinic_and_shift(rig):
    app, _cfg, conn = rig
    cookie = login(app)
    call(app, "POST", "/settings/clinic", {
        "_csrf": csrf_of(conn), "code": "KB", "name": "Karol Bagh",
        "week_off_dow": "6"}, cookie=cookie)
    call(app, "POST", "/settings/shift", {
        "_csrf": csrf_of(conn), "code": "NGT", "name": "Night",
        "start": "22:00", "end": "06:00", "overnight": "1",
        "grace_in": "10", "grace_out": "10", "half": "4", "full": "8"},
        cookie=cookie)
    assert conn.execute("SELECT name FROM clinic WHERE code='KB'").fetchone()["name"] \
        == "Karol Bagh"
    shift = conn.execute("SELECT * FROM shift WHERE code='NGT'").fetchone()
    assert shift["start_min"] == 1320 and shift["end_min"] == 1800  # rolled past midnight


def test_csv_import_from_settings(rig, tmp_path):
    app, _cfg, conn = rig
    cookie = login(app)
    csv_path = tmp_path / "imp.csv"
    csv_path.write_text("Empcode,Name,Date,INTime,OUTTime\n"
                        "E7,Meena,03/11/2026,09:00,18:00\n")
    res = call(app, "POST", "/settings/import",
               {"_csrf": csrf_of(conn), "path": str(csv_path)}, cookie=cookie)
    assert res["status"] == 303 and "kind=err" not in res["location"]
    assert conn.execute(
        "SELECT COUNT(*) FROM punch").fetchone()[0] == 2


def test_export_csv_is_a_download(rig):
    app, _cfg, _conn = rig
    cookie = login(app)
    res = call(app, "GET", "/export.csv", cookie=cookie)
    ctype = next(v.decode() for k, v in res["headers"] if k == b"content-type")
    assert res["status"] == 200 and "text/csv" in ctype
    assert res["text"].startswith("date,empcode,name,clinic,status")


def test_logout_clears_the_session(rig):
    app, _cfg, conn = rig
    cookie = login(app)
    call(app, "POST", "/logout", {"_csrf": csrf_of(conn)}, cookie=cookie)
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0
    assert call(app, "GET", "/", cookie=cookie)["location"] == "/login"
