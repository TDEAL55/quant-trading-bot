from pathlib import Path

import pandas as pd
import pytest

import factor_engine
import strategy


def _frame_from_close(close_values, volume_values=None):
    index = pd.date_range("2024-01-01", periods=len(close_values), freq="D")
    close = pd.Series(close_values, index=index, dtype=float)
    frame = pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
        },
        index=index,
    )
    if volume_values is not None:
        frame["volume"] = pd.Series(volume_values, index=index, dtype=float)
    return frame


def test_trend_factor_scores_strong_bullish_alignment():
    frame = _frame_from_close([100 + i * 0.8 for i in range(260)], [1_000_000] * 260)
    result = factor_engine.trend_factor(frame)
    assert result.available is True
    assert result.score >= 80
    assert result.status == "bullish"


def test_trend_factor_handles_insufficient_history():
    frame = _frame_from_close([100 + i for i in range(40)], [1_000_000] * 40)
    result = factor_engine.trend_factor(frame)
    assert result.available is False
    assert result.score == 50.0


def test_momentum_factor_penalizes_overextended_rsi():
    close = [100 + i * 0.15 for i in range(80)] + [125, 131, 137, 143, 149, 154]
    frame = _frame_from_close(close, [1_000_000] * len(close))
    result = factor_engine.momentum_factor(frame)
    assert result.available is True
    assert any(
        "overextended" in reason.lower() or "extended" in reason.lower() for reason in result.negative_reasons + result.warnings
    )


def test_volume_factor_returns_unavailable_without_volume():
    frame = _frame_from_close([100 + i * 0.2 for i in range(60)])
    result = factor_engine.volume_factor(frame)
    assert result.available is False
    assert result.score == 50.0


def test_volume_factor_rewards_confirmed_breakout_volume():
    close = [100 + i * 0.1 for i in range(39)] + [110]
    volume = [1_000_000] * 39 + [1_900_000]
    frame = _frame_from_close(close, volume)
    result = factor_engine.volume_factor(frame)
    assert result.available is True
    assert result.score > 60


def test_volatility_factor_penalizes_extreme_volatility():
    close = [100, 102, 97, 105, 92, 108, 90, 110, 88, 115] * 7
    frame = _frame_from_close(close[:70], [1_000_000] * 70)
    result = factor_engine.volatility_factor(frame)
    assert result.available is True
    assert result.score < 50


def test_volatility_factor_penalizes_near_zero_movement():
    close = [100 + ((i % 2) * 0.01) for i in range(60)]
    frame = _frame_from_close(close, [1_000_000] * 60)
    result = factor_engine.volatility_factor(frame)
    assert result.available is True
    assert result.score < 70


def test_market_regime_identifies_bull_and_bear_conditions():
    bull = factor_engine.market_regime_factor(_frame_from_close([100 + i for i in range(260)], [1_000_000] * 260))
    bear = factor_engine.market_regime_factor(_frame_from_close([360 - i for i in range(260)], [1_000_000] * 260))
    assert bull.status in {"strong_bull", "weak_bull"}
    assert bear.status in {"strong_bear", "weak_bear"}


def test_market_regime_identifies_high_volatility_risk_off():
    close = [100, 108, 94, 111, 90, 116, 87, 120, 85, 123] * 26
    frame = _frame_from_close(close[:260], [1_000_000] * 260)
    result = factor_engine.market_regime_factor(frame)
    assert result.status == "high_volatility_risk_off"


def test_risk_quality_factor_penalizes_large_drawdown():
    close = [100 + i * 0.5 for i in range(50)] + [120, 118, 115, 112, 109, 105, 101, 98, 95, 92, 90, 89]
    frame = _frame_from_close(close, [1_000_000] * len(close))
    result = factor_engine.risk_quality_factor(frame)
    assert result.available is True
    assert result.score < 65


def test_composite_engine_reweights_missing_factors_and_is_deterministic():
    frame = _frame_from_close([100 + i * 0.6 for i in range(260)])
    weights = {
        "trend": 0.30,
        "momentum": 0.20,
        "volume": 0.15,
        "volatility": 0.10,
        "market_regime": 0.15,
        "risk_quality": 0.10,
    }
    thresholds = {"strong_buy": 80.0, "buy": 65.0, "hold": 45.0, "reduce": 30.0}
    first = factor_engine.score_symbol(frame, frame, "SPY", weights, thresholds)
    second = factor_engine.score_symbol(frame, frame, "SPY", weights, thresholds)
    assert first == second
    assert abs(sum(first["weights_used"].values()) - 1.0) < 1e-9


def test_invalid_weights_fail_clearly():
    with pytest.raises(ValueError, match="sum to 1.0"):
        factor_engine.score_symbol(
            _frame_from_close([100 + i for i in range(260)], [1_000_000] * 260),
            None,
            "SPY",
            {"trend": 1.2, "momentum": 0.2, "volume": 0.1, "volatility": 0.1, "market_regime": 0.1, "risk_quality": 0.1},
            {"strong_buy": 80.0, "buy": 65.0, "hold": 45.0, "reduce": 30.0},
        )


def test_confidence_penalizes_factor_disagreement_and_threshold_proximity():
    bullish = _frame_from_close([100 + i * 0.9 for i in range(260)], [1_000_000] * 260)
    choppy = _frame_from_close([100 + ((-1) ** i) * (i % 5) for i in range(260)], [1_000_000] * 260)
    default_weights = {"trend": 0.30, "momentum": 0.20, "volume": 0.15, "volatility": 0.10, "market_regime": 0.15, "risk_quality": 0.10}
    thresholds = {"strong_buy": 80.0, "buy": 65.0, "hold": 45.0, "reduce": 30.0}
    strong = factor_engine.score_symbol(bullish, bullish, "SPY", default_weights, thresholds)
    mixed = factor_engine.score_symbol(choppy, bullish, "SPY", default_weights, thresholds)
    assert strong["confidence"] >= mixed["confidence"]


def test_signal_classification_and_hysteresis_behavior():
    thresholds = {"strong_buy": 80.0, "buy": 65.0, "hold": 45.0, "reduce": 30.0}
    assert factor_engine._classify_signal(85.0, thresholds) == "STRONG_BUY"
    assert factor_engine._classify_signal(70.0, thresholds) == "BUY"
    assert factor_engine._classify_signal(50.0, thresholds) == "HOLD"
    assert factor_engine._classify_signal(35.0, thresholds) == "REDUCE"
    assert factor_engine._classify_signal(20.0, thresholds) == "EXIT"
    assert factor_engine._classify_signal(64.2, thresholds, previous_signal="BUY", hysteresis_buffer=2.5) == "BUY"
    assert factor_engine._classify_signal(82.0, thresholds, previous_signal=None, hysteresis_buffer=2.5) == "STRONG_BUY"


def test_strategy_result_returns_hold_for_insufficient_history():
    frame = _frame_from_close([100 + i for i in range(80)], [1_000_000] * 80)
    result = strategy.generate_strategy_result(frame, strategy_mode="MULTI_FACTOR")
    assert result["signal"] == "HOLD"
    assert result["confidence"] <= 35.0
    assert result["warnings"]


def test_factor_engine_has_no_order_submission_capability():
    module_text = Path("factor_engine.py").read_text(encoding="utf-8")
    assert "submit_order" not in module_text
    assert "paper_broker" not in module_text