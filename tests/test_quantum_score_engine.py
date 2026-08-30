from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from continuous_scan_cycle import run_continuous_scan_cycle
from deployment_config import DeploymentConfigError, load_deployment_config
from portfolio_selector import build_portfolio_shortlist
from quantum_score_engine import (
    calculate_quantum_score,
    compute_strategy_specific_scores,
    load_quantum_component_weights,
    rank_scored_candidates,
)


def _frame(rows: int = 260, base: float = 100.0, step: float = 0.1, volume: float = 2_000_000.0) -> pd.DataFrame:
    end = pd.Timestamp.now(tz="UTC").normalize()
    index = pd.bdate_range(end=end, periods=rows)
    close = pd.Series([base + (i * step) for i in range(rows)], index=index, dtype=float)
    return pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def test_quantum_score_is_bounded_0_to_100():
    frame = _frame()
    result = calculate_quantum_score("AAA", frame, frame)
    assert 0.0 <= float(result["final_score"]) <= 100.0
    for value in result["normalized_component_scores"].values():
        assert 0.0 <= float(value) <= 100.0


def test_quantum_weights_must_sum_to_100(monkeypatch):
    monkeypatch.setenv("QUANTUM_WEIGHT_TREND_STRENGTH", "50")
    monkeypatch.setenv("QUANTUM_WEIGHT_RELATIVE_STRENGTH", "50")
    monkeypatch.setenv("QUANTUM_WEIGHT_MOMENTUM_QUALITY", "50")
    with pytest.raises(ValueError, match="sum to 100"):
        load_quantum_component_weights()


def test_missing_data_is_explicitly_penalized():
    short_frame = _frame(rows=20)
    result = calculate_quantum_score("AAA", short_frame, short_frame)
    reasons = set(result["rejection_reasons"])
    assert any(reason.startswith("missing_data:") for reason in reasons)
    assert float(result["missing_data_penalty_total"]) > 0.0


def test_stale_data_cannot_pass_shortlist():
    candidate = {
        "rank": 1,
        "symbol": "STALE",
        "sector": "Unknown",
        "overall_score": 99.0,
        "confidence": 80.0,
        "quantum_score": {
            "rejection_reasons": ["stale_data"],
            "warnings": [],
        },
        "strategy_specific_scores": {
            "trend_momentum_v1": {"strategy_id": "trend_momentum_v1", "eligible": True}
        },
    }
    payload = build_portfolio_shortlist([candidate], current_positions=[], current_cash=10_000.0, portfolio_value=10_000.0)
    assert payload["selected"] == []
    assert any("stale" in str(item.get("reason", "")).lower() for item in payload["rejected"])


def test_overextended_momentum_gets_penalized():
    frame = _frame()
    frame.loc[frame.index[-1], "close"] = float(frame["close"].iloc[-2]) * 1.25
    result = calculate_quantum_score("MOMO", frame, frame)
    momentum = float(result["normalized_component_scores"]["momentum_quality"])
    assert momentum < 80.0
    assert any("overextended" in warning.lower() or "unstable" in warning.lower() for warning in result["warnings"])


def test_weak_liquidity_is_rejected():
    frame = _frame(volume=10_000.0)
    result = calculate_quantum_score("ILLQ", frame, frame)
    assert "average_dollar_volume_below_minimum" in set(result["rejection_reasons"])


def test_invalid_reward_risk_is_rejected():
    frame = _frame(step=0.0)
    frame["high"] = frame["close"]
    frame["low"] = frame["close"]
    result = calculate_quantum_score("FLAT", frame, frame)
    reasons = set(result["rejection_reasons"])
    assert "invalid_reward_risk_structure" in reasons or "reward_risk_below_minimum" in reasons or "risk_reward_invalid" in reasons


def test_deterministic_inputs_produce_deterministic_scores():
    frame = _frame()
    first = calculate_quantum_score("AAA", frame, frame)
    second = calculate_quantum_score("AAA", frame, frame)
    assert first["final_score"] == second["final_score"]
    assert first["normalized_component_scores"] == second["normalized_component_scores"]
    assert first["weighted_contributions"] == second["weighted_contributions"]


def test_strategy_specific_scores_remain_separate():
    frame = _frame()
    quantum = calculate_quantum_score("AAA", frame, frame)
    strategy_scores = compute_strategy_specific_scores(quantum)
    assert set(strategy_scores.keys()) == {
        "stock_trend_ensemble_v2",
        "stock_mean_reversion_v2",
        "stock_bearish_trend_v2",
    }
    values = [float(item["strategy_score"]) for item in strategy_scores.values()]
    assert len(set(values)) >= 2


