"""Gold price source for the SGB strategy.

SGB fair value tracks the **domestic IBJA 999** gold price (the reference used
at SGB redemption), NOT international spot - the two differ by India's import
duty + local premium, so an international feed would misprice the discount. The
cleanest domestic source that needs no extra credentials is FYERS' own **MCX
gold** feed, which tracks IBJA 999 to within ~0.1%.

Two wrinkles this module handles:

  1. MCX gold trades as dated monthly futures (e.g. MCX:GOLDM26DECFUT) with no
     continuous symbol, so we resolve the **near-month** contract from FYERS'
     public symbol master and roll automatically as contracts expire.

  2. MCX gold is **995 fineness, quoted per 10 g**; SGB redemption uses **999
     per gram**. So we convert:

         price_999_per_gram = (ltp / grams_per_quote) * purity_factor * (1 - basis)

     purity_factor defaults to 999/995 (=1.00402); basis is a small optional
     haircut for any near-month futures premium over spot (default 0).

Sources are swappable: StaticGoldProvider lets you hard-set a price (tests,
offline, or a manual IBJA figure); FyersMcxGoldProvider derives it live.
"""

from __future__ import annotations

import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

MCX_MASTER_URL = "https://public.fyers.in/sym_details/MCX_COM.csv"

# Column indices in the FYERS MCX symbol master (21-col CSV, verified Nov 2026).
_COL_EXPIRY = 8      # expiry epoch (seconds)
_COL_SYMBOL = 9      # FYERS ticker, e.g. "MCX:GOLDM26DECFUT"
_COL_ROOT = 13       # underlying root, e.g. "GOLDM"


class GoldPriceProvider(ABC):
    @abstractmethod
    def price_per_gram_999(self) -> float:
        ...


class StaticGoldProvider(GoldPriceProvider):
    """A fixed price - for tests, offline runs, or a manual IBJA override."""

    def __init__(self, price_per_gram: float) -> None:
        self._price = float(price_per_gram)

    def price_per_gram_999(self) -> float:
        if self._price <= 0:
            raise RuntimeError("static gold price not set")
        return self._price


def resolve_near_month(master_text: str, root: str, now: Optional[float] = None) -> str:
    """Return the FYERS ticker of the nearest unexpired future for `root`.

    Pure function over the master CSV text, so it is unit-tested without a
    network. Futures only (symbol ends 'FUT'); options are excluded.
    """
    now = now if now is not None else time.time()
    cands: List[Tuple[int, str]] = []
    for line in master_text.splitlines():
        c = line.split(",")
        if len(c) <= _COL_ROOT:
            continue
        if c[_COL_ROOT].strip() != root:
            continue
        sym = c[_COL_SYMBOL].strip()
        if not sym.endswith("FUT"):
            continue
        try:
            exp = int(c[_COL_EXPIRY])
        except ValueError:
            continue
        if exp > now:
            cands.append((exp, sym))
    if not cands:
        raise RuntimeError(f"no unexpired {root} future found in MCX master")
    cands.sort()
    return cands[0][1]


class FyersMcxGoldProvider(GoldPriceProvider):
    """Derive 999 gold/gram from the near-month MCX gold future via FYERS.

    `quote_ltp` is any callable symbol -> last price (we pass FyersProvider's own
    lookup), so this class needs no direct SDK dependency and is testable with a
    stub. The symbol master and the resolved contract are cached for the day.
    """

    def __init__(
        self,
        quote_ltp: Callable[[str], Optional[float]],
        root: str = "GOLDM",
        grams_per_quote: float = 10.0,
        purity_factor: float = 999.0 / 995.0,
        basis_adj: float = 0.0,
        explicit_symbol: str = "",
        master_url: str = MCX_MASTER_URL,
        ttl_sec: int = 12 * 3600,
        timeout: int = 20,
    ) -> None:
        self._quote = quote_ltp
        self._root = root
        self._grams = grams_per_quote
        self._purity = purity_factor
        self._basis = basis_adj
        self._explicit = explicit_symbol
        self._url = master_url
        self._ttl = ttl_sec
        self._timeout = timeout
        self._symbol: Optional[str] = None
        self._resolved_at = 0.0

    def _fetch_master(self) -> str:
        req = urllib.request.Request(self._url, headers={"User-Agent": "sanarb/0.1"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec - public file
            return resp.read().decode("utf-8", errors="replace")

    def symbol(self) -> str:
        if self._explicit:
            return self._explicit
        fresh = self._symbol and (time.time() - self._resolved_at) < self._ttl
        if not fresh:
            self._symbol = resolve_near_month(self._fetch_master(), self._root)
            self._resolved_at = time.time()
        return self._symbol  # type: ignore[return-value]

    def price_per_gram_999(self) -> float:
        sym = self.symbol()
        ltp = self._quote(sym)
        if not ltp or ltp <= 0:
            raise RuntimeError(f"no LTP for gold contract {sym}")
        return (ltp / self._grams) * self._purity * (1.0 - self._basis)
