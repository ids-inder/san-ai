"""SQLite store: knowledge, mastery, execution history, audit log.

Retrieval uses FTS5 BM25, which ships with CPython's sqlite3 - no embedding
model, no vector service, works offline.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- A concept or module San AI has been asked to learn.
CREATE TABLE IF NOT EXISTS module (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    goal          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new',  -- new|learning|mastered|demoted|failed
    mastery       REAL NOT NULL DEFAULT 0.0,
    rounds        INTEGER NOT NULL DEFAULT 0,
    card_json     TEXT NOT NULL DEFAULT '{}',   -- the ConceptCard
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

-- Raw source material gathered for a module.
CREATE TABLE IF NOT EXISTS source (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,        -- url|file|command|note|repo
    origin      TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- Searchable chunks of source material (BM25 retrieval).
CREATE VIRTUAL TABLE IF NOT EXISTS chunk USING fts5(
    module_name, origin, body, tokenize='porter unicode61'
);

-- One comprehension exam = the gate between learning and executing.
CREATE TABLE IF NOT EXISTS exam (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    round       INTEGER NOT NULL,
    score       REAL NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '[]',
    gaps_json   TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL
);

-- A task San AI was asked to carry out.
CREATE TABLE IF NOT EXISTS task (
    id           INTEGER PRIMARY KEY,
    request      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
      -- pending|awaiting_approval|running|done|failed|blocked|rejected
    modules_json TEXT NOT NULL DEFAULT '[]',
    plan_json    TEXT NOT NULL DEFAULT '{}',
    result       TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

-- Every command San AI ran or was stopped from running.
CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER REFERENCES task(id) ON DELETE CASCADE,
    module_id   INTEGER REFERENCES module(id) ON DELETE SET NULL,
    step        INTEGER NOT NULL DEFAULT 0,
    command     TEXT NOT NULL,
    rationale   TEXT NOT NULL DEFAULT '',
    decision    TEXT NOT NULL,     -- allow|deny|queued
    reason      TEXT NOT NULL DEFAULT '',
    exit_code   INTEGER,
    stdout      TEXT NOT NULL DEFAULT '',
    stderr      TEXT NOT NULL DEFAULT '',
    duration    REAL NOT NULL DEFAULT 0.0,
    created_at  REAL NOT NULL
);

-- What San AI learned from actually running things.
CREATE TABLE IF NOT EXISTS experience (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    task_id     INTEGER REFERENCES task(id) ON DELETE SET NULL,
    outcome     TEXT NOT NULL,     -- success|failure
    lesson      TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- Append-only audit trail. Never updated, never deleted by the agent.
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY,
    event       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_task  ON run(task_id);
CREATE INDEX IF NOT EXISTS idx_src_mod   ON source(module_id);
CREATE INDEX IF NOT EXISTS idx_exam_mod  ON exam(module_id);
CREATE INDEX IF NOT EXISTS idx_exp_mod   ON experience(module_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------
# module helpers
# --------------------------------------------------------------------------
def upsert_module(conn: sqlite3.Connection, name: str, goal: str = "") -> int:
    row = conn.execute("SELECT id FROM module WHERE name = ?", (name,)).fetchone()
    if row:
        if goal:
            conn.execute(
                "UPDATE module SET goal = ?, updated_at = ? WHERE id = ?",
                (goal, now(), row["id"]),
            )
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO module (name, goal, status, created_at, updated_at) "
        "VALUES (?, ?, 'new', ?, ?)",
        (name, goal, now(), now()),
    )
    return int(cur.lastrowid)


def get_module(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM module WHERE name = ?", (name,)).fetchone()


def list_modules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM module ORDER BY mastery DESC, name ASC").fetchall()
    )


def set_module_card(
    conn: sqlite3.Connection, module_id: int, card: dict[str, Any]
) -> None:
    conn.execute(
        "UPDATE module SET card_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(card, ensure_ascii=False), now(), module_id),
    )


def set_module_status(
    conn: sqlite3.Connection,
    module_id: int,
    status: str,
    mastery: float | None = None,
) -> None:
    if mastery is None:
        conn.execute(
            "UPDATE module SET status = ?, updated_at = ? WHERE id = ?",
            (status, now(), module_id),
        )
    else:
        conn.execute(
            "UPDATE module SET status = ?, mastery = ?, updated_at = ? WHERE id = ?",
            (status, round(mastery, 4), now(), module_id),
        )


def bump_rounds(conn: sqlite3.Connection, module_id: int) -> int:
    conn.execute(
        "UPDATE module SET rounds = rounds + 1, updated_at = ? WHERE id = ?",
        (now(), module_id),
    )
    row = conn.execute("SELECT rounds FROM module WHERE id = ?", (module_id,)).fetchone()
    return int(row["rounds"]) if row else 0


# --------------------------------------------------------------------------
# sources + chunks
# --------------------------------------------------------------------------
def add_source(
    conn: sqlite3.Connection,
    module_id: int,
    module_name: str,
    kind: str,
    origin: str,
    content: str,
    chunk_chars: int = 1800,
) -> int:
    cur = conn.execute(
        "INSERT INTO source (module_id, kind, origin, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (module_id, kind, origin, content, now()),
    )
    for body in _chunks(content, chunk_chars):
        conn.execute(
            "INSERT INTO chunk (module_name, origin, body) VALUES (?, ?, ?)",
            (module_name, origin, body),
        )
    return int(cur.lastrowid)


def _chunks(text: str, size: int) -> Iterable[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 > size and buf:
            yield buf
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        yield buf


def search_chunks(
    conn: sqlite3.Connection, query: str, limit: int = 8, module_name: str | None = None
) -> list[sqlite3.Row]:
    """BM25 search. Returns [] rather than raising on an unparseable query."""
    match = _fts_query(query)
    if not match:
        return []
    sql = (
        "SELECT module_name, origin, body, bm25(chunk) AS rank FROM chunk "
        "WHERE chunk MATCH ?"
    )
    params: list[Any] = [match]
    if module_name:
        sql += " AND module_name = ?"
        params.append(module_name)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 OR-query of bare terms."""
    terms = [
        "".join(ch for ch in tok if ch.isalnum() or ch in "_-")
        for tok in query.lower().split()
    ]
    terms = [f'"{t}"' for t in terms if len(t) > 2]
    return " OR ".join(terms[:24])


