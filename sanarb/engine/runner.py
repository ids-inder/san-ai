"""The scan->execute loop.

`once()` does a single pass (scan + optional execute). `loop()` repeats it on
settings.scan_interval_sec. The runner wires together provider, scanner,
executor, and store, and is what the CLI's `scan` and `run` commands call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from ..config import Settings
from ..models import Signal
from ..providers.base import MarketDataProvider
from ..store import Store
from ..strategies.registry import default_strategies
from .executor import ExecOutcome, Executor
from .scanner import Scanner


@dataclass
class PassResult:
    signals: List[Signal]
    outcomes: List[ExecOutcome]


class Runner:
    def __init__(
        self,
        settings: Settings,
        provider: MarketDataProvider,
        store: Optional[Store] = None,
        execute: bool = False,
    ) -> None:
        self._s = settings
        self._provider = provider
        self._store = store or Store(settings.data_dir)
        self._scanner = Scanner(default_strategies(), settings)
        self._execute = execute
        self._executor = Executor(settings, self._store) if execute else None

    @property
    def broker_label(self) -> str:
        return self._executor.broker_label if self._executor else "none"

    def once(self) -> PassResult:
        signals = self._scanner.scan(self._provider)
        outcomes: List[ExecOutcome] = []
        if self._executor is not None:
            outcomes = self._executor.run(signals, self._provider)
        self._store.audit("scan", n_signals=len(signals),
                          n_actionable=sum(1 for s in signals if s.actionable),
                          executed=self._execute, broker=self.broker_label)
        return PassResult(signals, outcomes)

    def loop(self, max_passes: Optional[int] = None):
        n = 0
        while True:
            yield self.once()
            n += 1
            if max_passes is not None and n >= max_passes:
                return
            time.sleep(self._s.scan_interval_sec)
