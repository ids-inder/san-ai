from sanarb.config import Settings
from sanarb.models import Action, Quote
from sanarb.providers.mock import MockProvider
from sanarb.strategies.etf_premium import EtfPremiumStrategy
from sanarb.strategies.sgb_discount import SgbDiscountStrategy


def _settings(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# --- ETF premium strategy ---------------------------------------------------
def test_etf_premium_never_buys_into_premium():
    # MON100 in the mock trades far above fair value.
    prov = MockProvider(overnight_move=0.0)
    sigs = {s.instrument.symbol: s for s in EtfPremiumStrategy().evaluate(prov, _settings())}
    mon = sigs["NSE:MON100-EQ"]
    assert mon.action == Action.AVOID
    assert mon.action != Action.BUY


def test_etf_discount_produces_buy():
    # Hang Seng in the mock trades below NAV (330 vs 340) -> discount buy.
    prov = MockProvider(overnight_move=0.0)
    sigs = {s.instrument.symbol: s for s in EtfPremiumStrategy().evaluate(prov, _settings())}
    hs = sigs["NSE:HNGSNGBEES-EQ"]
    assert hs.action == Action.BUY
    assert hs.edge_pct > 0
    assert hs.limit_price is not None


def test_etf_discount_rejected_when_costs_exceed_edge():
    # A 2.9% discount but huge assumed costs -> net edge negative -> HOLD.
    prov = MockProvider(overnight_move=0.0)
    s = _settings(roundtrip_cost_pct=0.05, slippage_pct=0.05)
    sigs = {x.instrument.symbol: x for x in EtfPremiumStrategy().evaluate(prov, s)}
    assert sigs["NSE:HNGSNGBEES-EQ"].action == Action.HOLD


def test_etf_timezone_adjustment_suppresses_false_discount():
    # ETF at 330 vs NAV 340 looks like a discount, but if the index fell ~3%
    # overnight the fair value drops too and the discount shrinks below entry.
    prov = MockProvider(overnight_move=-0.03)
    sigs = {x.instrument.symbol: x for x in EtfPremiumStrategy().evaluate(prov, _settings())}
    assert sigs["NSE:HNGSNGBEES-EQ"].action != Action.BUY


# --- SGB discount strategy --------------------------------------------------
def test_sgb_discount_buy_on_cheap_series():
    prov = MockProvider(gold_per_gram=9500.0)   # SGBDEC26 at 8900 -> ~6.3% disc
    sigs = {s.instrument.symbol: s for s in SgbDiscountStrategy().evaluate(prov, _settings())}
    cheap = sigs["NSE:SGBDEC26-GB"]
    assert cheap.action == Action.BUY
    assert cheap.edge_pct > 0


def test_sgb_no_signal_near_fair_value():
    prov = MockProvider(gold_per_gram=9500.0)   # SGBAUG27 at 9480 ~ fair
    sigs = {s.instrument.symbol: s for s in SgbDiscountStrategy().evaluate(prov, _settings())}
    assert sigs["NSE:SGBAUG27-GB"].action == Action.HOLD
