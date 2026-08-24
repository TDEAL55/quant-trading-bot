from datetime import date, datetime, timezone

import pandas as pd

from options_market_data import analyze_underlying_bars, parse_option_symbol, select_option_contract


def _bars(step: float) -> pd.DataFrame:
    index = pd.date_range(end="2026-08-24T14:00:00Z", periods=100, freq="15min")
    return pd.DataFrame({"close": [100.0 + (index_value * step) for index_value in range(100)], "volume": [1000] * 100}, index=index)


def test_parse_occ_option_symbol():
    assert parse_option_symbol("SPY260918C00600000") == {
        "symbol": "SPY260918C00600000",
        "underlying_symbol": "SPY",
        "expiration_date": "2026-09-18",
        "contract_type": "call",
        "strike_price": 600.0,
    }


def test_underlying_analysis_emits_directional_call_and_put():
    now = datetime(2026, 8, 24, 14, 1, tzinfo=timezone.utc)
    bullish = analyze_underlying_bars("SPY", _bars(0.4), now=now)
    bearish = analyze_underlying_bars("QQQ", _bars(-0.4), now=now)

    assert bullish["signal"] == "CALL"
    assert bearish["signal"] == "PUT"
    assert bullish["eligible"] is True
    assert bearish["eligible"] is True


def test_contract_selector_prefers_target_delta_and_liquidity():
    contracts = [
        {"symbol": "SPY260918C00600000", "expiration_date": "2026-09-18", "strike_price": 600, "size": 100, "open_interest": 500},
        {"symbol": "SPY260918C00610000", "expiration_date": "2026-09-18", "strike_price": 610, "size": 100, "open_interest": 500},
    ]
    snapshots = {
        "SPY260918C00600000": {"bid": 5.0, "ask": 5.2, "spread_percent": 3.92, "delta": 0.56},
        "SPY260918C00610000": {"bid": 4.0, "ask": 5.5, "spread_percent": 31.58, "delta": 0.35},
    }

    selected = select_option_contract(
        contracts,
        snapshots,
        underlying_price=602,
        target_delta=0.55,
        maximum_spread_percent=35,
        today=date(2026, 8, 24),
    )

    assert selected["symbol"] == "SPY260918C00600000"
    assert selected["contract_multiplier"] == 100
