from sanarb.engine.fairvalue import (intl_etf_fair_value, premium_pct,
                                     sgb_fair_value)


def test_fair_value_rolls_nav_forward():
    # NAV 260, +1% overnight index, flat FX -> fair value 262.6
    fv = intl_etf_fair_value(260.0, 0.01, 1.0)
    assert abs(fv - 262.6) < 1e-9


def test_premium_positive_when_above_fair_value():
    assert premium_pct(110.0, 100.0) == 0.10


def test_premium_negative_is_discount():
    assert premium_pct(90.0, 100.0) == -0.10


def test_premium_rejects_nonpositive_fv():
    import pytest
    with pytest.raises(ValueError):
        premium_pct(100.0, 0.0)


def test_timezone_move_not_mistaken_for_edge():
    # Price is 1% above stale NAV, but the index moved +1% overnight.
    # Against fair value the premium should be ~0, not +1%.
    nav, price = 100.0, 101.0
    fv = intl_etf_fair_value(nav, 0.01, 1.0)
    assert abs(premium_pct(price, fv)) < 1e-6


def test_sgb_fair_value_tracks_gold():
    assert sgb_fair_value(9500.0, 1.0) == 9500.0
