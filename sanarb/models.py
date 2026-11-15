"""Core value objects shared across the engine.

Everything here is a plain, immutable-ish dataclass. No business logic lives in
these types beyond trivial derived properties, so they are safe to pass between
the provider, strategy, risk, and broker layers without coupling them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Action(str, Enum):
    """What a strategy recommends for an instrument right now."""

    BUY = "BUY"          # a real, costed edge to enter
    SELL = "SELL"        # exit / harvest an existing holding
    AVOID = "AVOID"      # explicitly do not touch (e.g. buying into a premium)
    HOLD = "HOLD"        # no action; edge too small or absent


@dataclass(frozen=True)
class Instrument:
    """A tradable security as FYERS addresses it.

    `symbol` is the FYERS symbol id, e.g. "NSE:MON100-EQ" or "NSE:SGBDEC26-GB".
    `lot_size` is 1 for cash-segment ETFs / bonds; kept for forward
    compatibility with F&O instruments.
    """

    symbol: str
    name: str
    kind: str                 # "etf_intl" | "sgb" | "equity" | ...
    lot_size: int = 1
    tick_size: float = 0.01


@dataclass(frozen=True)
class Quote:
    """A market-data snapshot for one instrument.

    Fields beyond ltp are optional because different strategies need different
    reference data. A strategy must tolerate missing optionals and skip the
    instrument rather than guess.
    """

    symbol: str
    ltp: float                              # last traded price
    bid: Optional[float] = None
    ask: Optional[float] = None
    # Depth / liquidity: total quantity resting on the best five levels each side.
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    day_volume: Optional[int] = None        # shares traded today
    # Reference values used by fair-value models:
    published_nav: Optional[float] = None   # AMC/iNAV (may be stale for intl ETFs)
    ts: float = field(default_factory=time.time)

    @property
    def spread_pct(self) -> Optional[float]:
        if self.bid and self.ask and self.ask > 0:
            return (self.ask - self.bid) / self.ask
        return None


@dataclass(frozen=True)
class Signal:
    """A strategy's verdict for one instrument at one moment."""

    strategy: str
    instrument: Instrument
    action: Action
    # edge_pct is the costed, fair-value-adjusted expected edge, as a fraction
    # (0.02 == 2%). Positive means profitable after modelled costs.
    edge_pct: float
    ref_price: float                 # the price the edge is measured against
    rationale: str
    # Suggested order shape if acted on:
    side: Optional[Side] = None
    order_type: OrderType = OrderType.LIMIT
    limit_price: Optional[float] = None
    ts: float = field(default_factory=time.time)

    @property
    def actionable(self) -> bool:
        return self.action in (Action.BUY, Action.SELL)


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    limit_price: Optional[float] = None
    tag: str = ""                    # strategy/signal provenance


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    order_id: Optional[str]
    filled_qty: int
    avg_price: float
    status: str                      # "FILLED" | "REJECTED" | "PENDING" | "PAPER"
    message: str = ""
    paper: bool = False


@dataclass
class Position:
    symbol: str
    name: str
    qty: int
    avg_price: float

    @property
    def cost(self) -> float:
        return self.qty * self.avg_price
