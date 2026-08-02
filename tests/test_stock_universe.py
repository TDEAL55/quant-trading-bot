import stock_universe
from stock_universe import build_stock_universe, load_stock_universe, normalize_symbol


def _asset(symbol: str, *, asset_class: str = "US_EQUITY", status: str = "ACTIVE", tradable: bool = True, name: str | None = None):
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "status": status,
        "tradable": tradable,
        "name": name or symbol,
        "fractionable": True,
    }


def _alpha_symbol(index: int) -> str:
    # Produce 4-letter symbols: AAAA, AAAB, ...
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    a = letters[(index // (26 * 26)) % 26]
    b = letters[(index // 26) % 26]
    c = letters[index % 26]
    return f"A{a}{b}{c}"


def test_universe_symbols_are_unique_and_sorted():
    stock_universe._UNIVERSE_CACHE["records"] = []
    stock_universe._UNIVERSE_CACHE["stats"] = {}
    stock_universe._fetch_alpaca_assets = lambda: [
        _asset("MSFT"),
        _asset("AAPL"),
        _asset("AAPL"),
        _asset("QQQ"),
    ]
    universe = build_stock_universe(max_universe_size=300, include_etfs=True)
    symbols = [item["symbol"] for item in universe]
    assert symbols == sorted(symbols)
    assert len(symbols) == len(set(symbols))


def test_universe_source_calls_alpaca_assets_api(monkeypatch):
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return [_asset("AAPL")]

    monkeypatch.setattr(stock_universe, "_fetch_alpaca_assets", _fetch)
    stock_universe._UNIVERSE_CACHE["market_date"] = ""
    stock_universe._UNIVERSE_CACHE["records"] = []
    rows = load_stock_universe(refresh=True, market_date="2026-08-01", max_universe_size=0)
    assert rows and rows[0]["symbol"] == "AAPL"
    assert calls["n"] == 1


def test_universe_exclusions_and_additions_apply():
    stock_universe._fetch_alpaca_assets = lambda: [_asset("AAPL"), _asset("QQQ"), _asset("MSFT")]
    universe = build_stock_universe(
        selected_universes=["unused"],
        excluded_symbols=["SPY"],
        additional_symbols=["tsla"],
        max_universe_size=50,
    )
    symbols = [item["symbol"] for item in universe]
    assert "TSLA" in symbols
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_universe_respects_max_size():
    stock_universe._fetch_alpaca_assets = lambda: [_asset(_alpha_symbol(i)) for i in range(80)]
    universe = build_stock_universe(max_universe_size=25, include_etfs=True)
    assert len(universe) == 25


def test_zero_max_universe_size_means_no_artificial_cap():
    stock_universe._fetch_alpaca_assets = lambda: [_asset(_alpha_symbol(i)) for i in range(75)]
    universe = build_stock_universe(max_universe_size=0, include_etfs=True)
    assert len(universe) == 75


def test_universe_is_filtered_from_alpaca_assets_api_rules():
    stock_universe._fetch_alpaca_assets = lambda: [
        _asset("AAPL", status="ACTIVE", tradable=True),
        _asset("MSFT", status="INACTIVE", tradable=True),
        _asset("TSLA", status="ACTIVE", tradable=False),
        _asset("BTCUSD", asset_class="CRYPTO", status="ACTIVE", tradable=True),
    ]
    universe = build_stock_universe(max_universe_size=0, include_etfs=True)
    symbols = [item["symbol"] for item in universe]
    assert symbols == ["AAPL"]


def test_cache_refresh_runs_once_per_market_date(monkeypatch):
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return [_asset("AAPL")]

    monkeypatch.setattr(stock_universe, "_fetch_alpaca_assets", _fetch)
    stock_universe._UNIVERSE_CACHE["market_date"] = ""
    stock_universe._UNIVERSE_CACHE["records"] = []
    first = load_stock_universe(refresh=False, market_date="2026-08-01", max_universe_size=0)
    second = load_stock_universe(refresh=False, market_date="2026-08-01", max_universe_size=0)
    third = load_stock_universe(refresh=False, market_date="2026-08-02", max_universe_size=0)

    assert first and second and third
    assert calls["n"] == 2


def test_universe_normalizes_symbols_with_periods():
    assert normalize_symbol("brk.b") == "BRK-B"


def test_universe_metadata_fields_present():
    stock_universe._fetch_alpaca_assets = lambda: [_asset("AAPL")]
    universe = build_stock_universe(selected_universes=["unused"], max_universe_size=10)
    sample = universe[0]
    assert {
        "symbol",
        "company_name",
        "sector",
        "industry",
        "universe_groups",
        "is_etf",
        "benchmark_only",
    }.issubset(set(sample.keys()))
