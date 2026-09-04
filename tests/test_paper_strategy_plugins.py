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
            "final_score": 82.0,
            "market_regime": "bull",
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
            "factor_values": {
                "trend_strength": {"close": 101.0, "ema20": 100.0, "ema50": 95.0},
                "momentum_quality": {"rsi14": 55.0},
                "volatility_quality": {"atr_pct": 2.0, "realized_volatility_pct": 18.0},
            },
            "warnings": [],
            "rejection_reasons": [],
        },
    }
    rows = evaluate_all_strategies(payload)

    ids = {row["strategy_id"] for row in rows}
    assert ids == {
        "stock_trend_pullback_v3",
        "stock_trend_ensemble_v2",
        "stock_mean_reversion_v2",
    }
    for row in rows:
        assert row["strategy_version"] in {"2.0.0", "3.0.0"}
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


def test_bearish_entry_sleeve_is_retired_even_when_short_is_enabled(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ALLOW_SHORT_SELLING", "true")
    payload = {
        "symbol": "BEAR",
        "latest_price": 50.0,
        "overall_score": 25.0,
        "confidence": 70.0,
        "trade_side": "SELL",
        "strategy_specific_scores": {
            "stock_bearish_trend_v2": {
                "eligible": True,
                "strategy_score": 82.0,
                "confidence": 76.0,
                "strategy_version": "2.0.0",
            }
        },
        "quantum_score": {"data_quality_status": "ok", "market_regime": "bear", "warnings": [], "rejection_reasons": []},
    }

    rows = evaluate_all_strategies(payload)
    assert all(row["signal"] != "SELL" for row in rows)


def test_strategy_plugins_fail_closed_without_quantum_factor_payload():
    rows = evaluate_all_strategies(
        {
            "symbol": "JPM",
            "latest_price": 100.0,
            "overall_score": 99.0,
            "confidence": 99.0,
            "regime": "bull",
        }
    )

    assert all(row["signal"] == "HOLD" for row in rows)
    assert all(row["strategy_score"] == 0.0 for row in rows)
