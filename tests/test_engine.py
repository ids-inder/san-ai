"""End-to-end engine tests using a stub brain - no API calls, real commands."""
from __future__ import annotations

from typing import Any

import pytest

from sanai import db
from sanai.config import Autonomy, Settings
from sanai.engine import act, learn
from sanai.executor import Executor
from sanai.providers.base import LLM
from sanai.safety.policy import PolicyEngine


class StubLLM(LLM):
    """Answers by looking at which schema was asked for."""

    name = "stub"

    def __init__(self, *, exam_scores: list[float] | None = None, blocked: bool = False,
                 fail_step: bool = False) -> None:
        super().__init__()
        self.exam_scores = exam_scores or [1.0]
        self.exam_calls = 0
        self.blocked = blocked
        self.fail_step = fail_step
        self.seen: list[str] = []

    def text(self, system, user, *, deep=False, max_tokens=None):  # noqa: D102
        return "ok"

    def json(self, system, user, schema, *, deep=False, max_tokens=None):  # noqa: D102
        req = set(schema.get("required", []))
        self.usage.add("stub", input_tokens=10, output_tokens=5)

        if "one_line" in req:                       # concept card
            self.seen.append("distill")
            return {
                "one_line": "A test concept.",
                "mental_model": "It works by testing.",
                "primitives": [{"name": "p", "purpose": "to be a primitive"}],
                "key_facts": ["facts live here"],
                "commands": [
                    {"command": "echo hi", "purpose": "greet", "preconditions": "none",
                     "risk": "read-only"}
                ],
                "invariants": ["echo must print"],
                "failure_modes": [{"symptom": "silence", "cause": "no tty", "fix": "use echo"}],
                "verification": ["echo verified"],
                "open_questions": [],
            }

        if req == {"questions"}:                    # examiner
            self.seen.append("exam.questions")
            return {
                "questions": [
                    {"id": 1, "question": "What is it?", "reference_answer": "a test concept",
                     "kind": "recall", "weight": 3},
                    {"id": 2, "question": "How does it work?", "reference_answer": "by testing",
                     "kind": "mechanism", "weight": 1},
                ]
            }

        if req == {"answers"}:                      # student
            self.seen.append("exam.answers")
            # The student must not be shown the sources or reference answers.
            assert "SOURCE MATERIAL" not in user
            assert "REFERENCE" not in user
            return {"answers": [
                {"id": 1, "answer": "a test concept", "unsure": False},
                {"id": 2, "answer": "by testing", "unsure": False},
            ]}

        if req == {"results", "gaps"}:              # grader
            self.seen.append("exam.grade")
            # The grader must not be shown the card.
            assert "Mental model" not in user
            score = self.exam_scores[min(self.exam_calls, len(self.exam_scores) - 1)]
            self.exam_calls += 1
            gaps = [] if score >= 0.85 else ["needs `proxy_timeout` detail"]
            return {
                "results": [{"id": 1, "score": score, "verdict": "v"},
                            {"id": 2, "score": score, "verdict": "v"}],
                "gaps": gaps,
            }

        if "understanding" in req:                  # planner
            self.seen.append("plan")
            if self.blocked:
                return {"understanding": "u", "blocked": True,
                        "blocked_reason": "my notes do not cover this",
                        "preflight": [], "steps": [], "final_verification": []}
            cmd = "exit 3" if self.fail_step else "echo step-ran"
            return {
                "understanding": "run a harmless command",
                "blocked": False, "blocked_reason": "",
                "preflight": ["echo preflight-ok"],
                "steps": [{"n": 1, "command": cmd, "rationale": "r", "risk": "read-only",
                           "expect": "output", "rollback": "none"}],
                "final_verification": ["echo verified"],
            }

        if "recoverable" in req:                    # diagnoser
            self.seen.append("diagnose")
            return {"analysis": "the command exited non-zero",
                    "recoverable": False,
                    "abandon_reason": "cannot fix from current knowledge",
                    "next_steps": []}

        if "lessons" in req:                        # reflection
            self.seen.append("reflect")
            return {"outcome": "success", "summary": "went fine", "lessons": []}

        raise AssertionError(f"unexpected schema: {sorted(req)}")


@pytest.fixture()
def rig(tmp_path):
    cfg = Settings(
        db_path=tmp_path / "sanai.db",
        workdir=tmp_path / "work",
        autonomy=Autonomy.FULL,
        integrity_guard=True,
        quiz_size=2,
        max_learn_rounds=3,
        mastery_threshold=0.85,
        _env_file=None,
    )
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    pol = PolicyEngine(cfg)
    ex = Executor(conn, cfg, pol)
    return cfg, conn, pol, ex


