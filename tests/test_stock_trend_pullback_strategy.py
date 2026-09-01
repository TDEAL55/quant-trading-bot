from quantum_score_engine import compute_strategy_specific_scores
from pnl_risk_policy import volatility_adjusted_position_percent


def _quantum(*, close=101.0, ema20=100.0, ema50=95.0, rsi=55.0, regime="bull"):
    return {
        "normalized_component_scores": {
            "trend_strength": 75.0,
            "relative_strength": 70.0,
            "momentum_quality": 65.0,
            "volume_confirmation": 55.0,
            "volatility_quality": 70.0,
            "liquidity_quality": 80.0,
            "risk_reward_quality": 70.0,
            "market_regime_alignment": 75.0,
        },
        "market_regime": regime,
        "risk_reward": {"reward_risk_ratio": 1.5},
        "data_quality_status": "ok",
        "factor_values": {
            "trend_strength": {"close": close, "ema20": ema20, "ema50": ema50},
            "momentum_quality": {"rsi14": rsi},
            "volatility_quality": {"atr_pct": 2.0, "realized_volatility_pct": 20.0},
        },
    }


def test_pullback_is_eligible_only_inside_uptrend_entry_zone():
    eligible = compute_strategy_specific_scores(_quantum())["stock_trend_pullback_v3"]
    extended = compute_strategy_specific_scores(_quantum(close=112.0))["stock_trend_pullback_v3"]
    risk_off = compute_strategy_specific_scores(_quantum(regime="high_volatility_risk_off"))["stock_trend_pullback_v3"]
    assert eligible["eligible"] is True
    assert eligible["strategy_version"] == "3.0.0"
    assert extended["eligible"] is False
    assert risk_off["eligible"] is False


def test_volatility_sizing_only_reduces_position_cap():
    calm = {
        "supporting_factors": {
            "factor_values": {"volatility_quality": {"atr_pct": 2.0, "realized_volatility_pct": 18.0}}
        }
    }
    volatile = {
        "supporting_factors": {
            "factor_values": {"volatility_quality": {"atr_pct": 5.0, "realized_volatility_pct": 40.0}}
        }
    }
    assert volatility_adjusted_position_percent(base_position_percent=10.0, strategy_signal=calm) == 10.0
    assert volatility_adjusted_position_percent(base_position_percent=10.0, strategy_signal=volatile) == 5.0
