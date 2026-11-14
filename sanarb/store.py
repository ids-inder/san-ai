"""Tiny JSON-file persistence for the paper ledger and audit log.

No database: a small account's state is a handful of positions and a trade log.
Everything is stored under settings.data_dir as plain JSON so it is inspectable
and git-ignorable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List


class Store:
    def __init__(self, data_dir: str) -> None:
        self._dir = data_dir
        os.makedirs(self._dir, exist_ok=True)
        self._ledger = os.path.join(self._dir, "paper_ledger.json")
        self._audit = os.path.join(self._dir, "audit.log.jsonl")

    # -- paper ledger ---------------------------------------------------------
    def load_ledger(self) -> Dict[str, Any]:
        if not os.path.exists(self._ledger):
            return {"cash": None, "positions": {}}
        with open(self._ledger, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_ledger(self, ledger: Dict[str, Any]) -> None:
        tmp = self._ledger + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, sort_keys=True)
        os.replace(tmp, self._ledger)

    # -- audit ----------------------------------------------------------------
    def audit(self, event: str, **fields: Any) -> None:
        rec = {"ts": time.time(), "event": event, **fields}
        with open(self._audit, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def read_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not os.path.exists(self._audit):
            return []
        with open(self._audit, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
        return [json.loads(ln) for ln in lines if ln.strip()]
