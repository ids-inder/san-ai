"""Deterministic mock provider.

Used for paper mode, tests, and demos. It returns a fixed, hand-built market
picture that deliberately contains:

  - one international ETF trading at a fat PREMIUM (should be AVOID),
  - one international ETF at a genuine DISCOUNT (should be a BUY),
  - one SGB series at a discount to gold (BUY), one near fair value (HOLD),

so the strategy and risk layers can be exercised end to end without a network.

Values are illustrative, not live. Override any of them via the constructor to
script a scenario in a test.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..models import Quote
from .base import MarketDataProvider


# A reference picture (approximate, illustrative Nov-2026 shapes).
_DEFAULT_QUOTES: Dict[str, Quote] = {
    # Nasdaq-100 ETF trading well above fair value -> classic premium trap.
    "NSE:MON100-EQ": Quote(
        symbol="NSE:MON100-EQ",
        ltp=340.0,
        bid=339.6,
        ask=340.4,
        bid_qty=1800,
        ask_qty=1600,
        day_volume=120_000,
        published_nav=260.0,       # stale US-close NAV
    ),
    # A hypothetical intl ETF quoted BELOW fair value -> discount catch.
    "NSE:HNGSNGBEES-EQ": Quote(
        symbol="NSE:HNGSNGBEES-EQ",
        ltp=330.0,
        bid=329.5,
        ask=330.5,
        bid_qty=5000,
        ask_qty=5200,
        day_volume=900_000,
        published_nav=340.0,
    ),
    # SGB series trading at a discount to spot gold.
    "NSE:SGBDEC26-GB": Quote(
        symbol="NSE:SGBDEC26-GB",
        ltp=8_900.0,
        bid=8_880.0,
        ask=8_920.0,
        bid_qty=40,
        ask_qty=35,
        day_volume=1_200,
        published_nav=None,
    ),
    # SGB series near fair value.
    "NSE:SGBAUG27-GB": Quote(
        symbol="NSE:SGBAUG27-GB",
        ltp=9_480.0,
        bid=9_460.0,
        ask=9_500.0,
        bid_qty=20,
        ask_qty=18,
        day_volume=300,
        published_nav=None,
    ),
}


class MockProvider(MarketDataProvider):
    def __init__(
        self,
        quotes: Optional[Dict[str, Quote]] = None,
        overnight_move: float = 0.004,     # +0.4% Nasdaq futures overnight
        usdinr_ratio: float = 1.0,         # already reflected in NAV here
        gold_per_gram: float = 9_500.0,    # INR/gram, 999
    ) -> None:
        self._quotes = dict(quotes) if quotes is not None else dict(_DEFAULT_QUOTES)
        self._overnight = overnight_move
        self._usdinr = usdinr_ratio
        self._gold = gold_per_gram

    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    def overnight_index_move(self, index: str) -> float:
        return self._overnight

    def usdinr(self) -> float:
        return self._usdinr

    def gold_price_per_gram(self, purity: str = "999") -> float:
        return self._gold
