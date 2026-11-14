"""International ETF premium / discount strategy.

What it does, and - just as importantly - what it refuses to do:

  * It NEVER issues a BUY into a premium. Buying an India-listed US/foreign ETF
    that trades above fair value means overpaying for the exact exposure you can
    get at NAV via the fund-of-fund route. That is the trap, so a premium above
    the warn threshold yields AVOID (or SELL, to harvest, if you hold it).

  * It issues a BUY only on a genuine DISCOUNT to fair value beyond the
    configured entry threshold - the one clean entry a fresh small account has,
    because the ETF's redemption arm still supports the floor even while unit
    creation is frozen.

The "fair value" is the stale foreign-close NAV rolled forward by the overnight
index move and the rupee (see engine.fairvalue), so time-zone drift is not
mistaken for an edge.
"""

from __future__ import annotations

from typing import Dict, List

from ..config import Settings
from ..models import Action, Instrument, OrderType, Side, Signal
from ..providers.base import MarketDataProvider
from ..engine.fairvalue import intl_etf_fair_value, premium_pct
from .base import Strategy


# Each instrument is paired with the index whose overnight move rolls its NAV
# forward. The provider decides how (or whether) to quantify that move.
_UNIVERSE: List[tuple[Instrument, str]] = [
    (Instrument("NSE:MON100-EQ", "Motilal Oswal Nasdaq-100 ETF", "etf_intl"), "NASDAQ100"),
    (Instrument("NSE:MAFANG-EQ", "Mirae Asset NYSE FANG+ ETF", "etf_intl"), "NYSEFANG"),
    (Instrument("NSE:HNGSNGBEES-EQ", "Nippon Hang Seng BeES", "etf_intl"), "HANGSENG"),
]


class EtfPremiumStrategy(Strategy):
    name = "etf_premium"

    def __init__(self) -> None:
        self._pairs = list(_UNIVERSE)

    @property
    def instruments(self) -> List[Instrument]:
        return [inst for inst, _ in self._pairs]

    def evaluate(self, provider: MarketDataProvider, settings: Settings) -> List[Signal]:
        symbols = [inst.symbol for inst, _ in self._pairs]
        quotes: Dict = provider.quotes(symbols)
        usdinr = provider.usdinr()
        signals: List[Signal] = []

        for inst, index in self._pairs:
            q = quotes.get(inst.symbol)
            if q is None or q.published_nav is None or q.published_nav <= 0:
                # No NAV reference -> we cannot judge fair value; skip quietly.
                continue

            overnight = provider.overnight_index_move(index)
            fv = intl_etf_fair_value(q.published_nav, overnight, usdinr)
            prem = premium_pct(q.ltp, fv)  # +premium / -discount vs fair value

            if prem <= -settings.etf_discount_entry:
                # Discount to fair value. Gross edge is the discount size; net it
                # against costs. Act only if still positive.
                gross = -prem
                net = self.net_edge(gross, settings)
                if net > 0:
                    limit = round(q.ltp, 2)
                    signals.append(Signal(
                        strategy=self.name, instrument=inst, action=Action.BUY,
                        edge_pct=net, ref_price=fv,
                        rationale=(
                            f"Trading {gross*100:.1f}% BELOW fair value "
                            f"(fv={fv:.2f} vs ltp={q.ltp:.2f}); redemption arm "
                            f"supports the floor. Net edge {net*100:.1f}% after costs."
                        ),
                        side=Side.BUY, order_type=OrderType.LIMIT, limit_price=limit,
                    ))
                else:
                    signals.append(_hold(self.name, inst, fv,
                        f"Discount {gross*100:.1f}% too small to clear costs."))
            elif prem >= settings.etf_premium_warn:
                signals.append(Signal(
                    strategy=self.name, instrument=inst, action=Action.AVOID,
                    edge_pct=-prem, ref_price=fv,
                    rationale=(
                        f"Trading {prem*100:.1f}% ABOVE fair value - buying is "
                        f"overpaying for NAV exposure. If held, harvest into the "
                        f"premium; otherwise do not enter."
                    ),
                    side=Side.SELL, order_type=OrderType.LIMIT, limit_price=round(q.ltp, 2),
                ))
            else:
                signals.append(_hold(self.name, inst, fv,
                    f"Near fair value (premium {prem*100:+.1f}%); no edge."))

        return signals


def _hold(strategy: str, inst: Instrument, ref: float, why: str) -> Signal:
    return Signal(strategy=strategy, instrument=inst, action=Action.HOLD,
                  edge_pct=0.0, ref_price=ref, rationale=why)
