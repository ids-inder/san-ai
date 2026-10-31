"""Stage 3: the comprehension gate.

Three model calls with deliberately separated information:

  examiner  sees the SOURCES only      -> writes questions + reference answers
  student   sees the CONCEPT CARD only -> answers them closed-book
  grader    sees questions + reference + student answers, NOT the card

The isolation is the whole point. The student cannot see the sources it is
being tested on, so a card that dropped a load-bearing fact fails here instead
of failing in production. A module stays locked until it clears the threshold.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import db
from ..providers import LLM
from .distill import render_card

SOURCE_BUDGET_CHARS = 120_000

QUIZ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "question": {"type": "string"},
                    "reference_answer": {
                        "type": "string",
                        "description": "The correct answer, drawn strictly from the sources.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["recall", "mechanism", "procedure", "failure", "judgment"],
                    },
                    "weight": {
                        "type": "integer",
                        "description": "1 = nice to know, 2 = important, 3 = outage if wrong.",
                        "enum": [1, 2, 3],
                    },
                },
                "required": ["id", "question", "reference_answer", "kind", "weight"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

ANSWERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "answer": {"type": "string"},
                    "unsure": {
                        "type": "boolean",
                        "description": "True if your notes do not actually cover this.",
                    },
                },
                "required": ["id", "answer", "unsure"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}

GRADE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {
                        "type": "number",
                        "description": "0.0 wrong, 0.5 partially right, 1.0 fully correct.",
                    },
                    "verdict": {"type": "string"},
                },
                "required": ["id", "score", "verdict"],
                "additionalProperties": False,
            },
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific missing knowledge, phrased so it can be studied.",
        },
    },
    "required": ["results", "gaps"],
    "additionalProperties": False,
}

EXAMINER_SYSTEM = """You are setting an exam that decides whether an \
autonomous agent is allowed to operate this concept on a live production \
server. A pass means it runs real commands unsupervised.

Write questions ONLY answerable from the source material provided. For each, \
give the reference answer exactly as the sources support it.

Weight your questions by consequence:
- weight 3: getting this wrong causes an outage or data loss
- weight 2: getting this wrong causes a broken or insecure configuration
- weight 1: useful background

Cover a spread of kinds: recall (exact paths, flags, defaults), mechanism \
(why it behaves as it does), procedure (correct order of operations), failure \
(diagnosing a symptom), judgment (when NOT to act).

Target the things an agent would actually get wrong: ordering constraints, \
preconditions, irreversible steps, the difference between reload and restart. \
Do not ask trivia that has no operational consequence."""

STUDENT_SYSTEM = """You are San AI, sitting a closed-book exam on a concept \
you studied earlier.

You have ONLY your own notes. You do not have the source material. Answer from \
the notes alone.

If your notes do not actually cover a question, set unsure=true and say what \
you do not know. An honest "my notes do not cover this" is worth far more than \
a confident guess: a guess here becomes a wrong command on a production server \
later. You are not being judged on eloquence, only on whether your notes made \
you competent."""

GRADER_SYSTEM = """You are grading an exam that gates production access for an \
autonomous agent. Be strict and be fair.

Score each answer against the reference answer:
- 1.0 the answer is operationally correct; small wording differences are fine
- 0.5 directionally right but missing a detail that matters in practice
- 0.0 wrong, or so vague it would not guide a correct action

A candidate that admitted being unsure scores 0.0 for that question, but note \
it as a known gap rather than a misconception - the two need different \
remedies. A confidently wrong answer is the worst outcome: say so in the verdict.

