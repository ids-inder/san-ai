"""Paper broker: simulates fills and keeps a JSON ledger.

Fills are modelled conservatively: a BUY fills at the limit price plus assumed
slippage, a SELL at limit minus slippage, so paper results never flatter the
strategy. This is the default broker; it never touches FYERS.
"""

from __future__ import annotations

from typing import List

from ..config import Settings
from ..models import OrderRequest, OrderResult, Position, Side
from ..store import Store
from .base import Broker


class PaperBroker(Broker):
    label = "paper"

    def __init__(self, settings: Settings, store: Store) -> None:
        self._settings = settings
        self._store = store
        ledger = store.load_ledger()
        if ledger.get("cash") is None:
            ledger["cash"] = settings.capital
            ledger.setdefault("positions", {})
            store.save_ledger(ledger)
        self._ledger = ledger

    def funds(self) -> float:
        return float(self._ledger.get("cash", 0.0))

    def positions(self) -> List[Position]:
        out: List[Position] = []
        for sym, p in self._ledger.get("positions", {}).items():
            out.append(Position(symbol=sym, name=p.get("name", sym),
                                qty=int(p["qty"]), avg_price=float(p["avg_price"])))
        return out

    def place(self, req: OrderRequest) -> OrderResult:
        slip = self._settings.slippage_pct
        base = req.limit_price if req.limit_price is not None else 0.0
        if base <= 0:
            return OrderResult(False, None, 0, 0.0, "REJECTED",
                               "paper broker needs a limit price", paper=True)
        fill = base * (1 + slip) if req.side == Side.BUY else base * (1 - slip)
        fill = round(fill, 2)
        cost = fill * req.qty + self._settings.brokerage_per_order

        positions = self._ledger.setdefault("positions", {})
        if req.side == Side.BUY:
            if cost > self.funds() + 1e-6:
                return OrderResult(False, None, 0, 0.0, "REJECTED",
                                   "insufficient paper cash", paper=True)
            self._ledger["cash"] = self.funds() - cost
            pos = positions.get(req.symbol)
            if pos:
                tot_qty = pos["qty"] + req.qty
                pos["avg_price"] = (pos["avg_price"] * pos["qty"] + fill * req.qty) / tot_qty
                pos["qty"] = tot_qty
            else:
                positions[req.symbol] = {"qty": req.qty, "avg_price": fill, "name": req.tag}
        else:  # SELL
            pos = positions.get(req.symbol)
            if not pos or pos["qty"] < req.qty:
                return OrderResult(False, None, 0, 0.0, "REJECTED",
                                   "no paper position to sell", paper=True)
            pos["qty"] -= req.qty
            self._ledger["cash"] = self.funds() + fill * req.qty - self._settings.brokerage_per_order
            if pos["qty"] == 0:
                positions.pop(req.symbol)

        self._store.save_ledger(self._ledger)
        return OrderResult(True, f"PAPER-{req.symbol}-{req.side.value}", req.qty, fill,
                           "PAPER", "simulated fill", paper=True)
