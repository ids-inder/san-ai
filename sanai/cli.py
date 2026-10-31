"""San AI command line."""
from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db
from .config import Autonomy, settings
from .engine import act, learn
from .engine.distill import render_card
from .executor import Executor
from .providers import Budget, build_llm
from .safety.policy import PolicyEngine

app = typer.Typer(
    add_completion=False,
    help="San AI - learns a concept, proves it understands it, then runs it.",
)
con = Console()
STATUS_COLOUR = {
    "mastered": "green", "learning": "yellow", "failed": "red",
    "demoted": "magenta", "new": "dim", "done": "green",
    "blocked": "yellow", "halted": "red", "awaiting_approval": "cyan",
    "running": "blue", "pending": "dim",
}


def _rig() -> tuple[Any, Any, PolicyEngine, Executor]:
    cfg = settings()
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    pol = PolicyEngine(cfg)
    return cfg, conn, pol, Executor(conn, cfg, pol)


def _event(stage: str, message: str) -> None:
    con.print(f"[dim]{stage:>10}[/dim]  {message}")


def _tint(value: str) -> str:
    return f"[{STATUS_COLOUR.get(value, 'white')}]{value}[/]"


# --------------------------------------------------------------------------
@app.command()
def init() -> None:
    """Create directories and the database, then report the configuration."""
    cfg, conn, pol, _ex = _rig()
    con.print(Panel.fit(
        f"database   {cfg.db_path}\n"
        f"workdir    {cfg.workdir}\n"
        f"provider   {cfg.provider} ({cfg.model})\n"
        f"autonomy   {cfg.autonomy.value}\n"
        f"guard      {'on' if cfg.integrity_guard else 'OFF'}\n"
        f"threshold  {cfg.mastery_threshold:.0%}\n"
        f"kill file  {pol.stop_file}",
        title="San AI initialised", border_style="green",
    ))
    if cfg.autonomy is Autonomy.FULL:
        con.print("[yellow]autonomy=full: commands run with nobody in the loop.[/]")
    if not cfg.integrity_guard:
        con.print("[red]integrity guard is OFF - nothing blocks host-destroying commands.[/]")


