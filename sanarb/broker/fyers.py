"""FYERS live broker (lazy, gated).

This is the only path that can place a real order. It is constructed by the
executor ONLY when settings.live_enabled is true (mode == "live" AND
arm_live). Even then, every order has already passed the risk gate.

Regulatory reality: placing orders through the FYERS API requires an API app
registered under SEBI's retail-algo framework (effective 1 Apr 2026). An
unregistered/legacy app will be rejected by FYERS at the order endpoint - this
class surfaces that rejection rather than hiding it.
"""

from __future__ import annotations

from typing import List

from ..config import Settings
from ..models import OrderRequest, OrderResult, OrderType, Position, Side
from .base import Broker


# FYERS order-enum mapping (API v3).
_FYERS_SIDE = {Side.BUY: 1, Side.SELL: -1}
_FYERS_TYPE = {OrderType.LIMIT: 1, OrderType.MARKET: 2}


class FyersBroker(Broker):
    label = "FYERS(LIVE)"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _api(self):
        if self._client is not None:
            return self._client
        s = self._settings
        if not (s.fyers_client_id and s.fyers_access_token):
            raise RuntimeError("FYERS credentials missing for live broker.")
        try:
            from fyers_apiv3 import fyersModel  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("fyers-apiv3 not installed; `pip install fyers-apiv3`.") from exc
        self._client = fyersModel.FyersModel(
            client_id=s.fyers_client_id, token=s.fyers_access_token,
            is_async=False, log_path="",
        )
        return self._client

    def funds(self) -> float:
        resp = self._api().funds()
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            raise RuntimeError(f"FYERS funds() failed: {resp}")
        for row in resp.get("fund_limit", []):
            # id 10 == "Available Balance" in FYERS' funds payload.
            if row.get("id") == 10:
                return float(row.get("equityAmount", 0.0))
        return 0.0

    def positions(self) -> List[Position]:
        resp = self._api().positions()
        out: List[Position] = []
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            return out
        for p in resp.get("netPositions", []):
            qty = int(p.get("netQty", 0))
            if qty == 0:
                continue
            out.append(Position(symbol=p.get("symbol", ""), name=p.get("symbol", ""),
                                qty=qty, avg_price=float(p.get("avgPrice", 0.0))))
        return out

    def place(self, req: OrderRequest) -> OrderResult:
        order = {
            "symbol": req.symbol,
            "qty": req.qty,
            "type": _FYERS_TYPE[req.order_type],
            "side": _FYERS_SIDE[req.side],
            "productType": "CNC",            # delivery - correct for ETF/SGB
            "limitPrice": float(req.limit_price or 0.0),
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": (req.tag or "sanarb")[:20],
        }
        resp = self._api().place_order(data=order)
        if isinstance(resp, dict) and resp.get("s") == "ok":
            return OrderResult(True, str(resp.get("id")), req.qty,
                               float(req.limit_price or 0.0), "PENDING",
                               resp.get("message", ""), paper=False)
        msg = resp.get("message", str(resp)) if isinstance(resp, dict) else str(resp)
        return OrderResult(False, None, 0, 0.0, "REJECTED", msg, paper=False)
