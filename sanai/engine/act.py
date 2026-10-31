"""Stage 4: carry out a task using only concepts San AI has actually mastered.

The loop is plan -> preflight -> execute -> verify, with a bounded
diagnose-and-replan cycle when a step fails. If no mastered module covers the
request, the task is refused with a pointer to what needs learning first -
that refusal is the point of the whole design.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..executor import Executor, Result
from ..providers import LLM
from . import reflect as reflect_stage
from .distill import render_card

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "understanding": {
            "type": "string",
            "description": "Restate the task as you understand it, including what you will NOT do.",
        },
        "blocked": {
            "type": "boolean",
            "description": "True if your knowledge is insufficient to do this safely.",
        },
        "blocked_reason": {"type": "string"},
        "preflight": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Read-only commands that confirm your assumptions before changing anything.",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "command": {"type": "string"},
                    "rationale": {"type": "string"},
                    "risk": {
                        "type": "string",
                        "enum": ["read-only", "mutating", "destructive"],
                    },
                    "expect": {
                        "type": "string",
                        "description": "What success looks like for this step.",
                    },
                    "rollback": {
                        "type": "string",
                        "description": "How to undo this step, or 'none' if it cannot be undone.",
                    },
                },
                "required": ["n", "command", "rationale", "risk", "expect", "rollback"],
                "additionalProperties": False,
            },
        },
        "final_verification": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Commands proving the task as a whole succeeded.",
        },
    },
    "required": [
        "understanding",
        "blocked",
        "blocked_reason",
        "preflight",
        "steps",
        "final_verification",
    ],
    "additionalProperties": False,
}

DIAGNOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "recoverable": {"type": "boolean"},
        "abandon_reason": {"type": "string"},
        "next_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "command": {"type": "string"},
                    "rationale": {"type": "string"},
                    "risk": {
                        "type": "string",
                        "enum": ["read-only", "mutating", "destructive"],
                    },
                    "expect": {"type": "string"},
                    "rollback": {"type": "string"},
                },
                "required": ["n", "command", "rationale", "risk", "expect", "rollback"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["analysis", "recoverable", "abandon_reason", "next_steps"],
    "additionalProperties": False,
}

PLANNER_SYSTEM = """You are San AI, operating a live Linux server \
unsupervised. You are planning real commands that will run without anyone \
reviewing them first.

You know only what your notes below contain. Work strictly within them.

Rules:
- Every step is one shell command. No interactive commands - they will hang \
and be killed. Add non-interactive flags (-y, --non-interactive, \
DEBIAN_FRONTEND=noninteractive) wherever a prompt is possible.
- Put read-only checks in preflight. Never assume a file, service, package or \
port is in the state you expect: confirm it.
- Back up any file before you edit it, and name the backup in the step.
- Validate before activating. Where the tool offers a check (nginx -t, \
sshd -t, visudo -c), run it as its own step before the step that applies it.
- Order matters. A step must not depend on something a later step creates.
- Set blocked=true when your notes genuinely do not cover what the task needs. \
Refusing is correct and costs nothing; guessing on a production server is an \
outage. Say exactly what you would need to learn.
- Prefer the reversible form of an action. If a step cannot be undone, say so \
in rollback and keep it as late in the plan as possible."""

DIAGNOSER_SYSTEM = """You are San AI. A step in your plan just failed on a \
live server. Read the actual output - not what you expected it to say - and \
work out why.

Set recoverable=false when you cannot fix this from your current knowledge, \
when fixing it would need a command your notes do not support, or when the \
safe move is to stop and leave the system as it is. Stopping with a clear \
explanation is a good outcome. Thrashing through variations of a failing \
command is not: do not retry the same command hoping for a different result, \
and do not escalate to a blunter tool to force it through.

