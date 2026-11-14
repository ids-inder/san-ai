"""Strategy registry.

The set of strategies the engine runs. All are minimum-capital, FYERS cash-
segment, and emit only LIMIT orders - matching the brief (auto-trade capable,
under Rs 10,000). Cash-futures / buyback strategies are intentionally absent
because they need far more capital; add them here when the account grows.
"""

from __future__ import annotations

from typing import List

from .base import Strategy
from .etf_premium import EtfPremiumStrategy
from .sgb_discount import SgbDiscountStrategy


def default_strategies() -> List[Strategy]:
    return [EtfPremiumStrategy(), SgbDiscountStrategy()]
