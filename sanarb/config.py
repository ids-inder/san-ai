"""Configuration for San Arb.

All knobs live here and are overridable from environment variables (prefix
``SANARB_``) so nothing secret or deployment-specific is baked into code.

The defaults encode the brief: a small account (<= Rs 10,000), auto-trade
capable, but *safe by default* - PAPER mode until explicitly armed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict


def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    return float(v) if v not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    return int(v) if v not in (None, "") else default


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v in (None, ""):
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    # ---- Account & capital --------------------------------------------------
    capital: float = field(default_factory=lambda: _env_float("SANARB_CAPITAL", 10_000.0))
    # Never commit more than this fraction of capital to a single position.
    per_trade_cap_pct: float = field(default_factory=lambda: _env_float("SANARB_PER_TRADE_CAP_PCT", 0.5))
    # Keep a cash buffer; never deploy the last rupee (fees, slippage).
    reserve_pct: float = field(default_factory=lambda: _env_float("SANARB_RESERVE_PCT", 0.05))
    max_open_positions: int = field(default_factory=lambda: _env_int("SANARB_MAX_OPEN_POSITIONS", 4))

    # ---- Execution mode (SAFETY) -------------------------------------------
    # mode: "paper" simulates fills; "live" talks to FYERS.
    mode: str = field(default_factory=lambda: _env_str("SANARB_MODE", "paper"))
    # Even in live mode, orders are blocked unless arm_live is explicitly true.
    # This is the physical safety switch. It is OFF unless SANARB_ARM_LIVE=1.
    arm_live: bool = field(default_factory=lambda: _env_bool("SANARB_ARM_LIVE", False))
    # Only ever place LIMIT orders on thin instruments (market orders can fill
    # far from the quote and vaporise a thin edge). Enforced by the risk gate.
    force_limit_orders: bool = field(default_factory=lambda: _env_bool("SANARB_FORCE_LIMIT", True))

    # ---- Cost model (per side, as fractions) --------------------------------
    # Discount-broker flat brokerage (Rs) applied per order leg. FYERS delivery
    # is effectively zero, but keep a knob. STT/exchange/GST/stamp approximated
    # as a single round-trip bps figure the edge must clear.
    brokerage_per_order: float = field(default_factory=lambda: _env_float("SANARB_BROKERAGE_PER_ORDER", 0.0))
    roundtrip_cost_pct: float = field(default_factory=lambda: _env_float("SANARB_ROUNDTRIP_COST_PCT", 0.004))
    # Assumed slippage vs quote when crossing the spread.
    slippage_pct: float = field(default_factory=lambda: _env_float("SANARB_SLIPPAGE_PCT", 0.002))

    # ---- Strategy thresholds -----------------------------------------------
    # International ETF: only BUY on a genuine discount beyond this (fraction).
    etf_discount_entry: float = field(default_factory=lambda: _env_float("SANARB_ETF_DISCOUNT_ENTRY", 0.015))
    # Warn / harvest when adjusted premium exceeds this.
    etf_premium_warn: float = field(default_factory=lambda: _env_float("SANARB_ETF_PREMIUM_WARN", 0.08))
    # SGB: BUY when exchange price is this far below gold fair value.
    sgb_discount_entry: float = field(default_factory=lambda: _env_float("SANARB_SGB_DISCOUNT_ENTRY", 0.02))

    # ---- Liquidity guard ----------------------------------------------------
    # Reject if the quoted spread is wider than this (thin book => bad fills).
    max_spread_pct: float = field(default_factory=lambda: _env_float("SANARB_MAX_SPREAD_PCT", 0.01))
    # Never take more than this fraction of resting size at the touch.
    max_book_participation: float = field(default_factory=lambda: _env_float("SANARB_MAX_BOOK_PARTICIPATION", 0.25))

    # ---- Engine loop --------------------------------------------------------
    scan_interval_sec: int = field(default_factory=lambda: _env_int("SANARB_SCAN_INTERVAL_SEC", 60))
    data_dir: str = field(default_factory=lambda: _env_str("SANARB_DATA_DIR", ".sanarb"))

    # ---- FYERS credentials (only needed for live) ---------------------------
    fyers_client_id: str = field(default_factory=lambda: _env_str("FYERS_CLIENT_ID", ""))
    fyers_secret_key: str = field(default_factory=lambda: _env_str("FYERS_SECRET_KEY", ""))
    fyers_redirect_uri: str = field(default_factory=lambda: _env_str("FYERS_REDIRECT_URI", ""))
    fyers_access_token: str = field(default_factory=lambda: _env_str("FYERS_ACCESS_TOKEN", ""))

    # ---- derived ------------------------------------------------------------
    @property
    def per_trade_cap(self) -> float:
        return self.capital * self.per_trade_cap_pct

    @property
    def deployable(self) -> float:
        return self.capital * (1.0 - self.reserve_pct)

    @property
    def live_enabled(self) -> bool:
        """Live order placement is only truly on when BOTH switches agree."""
        return self.mode == "live" and self.arm_live

    def redacted(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in list(d):
            if "secret" in k or "token" in k:
                d[k] = "***set***" if d[k] else ""
        return d


def load_settings() -> Settings:
    return Settings()