def source_text(conn: sqlite3.Connection, module_id: int, limit_chars: int) -> str:
    rows = conn.execute(
        "SELECT kind, origin, content FROM source WHERE module_id = ? ORDER BY id",
        (module_id,),
    ).fetchall()
    out, used = [], 0
    for r in rows:
        head = f"\n\n===== [{r['kind']}] {r['origin']} =====\n"
        body = r["content"]
        room = limit_chars - used - len(head)
        if room <= 0:
            break
        out.append(head + body[:room])
        used += len(head) + min(len(body), room)
    return "".join(out)


# --------------------------------------------------------------------------
# exams, tasks, runs, experience, audit
# --------------------------------------------------------------------------
def add_exam(
    conn: sqlite3.Connection,
    module_id: int,
    rnd: int,
    score: float,
    detail: list[dict[str, Any]],
    gaps: list[str],
) -> int:
    cur = conn.execute(
        "INSERT INTO exam (module_id, round, score, detail_json, gaps_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            module_id,
            rnd,
            round(score, 4),
            json.dumps(detail, ensure_ascii=False),
            json.dumps(gaps, ensure_ascii=False),
            now(),
        ),
    )
    return int(cur.lastrowid)


def latest_exam(conn: sqlite3.Connection, module_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM exam WHERE module_id = ? ORDER BY id DESC LIMIT 1", (module_id,)
    ).fetchone()


def create_task(conn: sqlite3.Connection, request: str, modules: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO task (request, status, modules_json, created_at, updated_at) "
        "VALUES (?, 'pending', ?, ?, ?)",
        (request, json.dumps(modules), now(), now()),
    )
    return int(cur.lastrowid)


def update_task(conn: sqlite3.Connection, task_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
    cols.append("updated_at = ?")
    vals.extend([now(), task_id])
    conn.execute(f"UPDATE task SET {', '.join(cols)} WHERE id = ?", vals)


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()


def list_tasks(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM task ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    )


def add_run(conn: sqlite3.Connection, **f: Any) -> int:
    cols = (
        "task_id module_id step command rationale decision reason "
        "exit_code stdout stderr duration"
    ).split()
    vals = [f.get(c) if c in ("task_id", "module_id", "exit_code") else f.get(c, "") for c in cols]
    # numeric defaults
    vals[cols.index("step")] = f.get("step", 0)
    vals[cols.index("duration")] = f.get("duration", 0.0)
    cur = conn.execute(
        f"INSERT INTO run ({', '.join(cols)}, created_at) "
        f"VALUES ({', '.join('?' * len(cols))}, ?)",
        [*vals, now()],
    )
    return int(cur.lastrowid)


def runs_for_task(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM run WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
    )


def recent_runs(conn: sqlite3.Connection, limit: int = 40) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    )


def add_experience(
    conn: sqlite3.Connection,
    module_id: int,
    task_id: int | None,
    outcome: str,
    lesson: str,
) -> None:
    conn.execute(
        "INSERT INTO experience (module_id, task_id, outcome, lesson, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (module_id, task_id, outcome, lesson, now()),
    )


def experiences(conn: sqlite3.Connection, module_id: int, limit: int = 12) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM experience WHERE module_id = ? ORDER BY id DESC LIMIT ?",
            (module_id, limit),
        ).fetchall()
    )


def audit(conn: sqlite3.Connection, event: str, payload: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO audit (event, payload, created_at) VALUES (?, ?, ?)",
        (event, json.dumps(payload or {}, ensure_ascii=False, default=str), now()),
    )


def recent_audit(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    )
