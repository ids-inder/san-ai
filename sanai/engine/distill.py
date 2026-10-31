"""Stage 2: turn raw sources into a Concept Card.

The card is San AI's own understanding, written in its own words. It is the
*only* thing available during the comprehension exam - so anything the
distillation drops gets caught by the gate rather than silently lost.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..providers import LLM

SOURCE_BUDGET_CHARS = 120_000

CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "one_line": {"type": "string", "description": "The concept in one sentence."},
        "mental_model": {
            "type": "string",
            "description": "2-6 sentences: how this actually works underneath, not what it is for.",
        },
        "primitives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["name", "purpose"],
                "additionalProperties": False,
            },
        },
        "key_facts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Load-bearing facts: paths, defaults, ports, ordering rules, syntax.",
        },
        "commands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "purpose": {"type": "string"},
                    "preconditions": {"type": "string"},
                    "risk": {
                        "type": "string",
                        "enum": ["read-only", "mutating", "destructive"],
                    },
                },
                "required": ["command", "purpose", "preconditions", "risk"],
                "additionalProperties": False,
            },
        },
        "invariants": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Things that must stay true. Violating one means the change is wrong.",
        },
        "failure_modes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symptom": {"type": "string"},
                    "cause": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["symptom", "cause", "fix"],
                "additionalProperties": False,
            },
        },
        "verification": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Commands or checks that prove the work succeeded.",
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What the sources did not answer. Be honest here.",
        },
    },
    "required": [
        "one_line",
        "mental_model",
        "primitives",
        "key_facts",
        "commands",
        "invariants",
        "failure_modes",
        "verification",
        "open_questions",
    ],
    "additionalProperties": False,
}

SYSTEM = """You are the learning core of San AI, an autonomous agent that \
administers a Linux server. You are studying one concept so that you can later \
operate it unsupervised.

Write for your future self, who will have ONLY your notes and no access to \
these sources. That future self will be executing real commands on a real \
production server, so:

- Prefer mechanism over description. "How does it work" beats "what is it for".
- Record exact paths, flags, default values, ports and ordering constraints. \
Vague notes cause outages.
- Mark every command's risk honestly. If a command can lose data, say destructive.
- Write invariants as checkable statements.
- In open_questions, record what the sources genuinely did not cover. Do not \
invent coverage you do not have - an honest gap gets filled next round, a \
fabricated certainty becomes an outage.

Never guess a fact to fill a field. An empty list is better than a wrong entry."""


def build_card(
    llm: LLM,
    conn: sqlite3.Connection,
    module_id: int,
    module: str,
    goal: str,
    *,
    gaps: list[str] | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Distill the stored sources into a Concept Card."""
    sources = db.source_text(conn, module_id, SOURCE_BUDGET_CHARS)
    if not sources.strip():
        raise ValueError(f"no sources stored for module {module!r} - ingest first")

    parts = [
        f"CONCEPT: {module}",
        f"WHY YOU ARE LEARNING IT: {goal or 'to operate it on this server'}",
    ]
    if previous and gaps:
        parts.append(
            "Your previous notes scored below the comprehension threshold. "
            "The exam exposed these gaps - close them specifically, and keep "
            "what was already correct:\n- " + "\n- ".join(gaps)
        )
        parts.append(f"YOUR PREVIOUS NOTES:\n{_render(previous)}")
    parts.append(f"SOURCE MATERIAL:\n{sources}")
    parts.append("Produce the Concept Card now.")

    card = llm.json(SYSTEM, "\n\n".join(parts), CARD_SCHEMA, deep=True)
    card["_module"] = module
    db.set_module_card(conn, module_id, card)
    db.audit(
        conn,
        "distill.card",
        {
            "module": module,
            "facts": len(card.get("key_facts", [])),
            "commands": len(card.get("commands", [])),
            "open_questions": len(card.get("open_questions", [])),
        },
    )
    return card


def _render(card: dict[str, Any]) -> str:
    """Human/model readable form of a card."""
    out = [f"# {card.get('_module', 'concept')}", "", card.get("one_line", ""), ""]
    out += ["## Mental model", card.get("mental_model", ""), ""]

    if card.get("primitives"):
        out.append("## Primitives")
        out += [f"- {p['name']}: {p['purpose']}" for p in card["primitives"]]
        out.append("")
    if card.get("key_facts"):
        out.append("## Key facts")
        out += [f"- {f}" for f in card["key_facts"]]
        out.append("")
    if card.get("commands"):
        out.append("## Commands")
        out += [
            f"- `{c['command']}` [{c['risk']}] - {c['purpose']} "
            f"(requires: {c['preconditions']})"
            for c in card["commands"]
        ]
        out.append("")
    if card.get("invariants"):
        out.append("## Invariants")
        out += [f"- {i}" for i in card["invariants"]]
        out.append("")
    if card.get("failure_modes"):
        out.append("## Failure modes")
        out += [
            f"- {f['symptom']} <- {f['cause']} => {f['fix']}" for f in card["failure_modes"]
        ]
        out.append("")
    if card.get("verification"):
        out.append("## Verification")
        out += [f"- {v}" for v in card["verification"]]
        out.append("")
    if card.get("open_questions"):
        out.append("## Open questions")
        out += [f"- {q}" for q in card["open_questions"]]
    return "\n".join(out)


render_card = _render
