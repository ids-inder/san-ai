"""Fair-value helpers.

The single most important idea in San Arb: the number a naive app shows -
``(market_price - NAV) / NAV`` - is *wrong* for an international ETF, because
the published NAV is struck at the last foreign close and is stale by the time
India trades. Part of any apparent premium is just the market pricing in the
overnight move of the underlying index and the rupee - that part is FAIR, not an
edge.

So we compute a fair value that rolls the stale NAV forward:

    fair_value = nav * (1 + overnight_index_move) * usdinr_ratio

and measure premium/discount against *that*. Only the residual is a real
dislocation worth acting on.
"""

from __future__ import annotations


def intl_etf_fair_value(nav: float, overnight_index_move: float, usdinr_ratio: float) -> float:
    """Roll a stale foreign-close NAV forward to an India-time fair value."""
    return nav * (1.0 + overnight_index_move) * usdinr_ratio


def premium_pct(market_price: float, fair_value: float) -> float:
    """Signed premium: >0 means price is above fair value (premium), <0 discount."""
    if fair_value <= 0:
        raise ValueError("fair_value must be positive")
    return (market_price - fair_value) / fair_value


def sgb_fair_value(gold_per_gram: float, grams_per_unit: float = 1.0) -> float:
    """SGB redemption value tracks gold; one unit == `grams_per_unit` grams."""
    return gold_per_gram * grams_per_unit
