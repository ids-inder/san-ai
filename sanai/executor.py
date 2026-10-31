"""The one place San AI runs anything.

Every command - whether it comes from a plan, a verification step, or the
ingest stage reading a man page - passes through Executor.run. That makes the
policy check and the audit record impossible to bypass by accident.
"""
from __future__ import annotations

import shlex
import sqlite3
import subprocess
import time
from dataclasses import dataclass

from . import db
from .config import Settings
from .safety.policy import Decision, PolicyEngine

MAX_CAPTURE = 24_000  # chars kept per stream; the model never needs more


@dataclass
class Result:
    command: str
    decision: Decision
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    run_id: int | None = None

    @property
    def ran(self) -> bool:
        return self.exit_code is not None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def summary(self, limit: int = 4000) -> str:
        """Compact form fed back to the model."""
        if not self.ran:
            return f"NOT RUN ({self.decision.action}): {self.decision.reason}"
        head = f"exit={self.exit_code} in {self.duration:.1f}s"
        out = (self.stdout or "").strip()
        err = (self.stderr or "").strip()
        body = ""
        if out:
            body += f"\n--- stdout ---\n{_tail(out, limit)}"
        if err:
            body += f"\n--- stderr ---\n{_tail(err, limit // 2)}"
        return head + (body or "\n(no output)")


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"[...{len(text) - limit} chars trimmed...]\n{text[-limit:]}"


class Executor:
    def __init__(self, conn: sqlite3.Connection, cfg: Settings, policy: PolicyEngine) -> None:
        self.conn = conn
        self.cfg = cfg
        self.policy = policy

    def run(
        self,
        command: str,
        *,
        task_id: int | None = None,
        module_id: int | None = None,
        step: int = 0,
        rationale: str = "",
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> Result:
        decision = self.policy.evaluate(command)

        if not decision.allowed:
            res = Result(command, decision, None, "", "", 0.0)
            res.run_id = self._record(res, task_id, module_id, step, rationale)
            db.audit(
                self.conn,
                "command.refused",
                {
                    "command": command,
                    "action": decision.action,
                    "reason": decision.reason,
                    "task_id": task_id,
                },
            )
            return res

        started = time.monotonic()
        argv = self._wrap(command)
        try:
            proc = subprocess.run(  # noqa: S603 - running shell commands is the product
                argv,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or self.cfg.cmd_timeout,
                cwd=cwd or str(self.cfg.workdir),
            )
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code = 124
            out = _decode(exc.stdout)
            err = (_decode(exc.stderr) + f"\n[timed out after {timeout or self.cfg.cmd_timeout}s]").strip()
        except FileNotFoundError as exc:
            code, out, err = 127, "", str(exc)

        res = Result(
            command=command,
            decision=decision,
            exit_code=code,
            stdout=out[:MAX_CAPTURE],
            stderr=err[:MAX_CAPTURE],
            duration=time.monotonic() - started,
        )
        res.run_id = self._record(res, task_id, module_id, step, rationale)
        db.audit(
            self.conn,
            "command.ran",
            {
                "command": command,
                "exit_code": code,
                "duration": round(res.duration, 3),
                "task_id": task_id,
            },
        )
        return res

    def _wrap(self, command: str) -> list[str]:
        """Build the argv. A login shell so PATH and profile behave normally."""
        if self.cfg.shell_user:
            return ["sudo", "-n", "-u", self.cfg.shell_user, "bash", "-lc", command]
        return ["bash", "-lc", command]

    def _record(
        self,
        res: Result,
        task_id: int | None,
        module_id: int | None,
        step: int,
        rationale: str,
    ) -> int:
        return db.add_run(
            self.conn,
            task_id=task_id,
            module_id=module_id,
            step=step,
            command=res.command,
            rationale=rationale,
            decision=res.decision.action,
            reason=res.decision.reason,
            exit_code=res.exit_code,
            stdout=res.stdout,
            stderr=res.stderr,
            duration=res.duration,
        )


def _decode(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def quote(path: str) -> str:
    return shlex.quote(path)
