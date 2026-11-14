"""Strategy interface.

A strategy is a pure function of (market data, settings) -> list[Signal]. It
never touches the broker, never sizes positions against the account, and never
decides whether a trade is affordable - that is the risk gate's job. Keeping
strategies pure makes them trivially testable and keeps all money-at-risk logic
in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..config import Settings
from ..models import Instrument, Signal
from ..providers.base import MarketDataProvider


class Strategy(ABC):
    #: stable short name used in signals, logs, and the ledger tag
    name: str = "base"

    @property
    @abstractmethod
    def instruments(self) -> List[Instrument]:
        """The universe this strategy watches."""

    @abstractmethod
    def evaluate(self, provider: MarketDataProvider, settings: Settings) -> List[Signal]:
        """Produce a signal per watched instrument (HOLD/AVOID included).

        Implementations must tolerate missing quotes and reference data by
        skipping the instrument rather than raising.
        """

    def net_edge(self, gross_edge: float, settings: Settings) -> float:
        """Subtract the modelled round-trip cost + slippage from a gross edge."""
        return gross_edge - settings.roundtrip_cost_pct - settings.slippage_pct
