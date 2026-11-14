"""Executor: route approved signals to a broker, with the live/paper gate.

Flow per signal:
    signal -> risk gate -> (approved order) -> broker.place -> audit

The live gate is enforced here and nowhere else:
  * settings.live_enabled must be true (mode == "live" AND arm_live), otherwise
    the PaperBroker is used no matter what.
  * Construction of the FyersBroker is lazy, so paper runs never import the SDK.

Every decision - vetoed or placed - is written to the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..config import Settings
from ..models import OrderResult, Signal
from ..providers.base import MarketDataProvider
from ..store import Store
from ..broker.base import Broker
from ..broker.paper import PaperBroker
from .risk import Decision, RiskGate


@dataclass
class ExecOutcome:
    signal: Signal
    decision: Decision
    result: Optional[OrderResult]

    @property
    def placed(self) -> bool:
        return self.result is not None and self.result.ok


class Executor:
    def __init__(self, settings: Settings, store: Store, broker: Optional[Broker] = None) -> None:
        self._s = settings
        self._store = store
        self._gate = RiskGate(settings)
        self._broker = broker if broker is not None else self._make_broker()

    def _make_broker(self) -> Broker:
        if self._s.live_enabled:
            # Imported lazily so paper never needs the SDK.
            from ..broker.fyers import FyersBroker
            self._store.audit("broker_selected", broker="FYERS(LIVE)",
                              mode=self._s.mode, arm_live=self._s.arm_live)
            return FyersBroker(self._s)
        self._store.audit("broker_selected", broker="paper",
                          mode=self._s.mode, arm_live=self._s.arm_live,
                          note="live gate closed" if self._s.mode == "live" else "paper mode")
        return PaperBroker(self._s, self._store)

    @property
    def broker_label(self) -> str:
        return self._broker.label

    def run(self, signals: List[Signal], provider: MarketDataProvider) -> List[ExecOutcome]:
        funds = self._broker.funds()
        positions = self._broker.positions()
        outcomes: List[ExecOutcome] = []

        # Pre-fetch quotes for the symbols we might trade.
        trade_syms = [s.instrument.symbol for s in signals if s.actionable]
        quotes = provider.quotes(trade_syms) if trade_syms else {}

        for sig in signals:
            if not sig.actionable:
                continue
            quote = quotes.get(sig.instrument.symbol)
            decision = self._gate.evaluate(sig, quote, funds, positions)
            if not decision.approved or decision.order is None:
                self._store.audit("veto", strategy=sig.strategy,
                                  symbol=sig.instrument.symbol,
                                  action=sig.action.value, reason=decision.reason)
                outcomes.append(ExecOutcome(sig, decision, None))
                continue

            result = self._broker.place(decision.order)
            self._store.audit("order", strategy=sig.strategy,
                              symbol=sig.instrument.symbol,
                              side=decision.order.side.value, qty=decision.order.qty,
                              limit=decision.order.limit_price, ok=result.ok,
                              status=result.status, paper=result.paper,
                              message=result.message, broker=self._broker.label)
            # Keep local funds/positions view in step so sizing stays correct
            # across multiple signals in one pass.
            if result.ok:
                funds = self._broker.funds()
                positions = self._broker.positions()
            outcomes.append(ExecOutcome(sig, decision, result))

        return outcomes
