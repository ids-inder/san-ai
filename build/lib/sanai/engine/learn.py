"""The learning loop: ingest -> distill -> examine -> (re-study) -> mastered.

A module that cannot clear the comprehension threshold within max_learn_rounds
is marked `failed` and reports what material it still needs. It does not get
execution rights on a near miss.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import db
from ..config import Settings
from ..executor import Executor
from ..providers import LLM
from . import comprehend, distill
from .ingest import Ingestor, collect

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "how", "what", "when",
    "which", "does", "not", "its", "are", "was", "has", "have", "you", "your",
    "knowledge", "missing", "understanding", "specific", "detail", "details",
    "about", "between", "difference", "should", "would", "must", "need",
    "explain", "explains", "interacts", "notes", "note", "cover", "covers",
    "lacks", "unclear", "exact", "whether", "under", "after", "before",
    "there", "their", "them", "also", "only", "same", "than", "then",
    "command", "commands", "option", "options", "value", "values", "used",
}


@dataclass
class LearnOutcome:
    module: str
    status: str                      # mastered | failed
    score: float
    rounds: int
    gaps: list[str] = field(default_factory=list)
    card: dict[str, Any] = field(default_factory=dict)
    history: list[float] = field(default_factory=list)

    @property
    def mastered(self) -> bool:
        return self.status == "mastered"


def learn(
    llm: LLM,
    conn: sqlite3.Connection,
    cfg: Settings,
    executor: Executor,
    module: str,
    *,
    goal: str = "",
    urls: list[str] | None = None,
    files: list[str] | None = None,
    dirs: list[str] | None = None,
    notes: list[str] | None = None,
    probe_local: bool = True,
    on_event: Any = None,
) -> LearnOutcome:
    """Study `module` until understood, or until the round budget runs out."""
    say = on_event or (lambda *_a, **_k: None)

    module_id = db.upsert_module(conn, module, goal)
    db.set_module_status(conn, module_id, "learning")
    db.audit(conn, "learn.start", {"module": module, "goal": goal})

    ingestor = Ingestor(conn, cfg, executor)
    say("ingest", f"gathering material on {module}")
    stored = collect(
        ingestor,
        module_id,
        module,
        topic=module,
        urls=urls,
        files=files,
        dirs=dirs,
        notes=notes,
        probe_local=probe_local,
    )
    say("ingest", f"{stored} source(s) stored")

    if not db.source_text(conn, module_id, 2000).strip():
        db.set_module_status(conn, module_id, "failed", 0.0)
        db.audit(conn, "learn.no_sources", {"module": module})
        return LearnOutcome(
            module=module,
            status="failed",
            score=0.0,
            rounds=0,
            gaps=[
                "No source material could be gathered. Supply documentation "
                "with --url, --file, --dir or --note."
            ],
        )

    card: dict[str, Any] = {}
    gaps: list[str] = []
    history: list[float] = []
    score = 0.0

    for rnd in range(1, cfg.max_learn_rounds + 1):
        db.bump_rounds(conn, module_id)

        say("distill", f"round {rnd}: writing concept card")
        card = distill.build_card(
            llm,
            conn,
            module_id,
            module,
            goal,
            gaps=gaps or None,
            previous=card or None,
        )

        say("exam", f"round {rnd}: closed-book exam ({cfg.quiz_size} questions)")
        result = comprehend.examine(
            llm,
            conn,
            module_id,
            module,
            card,
            quiz_size=cfg.quiz_size,
            threshold=cfg.mastery_threshold,
            rnd=rnd,
        )
        score = result.score
        gaps = result.gaps
        history.append(score)
        say(
            "exam",
            f"round {rnd}: scored {score:.0%} "
            f"(need {cfg.mastery_threshold:.0%}) - "
            + ("PASS" if result.passed else f"{len(gaps)} gap(s)"),
        )

        if result.passed:
            db.set_module_status(conn, module_id, "mastered", score)
            db.audit(
                conn,
                "learn.mastered",
                {"module": module, "score": round(score, 4), "rounds": rnd},
            )
            return LearnOutcome(
                module=module,
                status="mastered",
                score=score,
                rounds=rnd,
                gaps=gaps,
                card=card,
                history=history,
            )

        if rnd < cfg.max_learn_rounds:
            extra = _study_gaps(ingestor, module_id, module, gaps)
            say("ingest", f"pulled {extra} extra source(s) targeting the gaps")

    db.set_module_status(conn, module_id, "failed", score)
    db.audit(
        conn,
        "learn.failed",
        {
            "module": module,
            "score": round(score, 4),
            "rounds": cfg.max_learn_rounds,
            "gaps": gaps,
        },
    )
    return LearnOutcome(
        module=module,
        status="failed",
        score=score,
        rounds=cfg.max_learn_rounds,
        gaps=gaps,
        card=card,
        history=history,
    )


def _study_gaps(
    ingestor: Ingestor, module_id: int, module: str, gaps: list[str]
) -> int:
    """Try to pull local documentation that speaks to the exposed gaps."""
    terms = _gap_terms(gaps)
    stored = 0
    for term in terms[:4]:
        for cmd in (f"man -P cat {term}", f"{term} --help"):
            if ingestor.from_command(module_id, module, cmd):
                stored += 1
    if terms:
        pattern = "|".join(re.escape(t) for t in terms[:6])
        if ingestor.from_command(
            module_id,
            module,
            f"grep -rIl -E '{pattern}' /usr/share/doc /etc 2>/dev/null | head -20",
        ):
            stored += 1
    return stored


def _gap_terms(gaps: list[str]) -> list[str]:
    """Pull plausible command/config identifiers out of gap descriptions.

    Ranked, because the first few terms are all that get looked up: an
    explicitly backticked name beats an identifier-shaped word, which beats a
    bare English word that merely survived the stopword list.
    """
    scored: dict[str, int] = {}
    for gap in gaps:
        lowered = gap.lower()
        quoted = {
            re.sub(r"[^a-z0-9_.-]", "", t.split()[0])
            for t in re.findall(r"`([^`]+)`", lowered)
            if t.split()
        }
        for tok in re.findall(r"\b([a-z][a-z0-9_.-]{3,})\b", lowered):
            tok = tok.strip(".-")
            if len(tok) < 4 or tok in STOPWORDS:
                continue
            if tok in quoted:
                rank = 3
            elif any(ch in tok for ch in "_.-"):
                rank = 2
            else:
                rank = 1
            scored[tok] = max(scored.get(tok, 0), rank)

    return sorted(scored, key=lambda t: (-scored[t], t))
