import pytest

from sanarb.providers.gold import (FyersMcxGoldProvider, StaticGoldProvider,
                                   resolve_near_month)


def _row(expiry, symbol, root):
    """Build a 21-col MCX-master line with values at the indices we read."""
    c = [""] * 21
    c[8] = str(expiry)
    c[9] = symbol
    c[13] = root
    return ",".join(c)


NOW = 1_800_000_000
MASTER = "\n".join([
    _row(NOW - 10_000, "MCX:GOLDM26NOVFUT", "GOLDM"),   # expired -> ignored
    _row(NOW + 50_000, "MCX:GOLDM26DECFUT", "GOLDM"),   # nearest future
    _row(NOW + 90_000, "MCX:GOLDM27FEBFUT", "GOLDM"),   # later future
    _row(NOW + 20_000, "MCX:GOLDM26DEC100CE", "GOLDM"), # option -> ignored
    _row(NOW + 30_000, "MCX:GOLD26DECFUT", "GOLD"),     # different root
])


def test_resolver_picks_nearest_unexpired_future():
    assert resolve_near_month(MASTER, "GOLDM", now=NOW) == "MCX:GOLDM26DECFUT"


def test_resolver_excludes_options_and_other_roots():
    assert resolve_near_month(MASTER, "GOLD", now=NOW) == "MCX:GOLD26DECFUT"


def test_resolver_raises_when_none():
    with pytest.raises(RuntimeError):
        resolve_near_month(MASTER, "SILVER", now=NOW)


def test_static_gold_provider():
    assert StaticGoldProvider(9500.0).price_per_gram_999() == 9500.0
    with pytest.raises(RuntimeError):
        StaticGoldProvider(0.0).price_per_gram_999()


def test_mcx_conversion_995_per_10g_to_999_per_gram():
    # 995 price 135260 / 10g, converted to 999/g via purity factor.
    gp = FyersMcxGoldProvider(
        quote_ltp=lambda sym: 135260.0,
        explicit_symbol="MCX:GOLDM26DECFUT",   # skip master fetch
        grams_per_quote=10.0,
    )
    px = gp.price_per_gram_999()
    assert abs(px - 13526.0 * (999.0 / 995.0)) < 1e-6
    assert 13500 < px < 13700   # sane 999/gram ballpark


def test_mcx_basis_haircut_applied():
    gp = FyersMcxGoldProvider(
        quote_ltp=lambda sym: 100000.0,
        explicit_symbol="MCX:GOLDM26DECFUT",
        grams_per_quote=10.0,
        purity_factor=1.0,   # isolate the basis effect
        basis_adj=0.01,      # 1% haircut
    )
    assert abs(gp.price_per_gram_999() - 10000.0 * 0.99) < 1e-6


def test_mcx_raises_without_ltp():
    gp = FyersMcxGoldProvider(quote_ltp=lambda sym: None,
                              explicit_symbol="MCX:GOLDM26DECFUT")
    with pytest.raises(RuntimeError):
        gp.price_per_gram_999()
