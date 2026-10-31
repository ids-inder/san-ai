"""Read-mostly web dashboard.

Shows what San AI has learned, how it scored, what it ran and why. The only
state-changing endpoints are the kill switch, because that is the one control
an operator needs in a hurry. Approving queued commands is deliberately
CLI-only - it needs a shell on the box anyway.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import DictLoader, Environment, select_autoescape

from .. import db
from ..config import Settings, settings
from ..safety.policy import PolicyEngine
from ..engine.distill import render_card
from .templates import TEMPLATES

COOKIE = "sanai_token"


def _env() -> Environment:
    env = Environment(
        loader=DictLoader(TEMPLATES),
        autoescape=select_autoescape(default=True, default_for_string=True),
    )
    env.filters["urlencode"] = lambda v: urlencode({"": v})[1:]
    return env


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or settings()
    cfg.ensure_dirs()
    env = _env()
    policy = PolicyEngine(cfg)
    app = FastAPI(title="San AI", docs_url=None, redoc_url=None, openapi_url=None)

    def conn() -> sqlite3.Connection:
        return db.connect(cfg.db_path)

    def auth(request: Request) -> str:
        """Token from header, query or cookie. Constant-time comparison."""
        if not cfg.web_token:
            raise HTTPException(503, "SANAI_WEB_TOKEN is not configured")
        supplied = (
            request.headers.get("x-sanai-token")
            or (request.headers.get("authorization", "").removeprefix("Bearer ").strip())
            or request.query_params.get("token")
            or request.cookies.get(COOKIE)
            or ""
        )
        if not secrets.compare_digest(supplied, cfg.web_token):
            raise HTTPException(401, "bad or missing token")
        return supplied

    def shell(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        token = request.query_params.get("token")
        base = {
            "cfg": cfg,
            "stopped": policy.stopped(),
            # Carry the token through links only when it arrived that way.
            "q": f"?token={token}" if token else "",
        }
        html = env.get_template(name).render(**base, **ctx)
        resp = HTMLResponse(html)
        if token:
            resp.set_cookie(
                COOKIE, token, httponly=True, samesite="strict", max_age=86400
            )
        return resp

    # ---------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, _t: str = Depends(auth)) -> HTMLResponse:
        c = conn()
        modules = db.list_modules(c)
        tasks = db.list_tasks(c, 25)
        runs = db.recent_runs(c, 1000)
        stats = {
            "mastered": sum(1 for m in modules if m["status"] == "mastered"),
            "learning": sum(1 for m in modules if m["status"] in ("learning", "new")),
            "failed": sum(1 for m in modules if m["status"] in ("failed", "demoted")),
            "tasks": len(db.list_tasks(c, 10_000)),
            "commands": sum(1 for r in runs if r["decision"] == "allow"),
            "refused": sum(1 for r in runs if r["decision"] != "allow"),
        }
        return shell(request, "index", modules=modules, tasks=tasks, stats=stats)

    @app.get("/module/{name}", response_class=HTMLResponse)
    def module_page(name: str, request: Request, _t: str = Depends(auth)) -> HTMLResponse:
        c = conn()
        row = db.get_module(c, name)
        if not row:
            raise HTTPException(404, "no such module")
        card = json.loads(row["card_json"] or "{}")
        card.setdefault("_module", name)
        exam = db.latest_exam(c, int(row["id"]))
        sources = [
            {"kind": s["kind"], "origin": s["origin"], "size": len(s["content"])}
            for s in c.execute(
                "SELECT kind, origin, content FROM source WHERE module_id = ? ORDER BY id",
                (int(row["id"]),),
            ).fetchall()
        ]
        return shell(
            request,
            "module",
            module=row,
            card=render_card(card) if card.get("one_line") else "(not distilled yet)",
            exam=exam,
            detail=json.loads(exam["detail_json"]) if exam else [],
            gaps=json.loads(exam["gaps_json"]) if exam else [],
            lessons=db.experiences(c, int(row["id"]), limit=20),
            sources=sources,
        )

    @app.get("/task/{task_id}", response_class=HTMLResponse)
    def task_page(task_id: int, request: Request, _t: str = Depends(auth)) -> HTMLResponse:
        c = conn()
        row = db.get_task(c, task_id)
        if not row:
            raise HTTPException(404, "no such task")
        plan = json.loads(row["plan_json"] or "{}")
        return shell(
            request,
            "task",
            task=row,
            plan=plan,
            pending=plan.get("_pending", []),
            runs=db.runs_for_task(c, task_id),
        )

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request, _t: str = Depends(auth)) -> HTMLResponse:
        entries = [
            {
                "when": dt.datetime.fromtimestamp(a["created_at"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "event": a["event"],
                "payload": a["payload"],
            }
            for a in db.recent_audit(conn(), 300)
        ]
        return shell(request, "audit", entries=entries)

    # ------------------------------------------------------------- controls
    @app.post("/stop")
    def stop(request: Request, _t: str = Depends(auth)) -> RedirectResponse:
        policy.engage_stop("stopped from the dashboard")
        db.audit(conn(), "killswitch.engaged", {"via": "web"})
        return _back(request)

    @app.post("/resume")
    def resume(request: Request, _t: str = Depends(auth)) -> RedirectResponse:
        policy.release_stop()
        db.audit(conn(), "killswitch.released", {"via": "web"})
        return _back(request)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "stopped": policy.stopped()}

    return app


def _back(request: Request) -> RedirectResponse:
    token = request.query_params.get("token")
    return RedirectResponse(f"/?token={token}" if token else "/", status_code=303)
