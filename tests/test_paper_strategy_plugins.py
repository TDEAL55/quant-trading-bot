from __future__ import annotations

from strategies import evaluate_all_strategies


def test_strategy_plugins_emit_expected_fields_and_ids():
    payload = {
        "symbol": "JPM",
        "latest_price": 100.0,
        "overall_score": 82.0,
        "confidence": 70.0,
        "quantum_score": {
            "data_quality_status": "ok",
            "normalized_component_scores": {
                "trend_strength": 70.0,
                "relative_strength": 65.0,
                "momentum_quality": 72.0,
                "volume_confirmation": 68.0,
                "volatility_quality": 60.0,
                "liquidity_quality": 75.0,
                "risk_reward_quality": 66.0,
                "market_regime_alignment": 64.0,
            },
            "risk_reward": {"reward_risk_ratio": 1.8},
            "warnings": [],
            "rejection_reasons": [],
        },
    }
    rows = evaluate_all_strategies(payload)

    ids = {row["strategy_id"] for row in rows}
    assert ids == {
        "trend_momentum_v1",
        "moving_average_trend_v1",
        "short_term_mean_reversion_v1",
        "volume_breakout_v1",
        "bearish_trend_short_v1",
    }
    for row in rows:
        assert row["strategy_version"] == "1.0.0"
        assert row["symbol"] == "JPM"
        assert "entry_reason" in row
        assert "proposed_entry" in row
        assert "stop" in row
        assert "target_or_exit_rule" in row
        assert "market_regime" in row
        assert "quantum_score" in row
        assert "strategy_score" in row
        assert "expected_reward_risk" in row
        assert "data_quality_status" in row


def test_bearish_strategy_emits_sell_only_for_explicit_paper_short(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ALLOW_SHORT_SELLING", "true")
    payload = {
        "symbol": "BEAR",
        "latest_price": 50.0,
        "overall_score": 25.0,
        "confidence": 70.0,
        "trade_side": "SELL",
        "strategy_specific_scores": {
            "bearish_trend_short_v1": {
                "eligible": True,
                "strategy_score": 82.0,
                "confidence": 76.0,
            }
        },
        "quantum_score": {"data_quality_status": "ok", "warnings": [], "rejection_reasons": []},
    }

    rows = evaluate_all_strategies(payload)
    short_signal = next(row for row in rows if row["strategy_id"] == "bearish_trend_short_v1")

    assert short_signal["signal"] == "SELL"
    assert short_signal["stop"] > payload["latest_price"]
