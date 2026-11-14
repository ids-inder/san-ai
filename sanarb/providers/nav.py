"""NAV source for ETFs - the one price FYERS does not provide.

FYERS quotes carry last-traded price and the order book, but **not** an ETF's
NAV / iNAV. Without NAV there is no fair value and the ETF-premium strategy
cannot run. So we fetch NAV separately from AMFI's free, public daily file:

    https://www.amfiindia.com/spages/NAVAll.txt

Format (semicolon-separated), one scheme per line. AMFI added Plan/Option
columns, so NAV is the second-to-last field and Date the last - we read from the
end to stay robust to column-count changes:

    Scheme Code;ISIN ...;ISIN ...;Scheme Name;Plan;Option;Net Asset Value;Date

This is an END-OF-DAY NAV. For an international ETF it is already a day stale at
the Indian open, which is exactly why the fair-value model rolls it forward by
the overnight index move (see engine.fairvalue). If you have an intraday iNAV
feed, implement NavProvider against it and the rest of the engine is unchanged.

Network uses the standard library and respects HTTPS_PROXY. Results are cached
for the day so a scan loop makes at most one fetch.
"""

from __future__ import annotations

import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

# Map a FYERS symbol -> its AMFI scheme code. These codes are account-agnostic
# and stable (verified against NAVAll.txt, Nov 2026). Confirm the FYERS symbol
# strings against your account's symbol master before going live.
DEFAULT_SYMBOL_TO_SCHEME: Dict[str, str] = {
    "NSE:MON100-EQ": "114984",        # Motilal Oswal Nasdaq 100 ETF
    "NSE:MONQ50-EQ": "149438",        # Motilal Oswal Nasdaq Q50 ETF
    "NSE:MAFANG-EQ": "148927",        # Mirae Asset NYSE FANG+ ETF
    "NSE:HNGSNGBEES-EQ": "140095",    # Nippon India ETF Hang Seng BeES
}


def parse_navall(text: str) -> Dict[str, Tuple[float, str]]:
    """Parse NAVAll.txt into {scheme_code: (nav, date)}.

    Tolerant of the header line, blank lines, AMC-name section headers, and rows
    whose NAV is 'N.A.'. Pure function - unit-tested without a network.
    """
    out: Dict[str, Tuple[float, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ";" not in line:
            continue
        parts = line.split(";")
        if len(parts) < 6:
            continue
        code = parts[0].strip()
        if not code.isdigit():       # header row or section label
            continue
        # NAV is second-to-last, Date last - robust to added middle columns.
        nav_raw, date = parts[-2].strip(), parts[-1].strip()
        try:
            nav = float(nav_raw)
        except ValueError:           # 'N.A.' etc.
            continue
        out[code] = (nav, date)
    return out


class NavProvider(ABC):
    @abstractmethod
    def nav_for_symbol(self, symbol: str) -> Optional[float]:
        ...


class AmfiNavProvider(NavProvider):
    def __init__(
        self,
        symbol_to_scheme: Optional[Dict[str, str]] = None,
        ttl_sec: int = 6 * 3600,
        url: str = AMFI_NAV_URL,
        timeout: int = 20,
    ) -> None:
        self._map = dict(symbol_to_scheme or DEFAULT_SYMBOL_TO_SCHEME)
        self._ttl = ttl_sec
        self._url = url
        self._timeout = timeout
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._fetched_at: float = 0.0

    def _ensure(self) -> None:
        if self._cache and (time.time() - self._fetched_at) < self._ttl:
            return
        req = urllib.request.Request(self._url, headers={"User-Agent": "sanarb/0.1"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec - public file
            text = resp.read().decode("utf-8", errors="replace")
        parsed = parse_navall(text)
        if parsed:                   # only replace cache on a good parse
            self._cache = parsed
            self._fetched_at = time.time()

    def nav_for_symbol(self, symbol: str) -> Optional[float]:
        code = self._map.get(symbol)
        if not code:
            return None
        try:
            self._ensure()
        except Exception:            # network/parse failure -> no NAV, strategy skips
            return None
        row = self._cache.get(code)
        return row[0] if row else None
