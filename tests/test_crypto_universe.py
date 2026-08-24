from crypto_universe import build_crypto_universe, canonical_crypto_symbol


def test_canonical_crypto_symbol_supports_current_and_legacy_alpaca_symbols():
    assert canonical_crypto_symbol("btc/usd") == "BTC/USD"
    assert canonical_crypto_symbol("BTCUSD") == "BTC/USD"
    assert canonical_crypto_symbol("eth-usd") == "ETH/USD"


def test_crypto_universe_filters_and_prioritizes_usd_pairs():
    assets = [
        {"symbol": "AAVE/USD", "tradable": True},
        {"symbol": "USDT/USD", "tradable": True},
        {"symbol": "ETH/USD", "tradable": True},
        {"symbol": "BTC/USD", "tradable": True},
        {"symbol": "SOL/USD", "tradable": False},
        {"symbol": "BTC/EUR", "tradable": True},
    ]

    rows = build_crypto_universe(assets, maximum_symbols=10)

    assert [row["symbol"] for row in rows] == ["BTC/USD", "ETH/USD", "AAVE/USD"]
    assert all(row["asset_class"] == "crypto" and row["market"] == "24/7" for row in rows)


def test_crypto_universe_include_exclude_and_limit():
    assets = [
        {"symbol": "BTC/USD", "tradable": True},
        {"symbol": "ETH/USD", "tradable": True},
        {"symbol": "SOL/USD", "tradable": True},
    ]

    rows = build_crypto_universe(
        assets,
        included_symbols={"BTC/USD", "ETH/USD"},
        excluded_symbols={"ETH/USD"},
        maximum_symbols=1,
    )

    assert [row["symbol"] for row in rows] == ["BTC/USD"]