Only propose next_steps you can justify from the output you actually saw."""


@dataclass
class TaskOutcome:
    task_id: int
    status: str                      # done | failed | blocked | halted
    summary: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    results: list[Result] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    reflection: dict[str, Any] = field(default_factory=dict)
    pending: list[dict[str, Any]] = field(default_factory=list)


def select_modules(
    conn: sqlite3.Connection, request: str, limit: int = 4
) -> list[sqlite3.Row]:
    """Mastered modules relevant to this request, best match first."""
    mastered = [
        r for r in db.list_modules(conn) if r["status"] == "mastered"
    ]
    if not mastered:
        return []

    words = {w for w in _words(request) if len(w) > 3}
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in mastered:
        score = 0.0
        name_words = set(_words(row["name"]))
        if name_words & words:
            score += 4.0 * len(name_words & words)
        if row["name"].lower() in request.lower():
            score += 6.0
        card = _load_card(row)
        blob = " ".join(
            [card.get("one_line", "")]
            + card.get("key_facts", [])
            + [c.get("command", "") for c in card.get("commands", [])]
        ).lower()
        score += sum(0.4 for w in words if w in blob)
        if score > 0:
            scored.append((score, row))

    # BM25 over raw sources catches wording the card does not use.
    for hit in db.search_chunks(conn, request, limit=12):
        for row in mastered:
            if row["name"] == hit["module_name"]:
                scored.append((1.0, row))

    best: dict[str, tuple[float, sqlite3.Row]] = {}
    for score, row in scored:
        name = row["name"]
        if name not in best or score > best[name][0]:
            best[name] = (score, row)
    ordered = sorted(best.values(), key=lambda t: -t[0])
    return [row for _s, row in ordered[:limit]]


def run_task(
    llm: LLM,
    conn: sqlite3.Connection,
    cfg: Settings,
    executor: Executor,
    request: str,
    *,
    require_mastery: bool = True,
    dry_run: bool = False,
    on_event: Callable[..., None] | None = None,
) -> TaskOutcome:
    say = on_event or (lambda *_a, **_k: None)

    rows = select_modules(conn, request)
    names = [r["name"] for r in rows]
    task_id = db.create_task(conn, request, names)
    db.audit(conn, "task.created", {"task_id": task_id, "request": request, "modules": names})

    if require_mastery and not rows:
        msg = (
            "No mastered module covers this request. Teach it first, e.g.\n"
            f"  sanai learn \"<concept>\" --note \"...\" --url <docs>"
        )
        db.update_task(conn, task_id, status="blocked", result=msg)
        db.audit(conn, "task.blocked", {"task_id": task_id, "reason": "no mastered module"})
        return TaskOutcome(task_id, "blocked", msg, modules=[])

    say("plan", f"planning with module(s): {', '.join(names) or 'none'}")
    knowledge = "\n\n".join(render_card(_load_card(r)) for r in rows) or "(no notes)"
    lessons = _lesson_text(conn, rows)

    try:
        plan = llm.json(
            PLANNER_SYSTEM,
            f"TASK: {request}\n\n"
            f"YOUR NOTES:\n{knowledge}\n\n"
            f"{lessons}"
            f"AUTONOMY: {cfg.autonomy.value} "
            f"(integrity guard {'on' if cfg.integrity_guard else 'off'})\n"
            f"WORKING DIRECTORY: {cfg.workdir}\n\n"
            "Produce the plan.",
            PLAN_SCHEMA,
            deep=True,
        )
    except Exception as exc:  # noqa: BLE001 - surface planning failure as a task failure
        msg = f"planning failed: {exc}"
        db.update_task(conn, task_id, status="failed", result=msg)
        db.audit(conn, "task.plan_error", {"task_id": task_id, "error": str(exc)})
        return TaskOutcome(task_id, "failed", msg, modules=names)

    db.update_task(conn, task_id, plan_json=plan)

    if plan.get("blocked"):
        reason = plan.get("blocked_reason") or "insufficient knowledge"
        db.update_task(conn, task_id, status="blocked", result=reason)
        db.audit(conn, "task.blocked", {"task_id": task_id, "reason": reason})
        say("blocked", reason)
        return TaskOutcome(task_id, "blocked", reason, plan=plan, modules=names)

    if dry_run:
        db.update_task(conn, task_id, status="pending", result="dry run - nothing executed")
        return TaskOutcome(
            task_id, "blocked", "dry run - nothing executed", plan=plan, modules=names
        )

    db.update_task(conn, task_id, status="running")
    results: list[Result] = []
    module_id = int(rows[0]["id"]) if rows else None

    # -- preflight ---------------------------------------------------------
    for i, cmd in enumerate(plan.get("preflight", [])[:12]):
        say("preflight", cmd)
        results.append(
            executor.run(
                cmd,
                task_id=task_id,
                module_id=module_id,
                step=-(i + 1),
                rationale="preflight check",
            )
        )

    # -- main loop ---------------------------------------------------------
    queue = list(plan.get("steps", []))
    pending: list[dict[str, Any]] = []
    status = "done"
    summary = ""
    rounds = 0
    step_no = 0

    while queue:
        rounds += 1
        if rounds > cfg.max_act_rounds:
            status, summary = "failed", (
                f"stopped after {cfg.max_act_rounds} rounds without completing"
            )
            pending = queue
            break

        step = queue.pop(0)
        step_no += 1
        cmd = (step.get("command") or "").strip()
        if not cmd:
            continue

        say("step", f"[{step_no}] {cmd}")
        res = executor.run(
            cmd,
            task_id=task_id,
            module_id=module_id,
            step=step_no,
            rationale=step.get("rationale", ""),
        )
        results.append(res)

        if not res.ran:
            # Policy refused it. That is a decision, not a failure to debug.
            if "kill switch" in res.decision.reason:
                status, summary = "halted", res.decision.reason
                pending = queue
                break
            if res.decision.action == "queued":
                status, summary = "blocked", (
                    f"awaiting approval for: {cmd} ({res.decision.reason})"
                )
                pending = [step, *queue]
                # Persist the remainder so `sanai approve` can resume it.
                db.update_task(
                    conn,
                    task_id,
                    status="awaiting_approval",
                    plan_json={**plan, "_pending": pending},
                    result=summary,
                )
                break
            status, summary = "failed", f"refused: {res.decision.reason}"
            pending = queue
            break

        if res.ok:
            continue

        say("diagnose", f"step {step_no} exited {res.exit_code}")
        verdict = _diagnose(llm, request, knowledge, step, res, results)
        if not verdict.get("recoverable"):
            status = "failed"
            summary = verdict.get("abandon_reason") or verdict.get("analysis", "step failed")
            pending = queue
            break

        repairs = verdict.get("next_steps", [])[:6]
        say("diagnose", f"recovering with {len(repairs)} step(s)")
        queue = [*repairs, *queue]

    # -- final verification ------------------------------------------------
    if status == "done":
        for i, cmd in enumerate(plan.get("final_verification", [])[:10]):
            say("verify", cmd)
            res = executor.run(
                cmd,
                task_id=task_id,
                module_id=module_id,
                step=1000 + i,
                rationale="final verification",
            )
            results.append(res)
            if res.ran and not res.ok:
                status = "failed"
                summary = f"verification failed: {cmd} (exit {res.exit_code})"
                break
        if status == "done":
            summary = plan.get("understanding", "completed")

    if status != "blocked" or not pending:
        db.update_task(conn, task_id, status=status, result=summary)
    db.audit(
        conn,
        "task.finished",
        {
            "task_id": task_id,
            "status": status,
            "commands": len(results),
            "cost_usd": round(llm.usage.cost_usd, 4),
        },
    )

    # -- reflect -----------------------------------------------------------
    reflection: dict[str, Any] = {}
    if results and status in ("done", "failed"):
        say("reflect", "writing lessons back to memory")
        reflection = reflect_stage.reflect(
            llm, conn, cfg, task_id, request, names, results
        )

    return TaskOutcome(
        task_id=task_id,
        status=status,
        summary=summary,
        plan=plan,
        results=results,
        modules=names,
        reflection=reflection,
        pending=pending,
    )


def _diagnose(
    llm: LLM,
    request: str,
    knowledge: str,
    step: dict[str, Any],
    res: Result,
    history: list[Result],
) -> dict[str, Any]:
    recent = "\n".join(f"$ {r.command} -> exit {r.exit_code}" for r in history[-8:])
    try:
        return llm.json(
            DIAGNOSER_SYSTEM,
            f"TASK: {request}\n\n"
            f"YOUR NOTES:\n{knowledge}\n\n"
            f"RECENT COMMANDS:\n{recent}\n\n"
            f"FAILED STEP: {step.get('command')}\n"
            f"WHY YOU RAN IT: {step.get('rationale', '')}\n"
            f"WHAT YOU EXPECTED: {step.get('expect', '')}\n\n"
            f"ACTUAL RESULT:\n{res.summary()}",
            DIAGNOSE_SCHEMA,
            deep=True,
        )
    except Exception as exc:  # noqa: BLE001 - a failed diagnosis stops the task
        return {
            "analysis": f"diagnosis failed: {exc}",
            "recoverable": False,
            "abandon_reason": f"could not diagnose the failure: {exc}",
            "next_steps": [],
        }


def _lesson_text(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> str:
    out: list[str] = []
    for row in rows:
        for exp in db.experiences(conn, int(row["id"]), limit=6):
            out.append(f"- [{row['name']}] {exp['lesson']}")
    if not out:
        return ""
    return "LESSONS FROM YOUR PAST RUNS ON THIS SERVER:\n" + "\n".join(out) + "\n\n"


def _load_card(row: sqlite3.Row) -> dict[str, Any]:
    try:
        card = json.loads(row["card_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        card = {}
    card.setdefault("_module", row["name"])
    return card


def _words(text: str) -> list[str]:
    return [w for w in "".join(
        ch if ch.isalnum() else " " for ch in text.lower()
    ).split()]