def test_stock_strategy_sleeves_are_mutually_regime_gated():
    base = {
        "data_quality_status": "ok",
        "risk_reward": {"reward_risk_ratio": 2.0},
        "factor_values": {"momentum_quality": {"rsi14": 60.0}},
        "normalized_component_scores": {
            "trend_strength": 72.0,
            "relative_strength": 65.0,
            "momentum_quality": 68.0,
            "volume_confirmation": 62.0,
            "volatility_quality": 65.0,
            "liquidity_quality": 80.0,
            "risk_reward_quality": 70.0,
            "market_regime_alignment": 76.0,
        },
    }
    bull = compute_strategy_specific_scores({**base, "market_regime": "bull"})
    assert bull["stock_trend_ensemble_v2"]["eligible"] is True
    assert bull["stock_mean_reversion_v2"]["eligible"] is False
    assert bull["stock_bearish_trend_v2"]["eligible"] is False

    sideways = compute_strategy_specific_scores(
        {
            **base,
            "market_regime": "sideways",
            "factor_values": {"momentum_quality": {"rsi14": 30.0}},
            "normalized_component_scores": {
                **base["normalized_component_scores"],
                "trend_strength": 45.0,
                "momentum_quality": 35.0,
                "relative_strength": 45.0,
                "market_regime_alignment": 55.0,
            },
        }
    )
    assert sideways["stock_trend_ensemble_v2"]["eligible"] is False
    assert sideways["stock_mean_reversion_v2"]["eligible"] is True
    assert sideways["stock_bearish_trend_v2"]["eligible"] is False

    weak_bull_oversold = compute_strategy_specific_scores(
        {
            **base,
            "market_regime": "weak_bull",
            "factor_values": {"momentum_quality": {"rsi14": 30.0}},
            "normalized_component_scores": {
                **base["normalized_component_scores"],
                "momentum_quality": 35.0,
            },
        }
    )
    assert weak_bull_oversold["stock_trend_ensemble_v2"]["eligible"] is False
    assert weak_bull_oversold["stock_mean_reversion_v2"]["eligible"] is True
    assert weak_bull_oversold["stock_bearish_trend_v2"]["eligible"] is False


def test_ranking_tie_breaks_are_deterministic():
    base_quantum = {
        "data_quality_status": "ok",
        "risk_reward": {"reward_risk_ratio": 2.0},
    }
    rows = [
        {
            "symbol": "BBB",
            "overall_score": 85.0,
            "component_scores": {"liquidity_quality": 70.0},
            "quantum_score": base_quantum,
            "strategy_specific_scores": {"trend_momentum_v1": {"eligible": True, "strategy_score": 80.0}},
        },
        {
            "symbol": "AAA",
            "overall_score": 85.0,
            "component_scores": {"liquidity_quality": 70.0},
            "quantum_score": base_quantum,
            "strategy_specific_scores": {"trend_momentum_v1": {"eligible": True, "strategy_score": 80.0}},
        },
    ]
    ranked = rank_scored_candidates(rows)
    assert ranked[0]["symbol"] == "AAA"


class _Config:
    trading_mode = "PAPER"
    database_url = "sqlite:///unused.db"
    max_open_positions = 10
    max_position_equity_percent = 10.0


class _BrokerDryRun:
    def __init__(self, mode="PAPER"):
        self.mode = mode

    def get_positions(self):
        return {}

    def get_open_orders(self):
        return []

    def get_buying_power(self):
        return 5000.0

    def get_equity(self):
        return 5000.0

    def submit_order(self, *args, **kwargs):
        raise AssertionError("submit_order should not be called in dry_run")


class _Repo:
    def __init__(self, database_url=None):
        self._existing = None

    def fetch_latest_submitting_run_by_execution_fingerprint(self, execution_fingerprint):
        return self._existing

    def save_validation_run(self, payload):
        return {"run_id": payload.run.get("run_id")}

    def close(self):
        return None


def test_dry_run_never_submits_order():
    def _scan_runner(_universe):
        return {
            "scan_results": [{"symbol": "AAA", "latest_price": 100.0, "overall_score": 90.0, "confidence": 80.0, "eligible": True, "rejection_reasons": []}],
            "ranked_candidates": [{"rank": 1, "symbol": "AAA", "latest_price": 100.0, "overall_score": 90.0, "confidence": 80.0, "eligible": True, "quantum_score": {"rejection_reasons": []}}],
            "summary": {"symbol_count": 1, "success_count": 1, "rejection_count": 0, "error_count": 0, "eligible_count": 1},
        }

    def _shortlist_runner(*_args, **_kwargs):
        return {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 90.0,
                    "confidence": 80.0,
                    "suggested_paper_notional": 500.0,
                    "quantum_score": {"rejection_reasons": []},
                    "strategy_specific_scores": {"trend_momentum_v1": {"strategy_id": "trend_momentum_v1", "strategy_score": 80.0, "eligible": True}},
                    "eligible_strategy_ids": ["trend_momentum_v1"],
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        }

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Config(),
        now_provider=lambda: datetime.now(timezone.utc),
        broker_factory=lambda **kwargs: _BrokerDryRun(),
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database"},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=lambda: ([], 5000.0, 5000.0),
        universe_loader=lambda: [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}],
        persist=True,
        dry_run=True,
    )
    assert result.execution_status in {"completed", "no_trade", "risk_rejected", "duplicate_rejected"}


def test_live_mode_remains_blocked_in_deployment_config(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/qtb.db")
    monkeypatch.setenv("RUN_HOUR", "9")
    monkeypatch.setenv("RUN_MINUTE", "30")
    with pytest.raises(DeploymentConfigError, match="LIVE trading is hard-blocked"):
        load_deployment_config()
