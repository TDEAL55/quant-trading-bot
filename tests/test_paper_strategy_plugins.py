from __future__ import annotations

from strategies import evaluate_all_strategies


def test_strategy_plugins_emit_expected_fields_and_ids():
    payload = {
        "symbol": "JPM",
        "latest_price": 100.0,
        "overall_score": 82.0,
        "confidence": 70.0,
    }
    rows = evaluate_all_strategies(payload)

    ids = {row["strategy_id"] for row in rows}
    assert ids == {
        "trend_momentum",
        "ma_trend_follow",
        "short_term_mean_reversion",
        "breakout_volume_confirmation",
    }
    for row in rows:
        assert row["strategy_version"] == "1.0.0"
        assert row["symbol"] == "JPM"
        assert "entry_reason" in row
        assert "proposed_entry" in row
        assert "stop" in row
        assert "target_or_exit_rule" in row
        assert "market_regime" in row
