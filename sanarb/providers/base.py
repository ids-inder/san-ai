"""Market-data provider interface.

A provider turns FYERS symbols into Quote snapshots and supplies the few macro
reference values the fair-value models need (overnight index-futures move,
USD/INR ratio, spot gold). Keeping this behind an interface lets the whole
engine run and be tested against MockProvider with no network or credentials.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from ..models import Quote


class MarketDataProvider(ABC):
    @abstractmethod
    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        """Return a Quote for each symbol that could be fetched.

        Missing symbols are simply omitted; callers must handle absence.
        """

    @abstractmethod
    def overnight_index_move(self, index: str) -> float:
        """Fractional move in the relevant index since the ETF's NAV was struck.

        For a US ETF this is e.g. Nasdaq-100 futures move since the last US
        close. 0.0 means "no adjustment / unknown".
        """

    @abstractmethod
    def usdinr(self) -> float:
        """Current USD/INR spot, used to FX-adjust a stale foreign NAV."""

    @abstractmethod
    def gold_price_per_gram(self, purity: str = "999") -> float:
        """Spot gold in INR per gram at the given purity, for SGB fair value."""