@app.command()
def doctor() -> None:
    """Check that San AI can actually run here."""
    cfg, conn, pol, _ex = _rig()
    rows = []

    rows.append(("database", "ok", str(cfg.db_path)))
    rows.append(("workdir", "ok" if cfg.workdir.is_dir() else "missing", str(cfg.workdir)))
    try:
        llm = build_llm(cfg)
        llm.text("Reply with the single word: ready", "ready?", max_tokens=16)
        rows.append(("brain", "ok", f"{llm.name} / {cfg.model}"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("brain", "FAIL", str(exc)[:120]))
    rows.append((
        "kill switch",
        "ENGAGED" if pol.stopped() else "clear",
        str(pol.stop_file),
    ))
    rows.append(("autonomy", cfg.autonomy.value, "guard on" if cfg.integrity_guard else "GUARD OFF"))

    t = Table("check", "state", "detail", box=None)
    for name, state, detail in rows:
        colour = "red" if state in ("FAIL", "missing", "ENGAGED", "GUARD OFF") else "green"
        t.add_row(name, f"[{colour}]{state}[/]", detail)
    con.print(t)
    if any(r[1] == "FAIL" for r in rows):
        raise typer.Exit(1)


@app.command(name="learn")
def learn_cmd(
    module: str = typer.Argument(..., help="The concept or module to learn."),
    goal: str = typer.Option("", "--goal", "-g", help="Why it is being learned."),
    url: list[str] = typer.Option([], "--url", "-u", help="Documentation URL (repeatable)."),
    file: list[str] = typer.Option([], "--file", "-f", help="Local file (repeatable)."),
    directory: list[str] = typer.Option([], "--dir", "-d", help="Directory to read (repeatable)."),
    note: list[str] = typer.Option([], "--note", "-n", help="Your own explanation (repeatable)."),
    probe: bool = typer.Option(True, "--probe/--no-probe", help="Read local man pages and --help."),
    budget: float = typer.Option(0.0, "--budget", help="Spend ceiling in USD (0 = none)."),
) -> None:
    """Study a concept until the comprehension gate is cleared."""
    cfg, conn, _pol, ex = _rig()
    llm = build_llm(cfg)
    if budget > 0:
        llm.budget = Budget(max_usd=budget)

    con.print(f"[bold]learning[/] {module}")
    try:
        out = learn.learn(
            llm, conn, cfg, ex, module, goal=goal,
            urls=list(url), files=list(file), dirs=list(directory), notes=list(note),
            probe_local=probe, on_event=_event,
        )
    except Exception as exc:  # noqa: BLE001
        con.print(f"[red]learning failed:[/] {exc}")
        raise typer.Exit(1) from exc

    colour = "green" if out.mastered else "red"
    body = (
        f"score      {out.score:.0%} (threshold {cfg.mastery_threshold:.0%})\n"
        f"rounds     {out.rounds}\n"
        f"history    {' -> '.join(f'{s:.0%}' for s in out.history) or 'n/a'}\n"
        f"cost       ${llm.usage.cost_usd:.3f} over {llm.usage.calls} calls"
    )
    con.print(Panel.fit(body, title=f"{module}: {out.status}", border_style=colour))

    if out.gaps:
        con.print("\n[yellow]still missing:[/]")
        for g in out.gaps:
            con.print(f"  - {g}")
        if not out.mastered:
            con.print(
                "\nGive it better material and run again:\n"
                f'  sanai learn "{module}" --url <docs> --file <config> --note "..."'
            )
    if out.mastered:
        con.print(f'\nNow try:  sanai do "<task involving {module}>"')


@app.command(name="do")
def do_cmd(
    request: str = typer.Argument(..., help="What San AI should do."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; run nothing."),
    allow_unlearned: bool = typer.Option(
        False, "--allow-unlearned",
        help="Act without a mastered module. Removes the comprehension gate.",
    ),
    budget: float = typer.Option(0.0, "--budget", help="Spend ceiling in USD (0 = none)."),
) -> None:
    """Carry out a task using mastered knowledge."""
    cfg, conn, pol, ex = _rig()
    if pol.stopped():
        con.print(f"[red]kill switch engaged[/] - remove {pol.stop_file} first")
        raise typer.Exit(1)

    llm = build_llm(cfg)
    if budget > 0:
        llm.budget = Budget(max_usd=budget)

    con.print(f"[bold]task[/] {request}")
    out = act.run_task(
        llm, conn, cfg, ex, request,
        require_mastery=not allow_unlearned, dry_run=dry_run, on_event=_event,
    )

    if out.plan:
        _print_plan(out.plan)
    colour = {"done": "green", "blocked": "yellow", "halted": "red"}.get(out.status, "red")
    con.print(Panel.fit(
        f"{out.summary}\n\n"
        f"task id    {out.task_id}\n"
        f"modules    {', '.join(out.modules) or 'none'}\n"
        f"commands   {sum(1 for r in out.results if r.ran)} run, "
        f"{sum(1 for r in out.results if not r.ran)} refused\n"
        f"cost       ${llm.usage.cost_usd:.3f} over {llm.usage.calls} calls",
        title=f"task {out.status}", border_style=colour,
    ))
    if out.reflection.get("demoted"):
        con.print(f"[magenta]demoted (notes were wrong):[/] {', '.join(out.reflection['demoted'])}")
    if out.status == "blocked" and out.pending:
        con.print(f"Resume after review with:  sanai approve {out.task_id}")
    if out.status not in ("done", "blocked"):
        raise typer.Exit(1)


def _print_plan(plan: dict[str, Any]) -> None:
    con.print(f"\n[bold]plan:[/] {plan.get('understanding', '')}")
    steps = plan.get("steps", [])
    if not steps:
        return
    t = Table("#", "risk", "command", "rollback", box=None)
    risk_colour = {"read-only": "green", "mutating": "yellow", "destructive": "red"}
    for s in steps:
        t.add_row(
            str(s.get("n", "")),
            f"[{risk_colour.get(s.get('risk', ''), 'white')}]{s.get('risk', '')}[/]",
            s.get("command", ""),
            s.get("rollback", ""),
        )
    con.print(t)


@app.command()
def approve(task_id: int = typer.Argument(..., help="Task awaiting approval.")) -> None:
    """Run the remaining steps of a task that stopped for approval."""
    cfg, conn, pol, ex = _rig()
    row = db.get_task(conn, task_id)
    if not row:
        con.print(f"[red]no such task:[/] {task_id}")
        raise typer.Exit(1)
    plan = json.loads(row["plan_json"] or "{}")
    pending = plan.get("_pending", [])
    if not pending:
        con.print(f"task {task_id} has nothing pending (status: {row['status']})")
        raise typer.Exit(0)

    _print_plan({"understanding": "pending steps", "steps": pending})
    if not typer.confirm(f"Run these {len(pending)} step(s)?"):
        db.update_task(conn, task_id, status="rejected", result="rejected by operator")
        db.audit(conn, "task.rejected", {"task_id": task_id})
        con.print("rejected")
        raise typer.Exit(0)

    # The operator just became the approval, so these steps may run.
    cfg.autonomy = Autonomy.FULL
    ok = True
    for i, step in enumerate(pending, start=1):
        cmd = step.get("command", "")
        _event("step", cmd)
        res = ex.run(cmd, task_id=task_id, step=2000 + i,
                     rationale="operator-approved step")
        if not res.ok:
            con.print(f"[red]step failed[/] (exit {res.exit_code})")
            con.print(res.summary(1200))
            ok = False
            break
    status = "done" if ok else "failed"
    db.update_task(conn, task_id, status=status,
                   plan_json={**plan, "_pending": []},
                   result=f"operator-approved run: {status}")
    db.audit(conn, "task.approved_run", {"task_id": task_id, "status": status})
    con.print(f"task {task_id}: {_tint(status)}")


@app.command()
def modules() -> None:
    """List everything San AI has studied."""
    _cfg, conn, _pol, _ex = _rig()
    rows = db.list_modules(conn)
    if not rows:
        con.print("nothing learned yet - try:  sanai learn \"nginx reverse proxy\"")
        return
    t = Table("module", "status", "mastery", "rounds", "goal", box=None)
    for r in rows:
        t.add_row(r["name"], _tint(r["status"]), f"{r['mastery']:.0%}",
                  str(r["rounds"]), (r["goal"] or "")[:48])
    con.print(t)


@app.command()
def show(module: str = typer.Argument(..., help="Module name.")) -> None:
    """Print a module's notes and its last exam."""
    _cfg, conn, _pol, _ex = _rig()
    row = db.get_module(conn, module)
    if not row:
        con.print(f"[red]not learned:[/] {module}")
        raise typer.Exit(1)
    card = json.loads(row["card_json"] or "{}")
    card.setdefault("_module", module)
    con.print(Panel(render_card(card), title=f"{module} ({_tint(row['status'])})"))

    exam = db.latest_exam(conn, int(row["id"]))
    if not exam:
        return
    con.print(f"\n[bold]last exam[/] round {exam['round']}, scored {exam['score']:.0%}")
    t = Table("w", "score", "question", "verdict", box=None)
    for d in json.loads(exam["detail_json"] or "[]"):
        colour = "green" if d["score"] >= 1 else "yellow" if d["score"] > 0 else "red"
        t.add_row(str(d.get("weight", 1)), f"[{colour}]{d['score']:.1f}[/]",
                  d["question"][:60], (d.get("verdict") or "")[:40])
    con.print(t)
    for g in json.loads(exam["gaps_json"] or "[]"):
        con.print(f"  [yellow]gap:[/] {g}")


@app.command()
def forget(
    module: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Delete a module, its sources and its exams."""
    _cfg, conn, _pol, _ex = _rig()
    row = db.get_module(conn, module)
    if not row:
        con.print(f"[red]not learned:[/] {module}")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Delete everything San AI knows about {module!r}?"):
        raise typer.Exit(0)
    conn.execute("DELETE FROM chunk WHERE module_name = ?", (module,))
    conn.execute("DELETE FROM module WHERE id = ?", (int(row["id"]),))
    db.audit(conn, "module.forgotten", {"module": module})
    con.print(f"forgot {module}")


@app.command()
def tasks(limit: int = typer.Option(20, "--limit", "-l")) -> None:
    """Recent tasks."""
    _cfg, conn, _pol, _ex = _rig()
    rows = db.list_tasks(conn, limit)
    if not rows:
        con.print("no tasks yet")
        return
    t = Table("id", "status", "request", "result", box=None)
    for r in rows:
        t.add_row(str(r["id"]), _tint(r["status"]),
                  r["request"][:46], (r["result"] or "")[:46])
    con.print(t)


@app.command()
def task(task_id: int = typer.Argument(...)) -> None:
    """Everything that happened in one task."""
    _cfg, conn, _pol, _ex = _rig()
    row = db.get_task(conn, task_id)
    if not row:
        con.print(f"[red]no such task:[/] {task_id}")
        raise typer.Exit(1)
    con.print(Panel.fit(
        f"{row['request']}\n\nstatus  {_tint(row['status'])}\nresult  {row['result']}",
        title=f"task {task_id}",
    ))
    plan = json.loads(row["plan_json"] or "{}")
    if plan:
        _print_plan(plan)
    for r in db.runs_for_task(conn, task_id):
        if r["decision"] != "allow":
            con.print(f"\n[yellow]$ {r['command']}[/]\n  {r['decision']}: {r['reason']}")
            continue
        colour = "green" if r["exit_code"] == 0 else "red"
        con.print(f"\n[{colour}]$ {r['command']}[/]  (exit {r['exit_code']}, {r['duration']:.1f}s)")
        for stream in ("stdout", "stderr"):
            if r[stream]:
                con.print(f"  {r[stream][:1500]}")


@app.command()
def audit(limit: int = typer.Option(40, "--limit", "-l")) -> None:
    """Tail the append-only audit log."""
    _cfg, conn, _pol, _ex = _rig()
    import datetime as _dt

    t = Table("when", "event", "detail", box=None)
    for r in reversed(db.recent_audit(conn, limit)):
        when = _dt.datetime.fromtimestamp(r["created_at"]).strftime("%m-%d %H:%M:%S")
        t.add_row(when, r["event"], (r["payload"] or "")[:92])
    con.print(t)


@app.command()
def stop(note: str = typer.Option("", "--note", help="Why.")) -> None:
    """Engage the kill switch: refuse every command until resumed."""
    cfg, conn, pol, _ex = _rig()
    pol.engage_stop(note or "stopped by operator")
    db.audit(conn, "killswitch.engaged", {"note": note})
    con.print(f"[red]STOPPED[/] - no command will run while {pol.stop_file} exists")


@app.command()
def resume() -> None:
    """Release the kill switch."""
    cfg, conn, pol, _ex = _rig()
    pol.release_stop()
    db.audit(conn, "killswitch.released", {})
    con.print("[green]resumed[/]")


@app.command()
def serve(
    host: str = typer.Option("", "--host"),
    port: int = typer.Option(0, "--port"),
) -> None:
    """Run the web dashboard."""
    cfg = settings()
    cfg.ensure_dirs()
    import uvicorn

    from .web.app import create_app

    if not cfg.web_token:
        con.print("[red]SANAI_WEB_TOKEN is unset[/] - refusing to serve without a token")
        raise typer.Exit(1)
    uvicorn.run(
        create_app(), host=host or cfg.web_host, port=port or cfg.web_port, log_level="info"
    )


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        con.print("\ninterrupted")
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
