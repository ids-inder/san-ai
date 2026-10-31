"""Stage 5: learn from what actually happened.

Execution is the only honest exam. A module whose commands keep failing in
reality is demoted back to `learning` regardless of its quiz score.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..config import Settings
from ..executor import Result
from ..providers import LLM

REFLECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["success", "partial", "failure"]},
        "summary": {"type": "string"},
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"},
                    "lesson": {
                        "type": "string",
                        "description": "A durable, reusable fact learned from this run.",
                    },
                    "knowledge_was_wrong": {
                        "type": "boolean",
                        "description": "True only if the notes themselves were incorrect.",
                    },
                },
                "required": ["module", "lesson", "knowledge_was_wrong"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["outcome", "summary", "lessons"],
    "additionalProperties": False,
}

SYSTEM = """You are San AI reviewing a task you just carried out on a live \
server. Extract only durable lessons - things that will still be true next \
time, like a precondition you missed, a flag that behaves differently than \
your notes said, or an ordering constraint you discovered.

Set knowledge_was_wrong=true ONLY when your stored notes were actually \
incorrect or incomplete about this concept. Do not set it for transient \
problems: a service that happened to be down, a typo, a network blip, a \
missing package you then installed. That flag demotes a module and forces it \
to be re-learned, so use it when the notes misled you, not when the world was \
inconvenient."""


def reflect(
    llm: LLM,
    conn: sqlite3.Connection,
    cfg: Settings,
    task_id: int,
    request: str,
    modules: list[str],
    results: list[Result],
) -> dict[str, Any]:
    """Write execution experience back into the modules involved."""
    transcript = "\n\n".join(
        f"[step {i + 1}] $ {r.command}\n{r.summary(1500)}" for i, r in enumerate(results)
    ) or "(no commands were run)"

    try:
        doc = llm.json(
            SYSTEM,
            f"TASK: {request}\n"
            f"MODULES INVOLVED: {', '.join(modules) or 'none'}\n\n"
            f"WHAT HAPPENED:\n{transcript}",
            REFLECT_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 - reflection must never fail a task
        db.audit(conn, "reflect.error", {"task_id": task_id, "error": str(exc)})
        return {"outcome": "unknown", "summary": f"reflection failed: {exc}", "lessons": []}

    demoted: list[str] = []
    for item in doc.get("lessons", []):
        name = (item.get("module") or "").strip()
        row = db.get_module(conn, name)
        if not row:
            continue
        outcome = "failure" if item.get("knowledge_was_wrong") else "success"
        db.add_experience(conn, int(row["id"]), task_id, outcome, item.get("lesson", ""))

        if item.get("knowledge_was_wrong"):
            fails = _recent_failures(conn, int(row["id"]))
            if fails >= 2:
                db.set_module_status(
                    conn, int(row["id"]), "demoted", max(0.0, float(row["mastery"]) - 0.2)
                )
                demoted.append(name)

    db.audit(
        conn,
        "reflect.done",
        {
            "task_id": task_id,
            "outcome": doc.get("outcome"),
            "lessons": len(doc.get("lessons", [])),
            "demoted": demoted,
        },
    )
    doc["demoted"] = demoted
    return doc


def _recent_failures(conn: sqlite3.Connection, module_id: int, window: int = 5) -> int:
    rows = db.experiences(conn, module_id, limit=window)
    return sum(1 for r in rows if r["outcome"] == "failure")
