"""The risk gate: turns a Signal into a sized, approved OrderRequest - or a veto.

This is the single place where money-at-risk decisions live. Every rule here is
a hard gate; a signal must pass all of them to produce an order. The gate is
deliberately pessimistic: when in doubt, it vetoes.

Rules enforced:
  1. Only actionable BUY/SELL signals with a positive net edge proceed.
  2. Position sizing respects per-trade cap, cash reserve, and available funds.
  3. Liquidity: spread must be within max_spread_pct; order qty must not exceed
     max_book_participation of resting size at the touch.
  4. Order discipline: on thin cash instruments, LIMIT only (no market orders).
  5. Portfolio: respect max_open_positions; never exceed deployable capital.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..config import Settings
from ..models import (Action, OrderRequest, OrderType, Position, Quote, Side,
                      Signal)


@dataclass(frozen=True)
class Decision:
    approved: bool
    order: Optional[OrderRequest]
    reason: str


class RiskGate:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def evaluate(
        self,
        signal: Signal,
        quote: Optional[Quote],
        funds: float,
        positions: List[Position],
    ) -> Decision:
        s = self._s

        if not signal.actionable:
            return Decision(False, None, f"non-actionable ({signal.action.value})")
        if signal.edge_pct <= 0:
            return Decision(False, None, "net edge not positive")
        if quote is None or quote.ltp <= 0:
            return Decision(False, None, "no live quote")

        side = signal.side or (Side.BUY if signal.action == Action.BUY else Side.SELL)

        # --- Order discipline: thin instruments are LIMIT-only. --------------
        order_type = signal.order_type
        if s.force_limit_orders and order_type != OrderType.LIMIT:
            return Decision(False, None, "market order blocked (force_limit_orders)")
        limit_price = signal.limit_price if signal.limit_price else round(quote.ltp, 2)

        # --- Liquidity: spread guard. ---------------------------------------
        spread = quote.spread_pct
        if spread is not None and spread > s.max_spread_pct:
            return Decision(False, None,
                            f"spread {spread*100:.2f}% > max {s.max_spread_pct*100:.2f}%")

        # ================= SELL path (harvest / exit) =======================
        if side == Side.SELL:
            held = _find(positions, signal.instrument.symbol)
            if not held or held.qty <= 0:
                return Decision(False, None, "no position to sell")
            qty = held.qty
            qty = self._liquidity_cap(qty, quote, side)
            if qty <= 0:
                return Decision(False, None, "liquidity cap -> 0 qty")
            return Decision(True, OrderRequest(
                symbol=signal.instrument.symbol, side=Side.SELL, qty=qty,
                order_type=OrderType.LIMIT, limit_price=limit_price,
                tag=f"{signal.strategy}:{signal.instrument.name}"[:60]),
                "approved sell/harvest")

        # ================= BUY path (enter edge) ============================
        # Portfolio slot check (only for a genuinely new symbol).
        existing = _find(positions, signal.instrument.symbol)
        if existing is None and len(positions) >= s.max_open_positions:
            return Decision(False, None, f"max_open_positions ({s.max_open_positions}) reached")

        deployed = sum(p.cost for p in positions)
        headroom_portfolio = s.deployable - deployed
        if headroom_portfolio <= 0:
            return Decision(False, None, "no deployable capital left")

        budget = min(s.per_trade_cap, headroom_portfolio, funds)
        if budget < limit_price:  # cannot afford even one unit
            return Decision(False, None,
                            f"budget Rs {budget:.0f} < 1 unit Rs {limit_price:.0f}")

        qty = int(budget // limit_price)
        qty = self._liquidity_cap(qty, quote, side)
        if qty <= 0:
            return Decision(False, None, "liquidity cap -> 0 qty")

        return Decision(True, OrderRequest(
            symbol=signal.instrument.symbol, side=Side.BUY, qty=qty,
            order_type=OrderType.LIMIT, limit_price=limit_price,
            tag=f"{signal.strategy}:{signal.instrument.name}"[:60]),
            f"approved buy {qty} @ {limit_price}")

    # ------------------------------------------------------------------------
    def _liquidity_cap(self, qty: int, quote: Quote, side: Side) -> int:
        """Never take more than a set fraction of resting size at the touch."""
        resting = quote.ask_qty if side == Side.BUY else quote.bid_qty
        if resting is None or resting <= 0:
            return qty  # unknown depth; rely on LIMIT price to protect us
        cap = int(resting * self._s.max_book_participation)
        return max(0, min(qty, cap))


def _find(positions: List[Position], symbol: str) -> Optional[Position]:
    for p in positions:
        if p.symbol == symbol:
            return p
    return None
