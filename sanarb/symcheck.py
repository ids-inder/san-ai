"""Verify that every symbol the app trades exists in FYERS' symbol masters.

SGB series mature and get delisted, ETF tickers can change, and the gold
contract rolls monthly - so the symbol universe goes stale. This module checks
every configured symbol against FYERS' public master files, no credentials
needed, and is exposed as `sanarb verify-symbols`.

It checks:
  * the ETF strategy universe + the AMFI NAV map keys  (NSE master)
  * the SGB strategy universe                           (NSE master)
  * the resolvable near-month gold contract             (MCX master)
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import List, Set

from .config import Settings
from .providers.nav import DEFAULT_SYMBOL_TO_SCHEME
from .strategies.etf_premium import EtfPremiumStrategy
from .strategies.sgb_discount import SgbDiscountStrategy

NSE_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
MCX_MASTER_URL = "https://public.fyers.in/sym_details/MCX_COM.csv"
_COL_SYMBOL = 9


@dataclass
class Check:
    symbol: str
    kind: str
    ok: bool
    note: str = ""


def master_symbols(url: str, timeout: int = 45) -> Set[str]:
    """Return the set of FYERS tickers (col 9) in a master CSV."""
    req = urllib.request.Request(url, headers={"User-Agent": "sanarb/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - public file
        text = resp.read().decode("utf-8", errors="replace")
    out: Set[str] = set()
    for line in text.splitlines():
        c = line.split(",")
        if len(c) > _COL_SYMBOL and c[_COL_SYMBOL].strip():
            out.add(c[_COL_SYMBOL].strip())
    return out


def verify(settings: Settings) -> List[Check]:
    checks: List[Check] = []

    nse = master_symbols(NSE_MASTER_URL)

    # ETF universe
    for inst in EtfPremiumStrategy().instruments:
        ok = inst.symbol in nse
        checks.append(Check(inst.symbol, "etf", ok, "" if ok else "not in NSE master"))

    # NAV map keys (must also be valid NSE symbols)
    for sym in DEFAULT_SYMBOL_TO_SCHEME:
        ok = sym in nse
        checks.append(Check(sym, "nav-map", ok, "" if ok else "not in NSE master"))

    # SGB universe
    for inst in SgbDiscountStrategy().instruments:
        ok = inst.symbol in nse
        note = "" if ok else "not in NSE master (series may have matured)"
        checks.append(Check(inst.symbol, "sgb", ok, note))

    # Gold: resolve the near-month contract and confirm it exists in MCX master.
    try:
        from .providers.gold import resolve_near_month
        mcx_text = urllib.request.urlopen(  # nosec - public file
            urllib.request.Request(MCX_MASTER_URL, headers={"User-Agent": "sanarb/0.1"}),
            timeout=45,
        ).read().decode("utf-8", errors="replace")
        if settings.gold_symbol:
            sym = settings.gold_symbol
            ok = any(sym == ln.split(",")[_COL_SYMBOL].strip()
                     for ln in mcx_text.splitlines() if len(ln.split(",")) > _COL_SYMBOL)
            note = "" if ok else "pinned gold symbol not in MCX master"
        else:
            sym = resolve_near_month(mcx_text, settings.gold_root)
            ok, note = True, f"auto-resolved {settings.gold_root} near-month"
        checks.append(Check(sym, "gold", ok, note))
    except Exception as exc:
        checks.append(Check(settings.gold_symbol or settings.gold_root, "gold", False, str(exc)))

    return checks
