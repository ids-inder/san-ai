import json

import pytest
from fastapi.testclient import TestClient

from sanai import db
from sanai.config import Settings
from sanai.web.app import create_app

TOKEN = "test-token-abcdefghijklmnop"


@pytest.fixture()
def client(tmp_path):
    cfg = Settings(
        db_path=tmp_path / "sanai.db",
        workdir=tmp_path / "work",
        web_token=TOKEN,
        _env_file=None,
    )
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    mid = db.upsert_module(conn, "nginx", "serve the app")
    db.set_module_card(conn, mid, {
        "one_line": "A reverse proxy.", "mental_model": "It forwards requests.",
        "primitives": [], "key_facts": ["config in /etc/nginx"], "commands": [],
        "invariants": [], "failure_modes": [], "verification": [], "open_questions": [],
    })
    db.set_module_status(conn, mid, "mastered", 0.93)
    db.add_source(conn, mid, "nginx", "note", "manual", "Nginx forwards requests upstream. " * 4)
    db.add_exam(conn, mid, 1, 0.93,
                [{"id": 1, "question": "q", "kind": "recall", "weight": 3,
                  "reference_answer": "r", "answer": "a", "unsure": False,
                  "score": 1.0, "verdict": "correct"}], [])
    tid = db.create_task(conn, "set up a vhost", ["nginx"])
    db.update_task(conn, tid, status="done", result="done",
                   plan_json={"understanding": "u", "steps": [
                       {"n": 1, "command": "nginx -t", "rationale": "r",
                        "risk": "read-only", "expect": "ok", "rollback": "none"}]})
    db.add_run(conn, task_id=tid, command="nginx -t", decision="allow",
               exit_code=0, stdout="syntax is ok", duration=0.2)
    return TestClient(create_app(cfg)), cfg, conn, tid


def test_requires_a_token(client):
    c, _cfg, _conn, _tid = client
    assert c.get("/").status_code == 401
    assert c.get("/?token=wrong").status_code == 401
    assert c.get("/", headers={"x-sanai-token": "nope"}).status_code == 401


def test_token_accepted_three_ways(client):
    c, _cfg, _conn, _tid = client
    assert c.get(f"/?token={TOKEN}").status_code == 200
    c.cookies.clear()
    assert c.get("/", headers={"x-sanai-token": TOKEN}).status_code == 200
    assert c.get("/", headers={"authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_overview_shows_modules_and_tasks(client):
    c, _cfg, _conn, _tid = client
    body = c.get(f"/?token={TOKEN}").text
    assert "nginx" in body and "mastered" in body
    assert "set up a vhost" in body


def test_module_page_renders_card_and_exam(client):
    c, _cfg, _conn, _tid = client
    body = c.get(f"/module/nginx?token={TOKEN}").text
    assert "A reverse proxy." in body
    assert "config in /etc/nginx" in body
    assert "93%" in body
    assert c.get(f"/module/absent?token={TOKEN}").status_code == 404


def test_task_page_shows_commands(client):
    c, _cfg, _conn, tid = client
    body = c.get(f"/task/{tid}?token={TOKEN}").text
    assert "nginx -t" in body and "syntax is ok" in body


def test_kill_switch_round_trip(client):
    c, _cfg, _conn, _tid = client
    assert c.get("/healthz").json()["stopped"] is False
    c.post(f"/stop?token={TOKEN}", follow_redirects=False)
    assert c.get("/healthz").json()["stopped"] is True
    assert "KILL SWITCH ENGAGED" in c.get(f"/?token={TOKEN}").text
    c.post(f"/resume?token={TOKEN}", follow_redirects=False)
    assert c.get("/healthz").json()["stopped"] is False


def test_no_token_configured_is_a_503_not_an_open_door(tmp_path):
    cfg = Settings(db_path=tmp_path / "s.db", workdir=tmp_path / "w",
                   web_token="", _env_file=None)
    cfg.ensure_dirs()
    db.connect(cfg.db_path)
    c = TestClient(create_app(cfg))
    assert c.get("/").status_code == 503


def test_module_content_is_escaped(tmp_path):
    """Source material is untrusted - it must not render as markup."""
    cfg = Settings(db_path=tmp_path / "s.db", workdir=tmp_path / "w",
                   web_token=TOKEN, _env_file=None)
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    mid = db.upsert_module(conn, "xss", "")
    db.set_module_card(conn, mid, {
        "one_line": "<script>alert(1)</script>", "mental_model": "m",
        "primitives": [], "key_facts": [], "commands": [], "invariants": [],
        "failure_modes": [], "verification": [], "open_questions": [],
    })
    c = TestClient(create_app(cfg))
    body = c.get(f"/module/xss?token={TOKEN}").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
