from datetime import datetime, timezone

import pandas as pd

from crypto_market_data import AlpacaCryptoMarketData, analyze_crypto_bars


NOW = datetime(2026, 8, 24, 22, 0, tzinfo=timezone.utc)


def _bars(*, rising: bool) -> pd.DataFrame:
    index = pd.date_range(end=NOW, periods=120, freq="15min", tz="UTC")
    if rising:
        close = [100.0 + (index_value * 0.35) for index_value in range(len(index))]
    else:
        close = [160.0 - (index_value * 0.35) for index_value in range(len(index))]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value * 1.002 for value in close],
            "low": [value * 0.998 for value in close],
            "close": close,
            "volume": [1000.0 + index_value for index_value in range(len(index))],
        },
        index=index,
    )


def test_crypto_analysis_generates_buy_for_fresh_uptrend():
    result = analyze_crypto_bars("BTC/USD", _bars(rising=True), buy_score=55, now=NOW)

    assert result["eligible"] is True
    assert result["signal"] == "BUY"
    assert result["score"] >= 55
    assert result["data_age_minutes"] == 0


def test_crypto_analysis_generates_sell_for_downtrend():
    result = analyze_crypto_bars("ETH/USD", _bars(rising=False), exit_score=45, now=NOW)

    assert result["eligible"] is True
    assert result["signal"] == "SELL"
    assert result["score"] <= 45


def test_crypto_analysis_rejects_stale_or_short_history():
    short = _bars(rising=True).tail(20)
    result = analyze_crypto_bars("SOL/USD", short, now=NOW)

    assert result["eligible"] is False
    assert result["reason"] == "insufficient_crypto_history"


def test_alpaca_crypto_market_data_splits_multi_symbol_frame():
    timestamps = pd.date_range(end=NOW, periods=70, freq="15min", tz="UTC")
    index = pd.MultiIndex.from_product([["BTC/USD", "ETH/USD"], timestamps], names=["symbol", "timestamp"])
    frame = pd.DataFrame({"close": [100.0 + i for i in range(len(index))], "volume": 1000.0}, index=index)

    class _Response:
        df = frame

    class _Client:
        def __init__(self):
            self.requests = []

        def get_crypto_bars(self, request, **_kwargs):
            self.requests.append(request)
            return _Response()

    client = _Client()
    provider = AlpacaCryptoMarketData(client=client)

    result = provider.fetch_bars(["BTC/USD", "ETH/USD"], now=NOW, lookback_bars=60)

    assert set(result) == {"BTC/USD", "ETH/USD"}
    assert len(result["BTC/USD"]) == 60
    assert len(client.requests) == 1
