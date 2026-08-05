import stock_universe
from stock_universe import build_stock_universe, load_stock_universe, normalize_symbol
import time
import pytest


class _EnumLike:
    def __init__(self, value: str, name: str | None = None):
        self.value = value
        self.name = name or value


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


def test_universe_filters_accept_enum_values_and_plain_strings():
    stock_universe._fetch_alpaca_assets = lambda: [
        _asset("AAPL", asset_class=_EnumLike("us_equity", "US_EQUITY"), status=_EnumLike("active", "ACTIVE"), tradable="true"),
        _asset("MSFT", asset_class="US_EQUITY", status="ACTIVE", tradable=True),
    ]
    universe = build_stock_universe(max_universe_size=0, include_etfs=True)
    assert [item["symbol"] for item in universe] == ["AAPL", "MSFT"]


def test_true_empty_alpaca_universe_raises_explicit_error(monkeypatch):
    monkeypatch.setattr(stock_universe, "_fetch_alpaca_assets", lambda: [])
    stock_universe._UNIVERSE_CACHE["stats"] = {}

    with pytest.raises(stock_universe.AlpacaAssetUniverseError, match="returned zero assets"):
        build_stock_universe(max_universe_size=0, include_etfs=True)


def test_fallback_usage_is_reported_in_cache_stats(monkeypatch):
    def _fetch_with_telemetry():
        stock_universe._LAST_ALPACA_FETCH_TELEMETRY = {
            "unfiltered_asset_count": 5,
            "filtered_api_asset_count": 0,
            "client_filtered_asset_count": 2,
            "active_count": 4,
            "tradable_count": 4,
            "us_equity_count": 3,
            "rejected_by_asset_class": 2,
            "rejected_by_status": 1,
            "rejected_non_tradable": 1,
            "rejected_missing_symbol": 0,
            "fallback_used": True,
            "api_exception_type": "",
            "api_request_elapsed_time": 0.12,
        }
        return [_asset("AAPL"), _asset("MSFT")]

    monkeypatch.setattr(stock_universe, "_fetch_alpaca_assets", _fetch_with_telemetry)
    universe = build_stock_universe(max_universe_size=0, include_etfs=True)
    stats = stock_universe.get_universe_cache_stats()

    assert [item["symbol"] for item in universe] == ["AAPL", "MSFT"]
    assert stats["unfiltered_asset_count"] == 5
    assert stats["filtered_api_asset_count"] == 0
    assert stats["client_filtered_asset_count"] == 2
    assert stats["fallback_used"] is True


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


def test_cache_ttl_expiry_forces_refresh(monkeypatch):
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return [_asset("AAPL")]

    monkeypatch.setattr(stock_universe, "_fetch_alpaca_assets", _fetch)
    monkeypatch.setattr(stock_universe, "SCANNER_UNIVERSE_CACHE_TTL_SECONDS", 1)

    stock_universe._UNIVERSE_CACHE["market_date"] = ""
    stock_universe._UNIVERSE_CACHE["records"] = []
    stock_universe._UNIVERSE_CACHE["fetched_at"] = 0.0

    first = load_stock_universe(refresh=False, market_date="2026-08-01", max_universe_size=0)
    second = load_stock_universe(refresh=False, market_date="2026-08-01", max_universe_size=0)
    stock_universe._UNIVERSE_CACHE["fetched_at"] = time.time() - 2.0
    third = load_stock_universe(refresh=False, market_date="2026-08-01", max_universe_size=0)

    assert first and second and third
    assert calls["n"] == 2


def test_unlimited_universe_mode_is_reported_in_cache_stats():
    stock_universe._fetch_alpaca_assets = lambda: [_asset(_alpha_symbol(i)) for i in range(3)]
    stock_universe._UNIVERSE_CACHE["market_date"] = ""
    stock_universe._UNIVERSE_CACHE["records"] = []
    stock_universe._UNIVERSE_CACHE["fetched_at"] = 0.0
    load_stock_universe(refresh=True, market_date="2026-08-01", max_universe_size=0)
    stats = stock_universe.get_universe_cache_stats()
    assert stats["max_universe_mode"] == "unlimited"
    assert stats["max_universe_size"] == 0
