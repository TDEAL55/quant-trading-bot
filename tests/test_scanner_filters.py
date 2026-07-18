import pandas as pd

from scanner_filters import validate_symbol_data


def _valid_frame(rows=260, price=100.0, volume=1_000_000, slope=0.2):
    end = pd.Timestamp.now(tz="UTC").normalize()
    index = pd.bdate_range(end=end, periods=rows)
    close = pd.Series([price + (i * slope) for i in range(rows)], index=index)
    return pd.DataFrame(
        {
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def test_rejects_low_priced_symbol():
    frame = _valid_frame(price=3.0, slope=0.0)
    result = validate_symbol_data("ABC", frame)
    assert not result["passed"]
    assert any("price below minimum" in reason for reason in result["reasons"])


def test_rejects_illiquid_symbol():
    frame = _valid_frame(price=50.0, volume=50_000)
    result = validate_symbol_data("ABC", frame)
    assert not result["passed"]
    assert any("average dollar volume below minimum" in reason for reason in result["reasons"])


def test_rejects_insufficient_history():
    frame = _valid_frame(rows=100)
    result = validate_symbol_data("ABC", frame)
    assert not result["passed"]
    assert any("insufficient history" in reason for reason in result["reasons"])


def test_rejects_impossible_ohlc_data():
    frame = _valid_frame()
    frame.loc[frame.index[-1], "high"] = frame.loc[frame.index[-1], "low"] - 1
    result = validate_symbol_data("ABC", frame)
    assert not result["passed"]
    assert any("impossible OHLC" in reason for reason in result["reasons"])


def test_accepts_valid_liquid_symbol():
    frame = _valid_frame(price=120.0, volume=2_500_000)
    result = validate_symbol_data("ABC", frame)
    assert result["passed"]
    assert result["metrics"]["average_dollar_volume_20"] > 20_000_000
