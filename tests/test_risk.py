from sanarb.config import Settings
from sanarb.models import (Action, Instrument, OrderType, Position, Quote, Side,
                           Signal)
from sanarb.engine.risk import RiskGate


def _settings(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _buy_signal(symbol="NSE:X-EQ", edge=0.03, limit=100.0):
    inst = Instrument(symbol, "Test ETF", "etf_intl")
    return Signal(strategy="t", instrument=inst, action=Action.BUY, edge_pct=edge,
                  ref_price=limit, rationale="", side=Side.BUY,
                  order_type=OrderType.LIMIT, limit_price=limit)


def _quote(symbol="NSE:X-EQ", ltp=100.0, bid=99.9, ask=100.1, ask_qty=1000, bid_qty=1000):
    return Quote(symbol=symbol, ltp=ltp, bid=bid, ask=ask, ask_qty=ask_qty, bid_qty=bid_qty)


def test_sizes_within_per_trade_cap():
    s = _settings(capital=10000, per_trade_cap_pct=0.5, reserve_pct=0.0)
    gate = RiskGate(s)
    d = gate.evaluate(_buy_signal(limit=100.0), _quote(), funds=10000, positions=[])
    assert d.approved
    # per-trade cap Rs 5000 / 100 = 50, but book participation caps at 25% of 1000=250
    assert d.order.qty == 50


def test_vetoes_negative_edge():
    gate = RiskGate(_settings())
    d = gate.evaluate(_buy_signal(edge=-0.01), _quote(), funds=10000, positions=[])
    assert not d.approved and "edge" in d.reason


def test_vetoes_when_cannot_afford_one_unit():
    s = _settings(capital=10000, per_trade_cap_pct=0.5)
    gate = RiskGate(s)
    d = gate.evaluate(_buy_signal(limit=9000.0), _quote(ltp=9000, bid=8990, ask=9010),
                      funds=10000, positions=[])
    # per-trade cap is 5000 < 9000 -> cannot afford
    assert not d.approved


def test_liquidity_caps_quantity():
    s = _settings(capital=100000, per_trade_cap_pct=1.0, reserve_pct=0.0,
                  max_book_participation=0.25)
    gate = RiskGate(s)
    # budget allows 1000 units, but only 100 resting on the ask -> cap 25
    d = gate.evaluate(_buy_signal(limit=100.0), _quote(ask_qty=100),
                      funds=100000, positions=[])
    assert d.approved and d.order.qty == 25


def test_vetoes_wide_spread():
    gate = RiskGate(_settings(max_spread_pct=0.005))
    d = gate.evaluate(_buy_signal(limit=100.0), _quote(bid=98.0, ask=102.0),
                      funds=10000, positions=[])
    assert not d.approved and "spread" in d.reason


def test_vetoes_market_order_when_forced_limit():
    gate = RiskGate(_settings(force_limit_orders=True))
    sig = _buy_signal()
    sig = Signal(strategy="t", instrument=sig.instrument, action=Action.BUY,
                 edge_pct=0.03, ref_price=100.0, rationale="", side=Side.BUY,
                 order_type=OrderType.MARKET, limit_price=100.0)
    d = gate.evaluate(sig, _quote(), funds=10000, positions=[])
    assert not d.approved and "market" in d.reason


def test_respects_max_open_positions():
    s = _settings(max_open_positions=1)
    gate = RiskGate(s)
    held = [Position("NSE:OTHER-EQ", "Other", 1, 100.0)]
    d = gate.evaluate(_buy_signal(), _quote(), funds=10000, positions=held)
    assert not d.approved and "max_open_positions" in d.reason


def test_sell_requires_existing_position():
    gate = RiskGate(_settings())
    inst = Instrument("NSE:X-EQ", "Test", "etf_intl")
    sig = Signal(strategy="t", instrument=inst, action=Action.SELL, edge_pct=0.1,
                 ref_price=100.0, rationale="", side=Side.SELL,
                 order_type=OrderType.LIMIT, limit_price=100.0)
    d = gate.evaluate(sig, _quote(), funds=10000, positions=[])
    assert not d.approved
