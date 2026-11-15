"""Sovereign Gold Bond discount-catch strategy.

SGBs stopped being issued (no tranche since Feb 2024), so secondary-market
units on NSE/BSE are the only way in. They often trade at a discount to the
underlying gold value because the book is thin and some holders are forced
sellers. Buying a discounted series gives cheap gold exposure plus the 2.5%
coupon on face value, with the discount captured at redemption.

The gross edge here is (gold fair value - exchange price) / fair value. Because
SGB books are thin, this strategy leans hard on the risk gate's liquidity and
LIMIT-order rules - a market order here can pay the whole spread and wipe the
edge. It only ever issues LIMIT BUY signals.

Caveat surfaced in the rationale, not the math: redemption at maturity is
generally capital-gains tax-exempt for individuals, but that treatment for a
*secondary* buyer is contested - a holder should confirm with a CA. The edge is
reported pre-tax.
"""

from __future__ import annotations

from typing import Dict, List

from ..config import Settings
from ..models import Action, Instrument, OrderType, Side, Signal
from ..providers.base import MarketDataProvider
from ..engine.fairvalue import sgb_fair_value, premium_pct
from .base import Strategy


# Each SGB series is one unit == one gram of gold. (grams_per_unit kept at 1.0.)
# Tickers verified against FYERS' NSE symbol master (NSE_CM.csv): the series
# suffix is "-GB". Near-maturity series are preferred - gold convergence is more
# certain the closer redemption is. Refresh this list from the master as series
# mature; FYERS drops expired ones.
_UNIVERSE: List[Instrument] = [
    Instrument("NSE:SGBDEC26-GB", "SGB 2.5% Gold Bond 2026 Sr-IV", "sgb"),
    Instrument("NSE:SGBJAN27-GB", "SGB 2.5% Gold Bond 2027 Sr-V", "sgb"),
    Instrument("NSE:SGBAUG27-GB", "SGB 2.5% Gold Bond 2027 Sr-III", "sgb"),
    Instrument("NSE:SGBAPR28I-GB", "SGB 2.5% Gold Bond 2028 Sr-I", "sgb"),
]


class SgbDiscountStrategy(Strategy):
    name = "sgb_discount"

    def __init__(self) -> None:
        self._universe = list(_UNIVERSE)

    @property
    def instruments(self) -> List[Instrument]:
        return list(self._universe)

    def evaluate(self, provider: MarketDataProvider, settings: Settings) -> List[Signal]:
        symbols = [i.symbol for i in self._universe]
        quotes: Dict = provider.quotes(symbols)
        gold = provider.gold_price_per_gram("999")
        fv = sgb_fair_value(gold, grams_per_unit=1.0)
        signals: List[Signal] = []

        for inst in self._universe:
            q = quotes.get(inst.symbol)
            if q is None:
                continue
            prem = premium_pct(q.ltp, fv)  # negative == discount to gold

            if prem <= -settings.sgb_discount_entry:
                gross = -prem
                net = self.net_edge(gross, settings)
                if net > 0:
                    signals.append(Signal(
                        strategy=self.name, instrument=inst, action=Action.BUY,
                        edge_pct=net, ref_price=fv,
                        rationale=(
                            f"{gross*100:.1f}% discount to gold fair value "
                            f"(fv/g={fv:.0f} vs ltp={q.ltp:.0f}); + 2.5% coupon on "
                            f"face. Net {net*100:.1f}% after costs. Use LIMIT - book "
                            f"is thin. Maturity tax treatment for secondary buyers: "
                            f"confirm with a CA."
                        ),
                        side=Side.BUY, order_type=OrderType.LIMIT,
                        limit_price=round(q.ltp, 2),
                    ))
                else:
                    signals.append(_hold(self.name, inst, fv,
                        f"Discount {gross*100:.1f}% too small after costs."))
            else:
                signals.append(_hold(self.name, inst, fv,
                    f"No meaningful discount (premium {prem*100:+.1f}% to gold)."))

        return signals


def _hold(strategy: str, inst: Instrument, ref: float, why: str) -> Signal:
    return Signal(strategy=strategy, instrument=inst, action=Action.HOLD,
                  edge_pct=0.0, ref_price=ref, rationale=why)
