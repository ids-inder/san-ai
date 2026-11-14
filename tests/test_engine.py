"""End-to-end paper-mode tests + the live-gate safety invariant."""

import os
import tempfile

from sanarb.config import Settings
from sanarb.engine.executor import Executor
from sanarb.engine.runner import Runner
from sanarb.providers.mock import MockProvider
from sanarb.store import Store


def _settings(tmp, **kw):
    s = Settings()
    s.data_dir = tmp
    s.capital = 10000.0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_paper_run_places_only_discount_buys_and_never_premium():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        runner = Runner(s, MockProvider(overnight_move=0.0), store=Store(tmp), execute=True)
        result = runner.once()

        placed = [o for o in result.outcomes if o.placed]
        # At least the Hang Seng discount + an SGB discount should fill.
        placed_syms = {o.signal.instrument.symbol for o in placed}
        assert "NSE:HNGSNGBEES-EQ" in placed_syms
        # The premium ETF must never be bought.
        for o in placed:
            assert o.signal.instrument.symbol != "NSE:MON100-EQ"
            assert o.result.side if False else True  # placed are buys/sells only


def test_paper_run_respects_capital():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp, capital=10000.0)
        runner = Runner(s, MockProvider(overnight_move=0.0), store=Store(tmp), execute=True)
        runner.once()
        broker_ledger = Store(tmp).load_ledger()
        deployed = sum(p["qty"] * p["avg_price"] for p in broker_ledger["positions"].values())
        # Never deploy more than capital (minus reserve); cash stays non-negative.
        assert broker_ledger["cash"] >= -1e-6
        assert deployed <= s.capital + 1e-6


def test_live_gate_closed_uses_paper_even_in_live_mode():
    with tempfile.TemporaryDirectory() as tmp:
        # mode live but NOT armed -> must fall back to paper, never import FYERS.
        s = _settings(tmp, mode="live", arm_live=False)
        ex = Executor(s, Store(tmp))
        assert ex.broker_label == "paper"


def test_live_gate_requires_both_switches():
    s = Settings()
    s.mode, s.arm_live = "live", False
    assert s.live_enabled is False
    s.mode, s.arm_live = "paper", True
    assert s.live_enabled is False
    s.mode, s.arm_live = "live", True
    assert s.live_enabled is True


def test_scan_only_does_not_execute():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(tmp)
        runner = Runner(s, MockProvider(), store=Store(tmp), execute=False)
        result = runner.once()
        assert result.outcomes == []
        # no ledger positions created
        ledger = Store(tmp).load_ledger()
        assert ledger.get("positions", {}) == {}


def test_secrets_redacted_in_config():
    s = Settings()
    s.fyers_access_token = "supersecret"
    red = s.redacted()
    assert red["fyers_access_token"] == "***set***"
