from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_HORIZONS = [1, 5, 10, 20]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def strategy_fingerprint(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    strategy_name: str
    description: str
    version: str
    enabled: bool
    filter_rules: dict[str, Any]
    ranking_convention: str
    portfolio_configuration: dict[str, Any]
    supported_horizons: list[int]
    created_at: str
    configuration_fingerprint: str


def _definition(
    strategy_id: str,
    strategy_name: str,
    description: str,
    filter_rules: dict[str, Any],
    portfolio_configuration: dict[str, Any],
    version: str = "v1",
    enabled: bool = True,
    ranking_convention: str = "stored_historical_rank_ascending",
    supported_horizons: list[int] | None = None,
) -> StrategyDefinition:
    config_payload = {
        "strategy_id": strategy_id,
        "filter_rules": filter_rules,
        "portfolio_configuration": portfolio_configuration,
        "ranking_convention": ranking_convention,
        "supported_horizons": supported_horizons or DEFAULT_HORIZONS,
        "version": version,
    }
    return StrategyDefinition(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        description=description,
        version=version,
        enabled=bool(enabled),
        filter_rules=filter_rules,
        ranking_convention=ranking_convention,
        portfolio_configuration=portfolio_configuration,
        supported_horizons=list(supported_horizons or DEFAULT_HORIZONS),
        created_at=_utc_iso(),
        configuration_fingerprint=strategy_fingerprint(config_payload),
    )


def built_in_strategy_definitions() -> list[StrategyDefinition]:
    common_portfolio = {
        "top_n": 5,
        "weighting_method": "equal_weight",
        "max_position_weight": 0.30,
        "sector_cap": 0.50,
        "min_holdings": 1,
        "horizon": 20,
        "benchmark": "SPY",
    }
    definitions = [
        _definition(
            "baseline_scanner",
            "Baseline Scanner",
            "Reproduces stored scanner candidate population without rescoring or rank mutation.",
            {
                "required_signals": [],
                "min_overall_score": None,
                "min_confidence": None,
                "max_rank": None,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {},
                "factor_maxs": {},
            },
            dict(common_portfolio),
        ),
        _definition(
            "trend_focused",
            "Trend Focused",
            "Requires stronger trend_score and BUY-oriented signals using stored factor fields.",
            {
                "required_signals": ["BUY", "STRONG_BUY"],
                "min_overall_score": 60.0,
                "min_confidence": 55.0,
                "max_rank": 15,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {"trend_score": 65.0},
                "factor_maxs": {},
            },
            {**common_portfolio, "weighting_method": "score_proportional"},
        ),
        _definition(
            "momentum_focused",
            "Momentum Focused",
            "Prefers strong momentum and top-ranked observations.",
            {
                "required_signals": ["BUY", "STRONG_BUY"],
                "min_overall_score": 58.0,
                "min_confidence": 52.0,
                "max_rank": 20,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {"momentum_score": 62.0},
                "factor_maxs": {},
            },
            {**common_portfolio, "weighting_method": "rank_based"},
        ),
        _definition(
            "quality_risk_focused",
            "Quality Risk Focused",
            "Screens for higher confidence and risk-quality profile.",
            {
                "required_signals": ["BUY", "HOLD", "STRONG_BUY"],
                "min_overall_score": 55.0,
                "min_confidence": 60.0,
                "max_rank": 20,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {"risk_quality_score": 60.0, "liquidity_score": 45.0},
                "factor_maxs": {},
            },
            {**common_portfolio, "weighting_method": "confidence_proportional"},
        ),
        _definition(
            "low_volatility",
            "Low Volatility",
            "Uses stored volatility measure proxies and inverse-vol weighting.",
            {
                "required_signals": [],
                "min_overall_score": 50.0,
                "min_confidence": 50.0,
                "max_rank": 25,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {},
                "factor_maxs": {"volatility_measure": 0.55},
            },
            {**common_portfolio, "weighting_method": "inverse_volatility"},
        ),
        _definition(
            "high_confidence",
            "High Confidence",
            "Selects high-confidence rows while preserving stored rank order.",
            {
                "required_signals": [],
                "min_overall_score": 55.0,
                "min_confidence": 70.0,
                "max_rank": 25,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {},
                "factor_maxs": {},
            },
            dict(common_portfolio),
        ),
        _definition(
            "top_rank",
            "Top Rank",
            "Strictly highest stored-ranked candidates only.",
            {
                "required_signals": [],
                "min_overall_score": None,
                "min_confidence": None,
                "max_rank": 5,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {},
                "factor_maxs": {},
            },
            {**common_portfolio, "weighting_method": "rank_based", "top_n": 5},
        ),
        _definition(
            "bull_regime",
            "Bull Regime",
            "Only bull/strong_bull observations from stored regime tags.",
            {
                "required_signals": ["BUY", "STRONG_BUY", "HOLD"],
                "min_overall_score": 50.0,
                "min_confidence": 50.0,
                "max_rank": 25,
                "permitted_regimes": ["bull", "strong_bull"],
                "permitted_sectors": [],
                "factor_mins": {},
                "factor_maxs": {},
            },
            dict(common_portfolio),
        ),
        _definition(
            "defensive_bear",
            "Defensive Bear",
            "Bear/strong_bear observations with quality and lower-volatility constraints.",
            {
                "required_signals": ["HOLD", "SELL", "BUY"],
                "min_overall_score": 45.0,
                "min_confidence": 50.0,
                "max_rank": 30,
                "permitted_regimes": ["bear", "strong_bear", "unknown"],
                "permitted_sectors": [],
                "factor_mins": {"risk_quality_score": 50.0},
                "factor_maxs": {"volatility_measure": 0.65},
            },
            {**common_portfolio, "top_n": 6, "weighting_method": "equal_weight"},
        ),
        _definition(
            "balanced_multi_factor",
            "Balanced Multi-Factor",
            "Balanced thresholds across trend, momentum, liquidity, and confidence.",
            {
                "required_signals": ["BUY", "STRONG_BUY", "HOLD"],
                "min_overall_score": 58.0,
                "min_confidence": 58.0,
                "max_rank": 20,
                "permitted_regimes": [],
                "permitted_sectors": [],
                "factor_mins": {"trend_score": 55.0, "momentum_score": 55.0, "liquidity_score": 40.0},
                "factor_maxs": {"volatility_measure": 0.70},
            },
            {**common_portfolio, "weighting_method": "score_proportional", "top_n": 6},
        ),
    ]
    validate_strategy_definitions(definitions)
    return definitions


def validate_strategy_definitions(definitions: list[StrategyDefinition]) -> None:
    ids = [item.strategy_id for item in definitions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate strategy_id values are not allowed")
    for definition in definitions:
        rules = dict(definition.filter_rules or {})
        if rules.get("max_rank") is not None and float(rules.get("max_rank")) <= 0:
            raise ValueError(f"invalid max_rank for {definition.strategy_id}")
        for key in ["min_overall_score", "min_confidence"]:
            value = rules.get(key)
            if value is not None and not isinstance(value, (int, float)):
                raise ValueError(f"invalid {key} for {definition.strategy_id}")


def definition_by_id(definitions: list[StrategyDefinition], strategy_id: str) -> StrategyDefinition:
    for definition in definitions:
        if definition.strategy_id == strategy_id:
            return definition
    raise KeyError(f"unknown strategy_id: {strategy_id}")
