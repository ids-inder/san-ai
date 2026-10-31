"""Audit helpers - a thin, deliberately boring wrapper over db.audit."""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db


def record(conn: sqlite3.Connection, event: str, **payload: Any) -> None:
    db.audit(conn, event, payload)