def test_learn_masters_a_module_when_it_passes_the_exam(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[1.0])
    out = learn.learn(llm, conn, cfg, ex, "testconcept",
                      goal="prove the loop", notes=["A test concept is used for testing. " * 5],
                      probe_local=False)
    assert out.mastered and out.score == 1.0 and out.rounds == 1
    row = db.get_module(conn, "testconcept")
    assert row["status"] == "mastered"
    assert llm.seen.count("exam.answers") == 1


def test_learn_restudies_then_passes(rig):
    """A near miss re-studies instead of unlocking."""
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[0.5, 1.0])
    out = learn.learn(llm, conn, cfg, ex, "slowconcept",
                      notes=["Material about the slow concept. " * 5], probe_local=False)
    assert out.mastered and out.rounds == 2
    assert llm.seen.count("distill") == 2, "should re-distill against the gaps"


def test_learn_fails_closed_after_round_budget(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[0.4])
    out = learn.learn(llm, conn, cfg, ex, "hardconcept",
                      notes=["Material about a hard concept. " * 5], probe_local=False)
    assert not out.mastered and out.status == "failed"
    assert out.rounds == cfg.max_learn_rounds
    assert db.get_module(conn, "hardconcept")["status"] == "failed"
    assert out.gaps, "a failed module must report what it still needs"


def test_learn_without_sources_fails_rather_than_inventing(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM()
    out = learn.learn(llm, conn, cfg, ex, "voidconcept", probe_local=False)
    assert out.status == "failed" and out.rounds == 0
    assert "No source material" in out.gaps[0]
    assert "distill" not in llm.seen, "must not distill from nothing"


def test_task_is_refused_when_no_module_covers_it(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM()
    out = act.run_task(llm, conn, cfg, ex, "do something about kubernetes")
    assert out.status == "blocked"
    assert "No mastered module" in out.summary
    assert "plan" not in llm.seen, "must not plan without knowledge"


def test_mastered_module_executes_and_verifies(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[1.0])
    learn.learn(llm, conn, cfg, ex, "echoing",
                notes=["Echoing prints text to stdout. " * 5], probe_local=False)

    out = act.run_task(llm, conn, cfg, ex, "use echoing to print something")
    assert out.status == "done", out.summary
    assert "echoing" in out.modules
    ran = [r for r in out.results if r.ran]
    assert any("step-ran" in r.stdout for r in ran)
    assert any("verified" in r.stdout for r in ran)
    assert any("preflight-ok" in r.stdout for r in ran)
    assert "reflect" in llm.seen


def test_planner_can_refuse_on_insufficient_knowledge(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[1.0], blocked=True)
    learn.learn(llm, conn, cfg, ex, "thing",
                notes=["A thing that does things. " * 5], probe_local=False)
    out = act.run_task(llm, conn, cfg, ex, "do a thing")
    assert out.status == "blocked"
    assert "do not cover" in out.summary


def test_unrecoverable_failure_stops_instead_of_thrashing(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[1.0], fail_step=True)
    learn.learn(llm, conn, cfg, ex, "failing",
                notes=["A failing concept exits non-zero. " * 5], probe_local=False)
    out = act.run_task(llm, conn, cfg, ex, "use failing")
    assert out.status == "failed"
    assert "cannot fix" in out.summary
    assert llm.seen.count("diagnose") == 1, "must not retry in a loop"


def test_kill_switch_halts_a_running_task(rig):
    cfg, conn, pol, ex = rig
    llm = StubLLM(exam_scores=[1.0])
    learn.learn(llm, conn, cfg, ex, "haltable",
                notes=["A haltable concept. " * 5], probe_local=False)
    pol.engage_stop("operator halt")
    out = act.run_task(llm, conn, cfg, ex, "use haltable")
    assert out.status == "halted" and "kill switch" in out.summary
    assert not any(r.ran for r in out.results)


def test_every_command_is_audited(rig):
    cfg, conn, _pol, ex = rig
    llm = StubLLM(exam_scores=[1.0])
    learn.learn(llm, conn, cfg, ex, "audited",
                notes=["An audited concept. " * 5], probe_local=False)
    act.run_task(llm, conn, cfg, ex, "use audited")
    events = [r["event"] for r in db.recent_audit(conn, 200)]
    assert "command.ran" in events
    assert "task.finished" in events
    assert "learn.mastered" in events
    runs = db.recent_runs(conn, 100)
    assert runs and all(r["decision"] for r in runs)
