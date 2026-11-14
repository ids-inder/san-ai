"""Broker interface: the only component that can move real money."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..models import OrderRequest, OrderResult, Position


class Broker(ABC):
    #: human-readable, shown in logs so it is always obvious which broker is live
    label: str = "base"

    @abstractmethod
    def funds(self) -> float:
        """Available cash for trading, in INR."""

    @abstractmethod
    def positions(self) -> List[Position]:
        ...

    @abstractmethod
    def place(self, req: OrderRequest) -> OrderResult:
        ...
