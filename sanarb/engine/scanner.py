"""Scanner: run every strategy and collect their signals.

Pure orchestration - it asks each strategy to evaluate against the provider and
flattens the results. Sorting puts the most actionable, highest-edge signals
first so a human (or the executor) sees what matters.
"""

from __future__ import annotations

from typing import List

from ..config import Settings
from ..models import Signal
from ..providers.base import MarketDataProvider
from ..strategies.base import Strategy


def _rank(sig: Signal) -> tuple:
    # actionable first, then by edge size descending
    order = {"BUY": 0, "SELL": 1, "AVOID": 2, "HOLD": 3}
    return (order.get(sig.action.value, 9), -sig.edge_pct)


class Scanner:
    def __init__(self, strategies: List[Strategy], settings: Settings) -> None:
        self._strategies = strategies
        self._settings = settings

    def scan(self, provider: MarketDataProvider) -> List[Signal]:
        signals: List[Signal] = []
        for strat in self._strategies:
            try:
                signals.extend(strat.evaluate(provider, self._settings))
            except Exception as exc:  # one bad strategy must not kill the scan
                # Surface, don't swallow silently - but keep scanning.
                import logging
                logging.getLogger("sanarb.scanner").warning(
                    "strategy %s failed: %s", strat.name, exc)
        signals.sort(key=_rank)
        return signals
