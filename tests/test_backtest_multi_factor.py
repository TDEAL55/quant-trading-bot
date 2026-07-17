import pandas as pd

import backtest


def _market_frame(size=260, slope=0.5):
    index = pd.date_range("2023-01-01", periods=size, freq="D")
    close = pd.Series([100 + slope * i for i in range(size)], index=index)
    return pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
        },
        index=index,
    )


def test_backtest_multi_factor_uses_only_prior_history(monkeypatch):
    prices = _market_frame(15, slope=0.3)
    observed_lengths = []

    monkeypatch.setattr(backtest, "download_price_data", lambda *args, **kwargs: prices)
    monkeypatch.setattr(backtest, "_benchmark_history", lambda *args, **kwargs: prices)

    def fake_generate_strategy_result(history, **kwargs):
        observed_lengths.append(len(history))
        return {"legacy_signal": "hold", "signal": "HOLD", "overall_score": 50.0, "confidence": 30.0}

    monkeypatch.setattr(backtest, "generate_strategy_result", fake_generate_strategy_result)
    result = backtest.run_backtest("SPY", "2023-01-01", "2023-02-01", strategy_mode="MULTI_FACTOR")

    assert observed_lengths[0] == 0
    assert observed_lengths[-1] == len(prices) - 1
    assert result["strategy_mode"] == "MULTI_FACTOR"


def test_backtest_legacy_strategy_still_works(monkeypatch):
    prices = _market_frame(30, slope=0.1)
    monkeypatch.setattr(backtest, "download_price_data", lambda *args, **kwargs: prices)
    monkeypatch.setattr(backtest, "_benchmark_history", lambda *args, **kwargs: prices)
    monkeypatch.setattr(backtest, "generate_signal", lambda history, short_window, long_window: "hold")

    result = backtest.run_backtest("SPY", "2023-01-01", "2023-02-01", strategy_mode="LEGACY_MA")

    assert result["strategy_mode"] == "LEGACY_MA"
    assert result["number_of_trades"] == 0
    assert "benchmark_return" in result


def test_compare_strategy_modes_returns_legacy_multi_factor_and_buy_hold(monkeypatch):
    prices = _market_frame(40, slope=0.2)
    monkeypatch.setattr(backtest, "download_price_data", lambda *args, **kwargs: prices)
    monkeypatch.setattr(backtest, "_benchmark_history", lambda *args, **kwargs: prices)
    monkeypatch.setattr(backtest, "generate_signal", lambda history, short_window, long_window: "hold")
    monkeypatch.setattr(
        backtest,
        "generate_strategy_result",
        lambda history, **kwargs: {"legacy_signal": "hold", "signal": "HOLD", "overall_score": 50.0, "confidence": 40.0},
    )

    result = backtest.compare_strategy_modes("SPY", "2023-01-01", "2023-02-15")

    assert set(result.keys()) == {"legacy_ma", "multi_factor", "buy_and_hold"}