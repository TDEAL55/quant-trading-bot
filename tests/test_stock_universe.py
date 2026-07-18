from stock_universe import build_stock_universe, normalize_symbol


def test_universe_symbols_are_unique_and_sorted():
    universe = build_stock_universe(max_universe_size=300)
    symbols = [item["symbol"] for item in universe]
    assert symbols == sorted(symbols)
    assert len(symbols) == len(set(symbols))


def test_universe_exclusions_and_additions_apply():
    universe = build_stock_universe(
        selected_universes=["benchmarks"],
        excluded_symbols=["SPY"],
        additional_symbols=["aapl", "msft"],
        max_universe_size=50,
    )
    symbols = [item["symbol"] for item in universe]
    assert "SPY" not in symbols
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_universe_respects_max_size():
    universe = build_stock_universe(max_universe_size=25)
    assert len(universe) == 25


def test_universe_normalizes_symbols_with_periods():
    assert normalize_symbol("brk.b") == "BRK-B"


def test_universe_metadata_fields_present():
    universe = build_stock_universe(selected_universes=["benchmarks"], max_universe_size=10)
    sample = universe[0]
    assert set(sample.keys()) == {
        "symbol",
        "company_name",
        "sector",
        "industry",
        "universe_groups",
        "is_etf",
        "benchmark_only",
    }