In "gaps", write what must be studied next, specifically enough to go and \
learn it. Phrase each as the missing knowledge, not as the question."""


@dataclass
class ExamResult:
    score: float
    threshold: float
    detail: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold

    @property
    def weighted_total(self) -> int:
        return sum(int(d.get("weight", 1)) for d in self.detail)


def examine(
    llm: LLM,
    conn: sqlite3.Connection,
    module_id: int,
    module: str,
    card: dict[str, Any],
    *,
    quiz_size: int,
    threshold: float,
    rnd: int,
) -> ExamResult:
    """Run one full exam cycle and persist the result."""
    sources = db.source_text(conn, module_id, SOURCE_BUDGET_CHARS)
    if not sources.strip():
        raise ValueError(f"no sources stored for module {module!r}")

    # 1. examiner - sources only
    quiz = llm.json(
        EXAMINER_SYSTEM,
        f"CONCEPT: {module}\n\nWrite exactly {quiz_size} questions.\n\n"
        f"SOURCE MATERIAL:\n{sources}",
        QUIZ_SCHEMA,
        deep=True,
    )
    questions = quiz.get("questions", [])[:quiz_size]
    if not questions:
        raise ValueError("examiner produced no questions")

    # 2. student - card only, no sources, no reference answers
    asked = "\n".join(f"{q['id']}. {q['question']}" for q in questions)
    answers_doc = llm.json(
        STUDENT_SYSTEM,
        f"YOUR NOTES ON {module.upper()}:\n{render_card(card)}\n\n"
        f"EXAM QUESTIONS:\n{asked}\n\nAnswer every question by id.",
        ANSWERS_SCHEMA,
    )
    answers = {int(a["id"]): a for a in answers_doc.get("answers", [])}

    # 3. grader - questions, reference answers, student answers; never the card
    sheet = []
    for q in questions:
        a = answers.get(int(q["id"]), {})
        sheet.append(
            f"--- question {q['id']} (kind={q['kind']}, weight={q['weight']}) ---\n"
            f"Q: {q['question']}\n"
            f"REFERENCE: {q['reference_answer']}\n"
            f"CANDIDATE: {a.get('answer', '[no answer given]')}\n"
            f"CANDIDATE_SELF_REPORTED_UNSURE: {bool(a.get('unsure', False))}"
        )
    grades_doc = llm.json(
        GRADER_SYSTEM,
        f"CONCEPT: {module}\n\n" + "\n\n".join(sheet),
        GRADE_SCHEMA,
        deep=True,
    )

    detail, score = _combine(questions, answers, grades_doc.get("results", []))
    gaps = [g for g in grades_doc.get("gaps", []) if isinstance(g, str) and g.strip()]

    result = ExamResult(score=score, threshold=threshold, detail=detail, gaps=gaps)
    db.add_exam(conn, module_id, rnd, score, detail, gaps)
    db.audit(
        conn,
        "comprehend.exam",
        {
            "module": module,
            "round": rnd,
            "score": round(score, 4),
            "threshold": threshold,
            "passed": result.passed,
            "questions": len(detail),
            "gaps": len(gaps),
        },
    )
    return result


def _combine(
    questions: list[dict[str, Any]],
    answers: dict[int, dict[str, Any]],
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    """Weighted score. An ungraded question counts as zero, never as a pass."""
    by_id = {int(r["id"]): r for r in results if "id" in r}
    detail: list[dict[str, Any]] = []
    earned = 0.0
    possible = 0

    for q in questions:
        qid = int(q["id"])
        weight = int(q.get("weight", 1))
        graded = by_id.get(qid, {})
        raw = graded.get("score", 0.0)
        try:
            score = min(1.0, max(0.0, float(raw)))
        except (TypeError, ValueError):
            score = 0.0
        earned += score * weight
        possible += weight
        detail.append(
            {
                "id": qid,
                "question": q["question"],
                "kind": q.get("kind", "recall"),
                "weight": weight,
                "reference_answer": q["reference_answer"],
                "answer": answers.get(qid, {}).get("answer", ""),
                "unsure": bool(answers.get(qid, {}).get("unsure", False)),
                "score": score,
                "verdict": graded.get("verdict", "not graded"),
            }
        )

    return detail, (earned / possible if possible else 0.0)
