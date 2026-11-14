"""FYERS-backed market-data provider.

The FYERS SDK (``fyers-apiv3``) and any outbound calls are imported lazily so
that importing this module never drags in a network dependency. The engine only
constructs a FyersProvider when running live with credentials present.

This class is intentionally thin: it maps FYERS' quote payload onto our Quote
model. The macro reference values (overnight index move, USD/INR, spot gold)
are fetched from configurable FYERS symbols; where a feed is unavailable the
method returns a neutral value and the fair-value model degrades gracefully
(the premium just isn't time-zone-adjusted).

NOTE: Live order placement via FYERS additionally requires an API app
registered under SEBI's retail-algo framework (effective 1 Apr 2026). Data
access does not, but see broker/fyers.py.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..config import Settings
from ..models import Quote
from .base import MarketDataProvider
from .nav import NavProvider

# Symbols used to derive macro reference values. Overridable if FYERS changes
# its symbology.
NASDAQ_FUT_SYMBOL = "GLOBAL:NAS100-INDEX"   # placeholder; set to your data feed
USDINR_SYMBOL = "NSE:USDINR-INDEX"
GOLD_SYMBOL = "MCX:GOLDPETAL-INDEX"         # petal ~ 1g; adjust to your feed


class FyersProvider(MarketDataProvider):
    def __init__(self, settings: Settings, nav_provider: Optional[NavProvider] = None) -> None:
        self._settings = settings
        self._client = None  # lazily created
        self._nav = nav_provider  # supplies ETF NAV, which FYERS does not

    # -- SDK plumbing ---------------------------------------------------------
    def _api(self):
        if self._client is not None:
            return self._client
        if not (self._settings.fyers_client_id and self._settings.fyers_access_token):
            raise RuntimeError(
                "FYERS credentials missing. Run `sanarb auth` and set "
                "FYERS_CLIENT_ID / FYERS_ACCESS_TOKEN."
            )
        try:
            from fyers_apiv3 import fyersModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when live
            raise RuntimeError(
                "fyers-apiv3 is not installed. `pip install sanarb[live]` "
                "(or `pip install fyers-apiv3`) to enable live data."
            ) from exc
        self._client = fyersModel.FyersModel(
            client_id=self._settings.fyers_client_id,
            token=self._settings.fyers_access_token,
            is_async=False,
            log_path="",
        )
        return self._client

    # -- MarketDataProvider ---------------------------------------------------
    def quotes(self, symbols: List[str], with_depth: bool = True) -> Dict[str, Quote]:
        """Fetch prices for `symbols`.

        Two FYERS endpoints are combined:
          * quotes()  -> last price, best bid/ask, day volume (batched, 1 call);
          * depth()   -> resting book size per symbol (1 call each), used by the
                         liquidity gate. quotes() does NOT carry depth, so this
                         second call is required. It is best-effort: if a depth
                         call fails, the quote is still returned with qty=None and
                         the risk gate falls back to LIMIT-price protection.

        NAV is intentionally left None here - it is not in the FYERS feed. A NAV
        source (e.g. AMFI daily NAV / exchange iNAV) is injected separately by a
        NavProvider; see providers/nav.py.
        """
        if not symbols:
            return {}
        api = self._api()
        out: Dict[str, Quote] = {}

        resp = api.quotes({"symbols": ",".join(symbols)})
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            return out

        # First pass: price fields from quotes().
        price_rows: Dict[str, dict] = {}
        for row in resp.get("d", []):
            sym = row.get("n")
            v = row.get("v", {}) or {}
            if sym and "lp" in v:
                price_rows[sym] = v

        # Second pass: resting size from depth() (one symbol per call).
        for sym, v in price_rows.items():
            bid_qty = ask_qty = None
            if with_depth:
                bid_qty, ask_qty = self._depth_totals(sym)
            nav = self._nav.nav_for_symbol(sym) if self._nav else None
            out[sym] = Quote(
                symbol=sym,
                ltp=float(v.get("lp")),
                bid=float(v["bid"]) if v.get("bid") else None,
                ask=float(v["ask"]) if v.get("ask") else None,
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                day_volume=int(v["volume"]) if v.get("volume") else None,
                published_nav=nav,  # from NavProvider (AMFI); FYERS has no NAV
            )
        return out

    def _depth_totals(self, symbol: str):
        """Return (total_bid_qty, total_ask_qty) from depth(), or (None, None).

        Best-effort: any failure degrades to unknown depth rather than raising,
        so one illiquid symbol cannot abort a scan.
        """
        try:
            resp = self._api().depth({"symbol": symbol, "ohlcv_flag": "1"})
        except Exception:  # pragma: no cover - network dependent
            return None, None
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            return None, None
        node = (resp.get("d") or {}).get(symbol) or {}
        # FYERS exposes side totals as totalbuyqty / totalsellqty.
        bid_qty = node.get("totalbuyqty")
        ask_qty = node.get("totalsellqty")
        return (int(bid_qty) if bid_qty else None,
                int(ask_qty) if ask_qty else None)

    def _ltp(self, symbol: str) -> Optional[float]:
        q = self.quotes([symbol]).get(symbol)
        return q.ltp if q else None

    def overnight_index_move(self, index: str) -> float:
        # Without a reliable futures feed we return 0.0 (no adjustment). Wire
        # your index-futures symbol here if you have entitlement.
        return 0.0

    def usdinr(self) -> float:
        # Returning 1.0 means "NAV already in INR / no extra FX adjustment".
        return 1.0

    def gold_price_per_gram(self, purity: str = "999") -> float:
        px = self._ltp(GOLD_SYMBOL)
        if px is None:
            raise RuntimeError(
                "Could not fetch spot gold; set GOLD_SYMBOL to a symbol your "
                "FYERS account can quote, or inject gold price another way."
            )
        return float(px)
