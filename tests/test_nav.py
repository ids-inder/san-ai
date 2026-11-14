from sanarb.providers.nav import AmfiNavProvider, parse_navall

# A trimmed sample mirroring the real NAVAll.txt layout (8 columns, with the
# Plan/Option columns AMFI now includes; NAV is second-to-last).
SAMPLE = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Motilal Oswal Mutual Fund

114984;INF247L01AP3;-;Motilal Oswal Nasdaq 100 ETF;;;257.3064;13-Nov-2026
147623;INF247L01XX0;-;Some Scheme With N.A.;;;N.A.;13-Nov-2026

Nippon India Mutual Fund

140095;INF204KB14I2;-;Nippon India ETF Hang Seng BeES;;;423.3945;13-Nov-2026
"""


def test_parse_navall_extracts_valid_rows():
    parsed = parse_navall(SAMPLE)
    assert parsed["114984"][0] == 257.3064
    assert parsed["140095"][0] == 423.3945
    # 'N.A.' row is skipped
    assert "147623" not in parsed
    # header / section labels are skipped
    assert all(code.isdigit() for code in parsed)


def test_nav_for_symbol_uses_mapping(monkeypatch):
    prov = AmfiNavProvider(symbol_to_scheme={"NSE:MON100-EQ": "114984"})
    # Avoid network: pre-load the cache as if fetched.
    prov._cache = parse_navall(SAMPLE)
    prov._fetched_at = 10**12  # far future so _ensure() treats it as fresh
    assert prov.nav_for_symbol("NSE:MON100-EQ") == 257.3064
    assert prov.nav_for_symbol("NSE:UNKNOWN-EQ") is None


def test_nav_network_failure_returns_none():
    prov = AmfiNavProvider(symbol_to_scheme={"NSE:MON100-EQ": "147622"},
                           url="https://invalid.invalid/none.txt", timeout=1)
    # _ensure() will raise internally; nav_for_symbol swallows -> None
    assert prov.nav_for_symbol("NSE:MON100-EQ") is None
