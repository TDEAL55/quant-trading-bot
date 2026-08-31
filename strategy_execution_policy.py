from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Iterable, Mapping


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class StrategyExecutionPolicySettings:
    minimum_ready_sample: int = 30
    minimum_expectancy: float = 0.0
    minimum_profit_factor: float = 1.2
    probation_max_position_percent: float = 10.0
    shadow_strategy_ids: tuple[str, ...] = ("trend_momentum_v1",)

    def validate(self) -> None:
        if self.minimum_ready_sample < 1:
            raise ValueError("minimum_ready_sample must be positive")
        if self.minimum_profit_factor < 0:
            raise ValueError("minimum_profit_factor cannot be negative")
        if not 0 < self.probation_max_position_percent <= 100:
            raise ValueError("probation_max_position_percent must be in (0, 100]")


def settings_from_environment(environ: Mapping[str, str] | None = None) -> StrategyExecutionPolicySettings:
    env = dict(environ or os.environ)
    shadow = tuple(
        sorted(
            {
                item.strip()
                for item in str(env.get("PAPER_SHADOW_STRATEGY_IDS", "trend_momentum_v1")).split(",")
                if item.strip()
            }
        )
    )
    settings = StrategyExecutionPolicySettings(
        minimum_ready_sample=_as_int(env.get("PAPER_STRATEGY_MINIMUM_READY_SAMPLE"), 30),
        minimum_expectancy=_as_float(env.get("PAPER_STRATEGY_MINIMUM_EXPECTANCY"), 0.0),
        minimum_profit_factor=_as_float(env.get("PAPER_STRATEGY_MINIMUM_PROFIT_FACTOR"), 1.2),
        probation_max_position_percent=_as_float(env.get("PAPER_PROBATION_MAX_POSITION_PERCENT"), 10.0),
        shadow_strategy_ids=shadow,
    )
    settings.validate()
    return settings


def evaluate_strategy_execution_policy(
    strategy_id: str,
    leaderboard: Iterable[Mapping[str, Any]] | None,
    *,
    settings: StrategyExecutionPolicySettings | None = None,
) -> dict[str, Any]:
    """Return a PAPER execution state without changing the strategy signal."""
    policy = settings or StrategyExecutionPolicySettings()
    policy.validate()
    normalized_id = str(strategy_id or "").strip()
    if normalized_id in set(policy.shadow_strategy_ids):
        return {
            "strategy_id": normalized_id,
            "state": "SHADOW",
            "execution_allowed": False,
            "reason": "explicit_shadow_strategy",
            "max_position_percent": 0.0,
            "evidence": {},
        }

    evidence = next(
        (dict(row or {}) for row in list(leaderboard or []) if str((row or {}).get("strategy_id") or "").strip() == normalized_id),
        {},
    )
    completed = _as_int(evidence.get("completed_trade_count"), 0)
    expectancy = _as_float(evidence.get("expectancy"), 0.0)
    profit_factor = _as_float(evidence.get("profit_factor"), 0.0)
    ready = bool(
        completed >= policy.minimum_ready_sample
        and str(evidence.get("sample_status") or "").strip().upper() == "READY"
    )
    if not ready:
        return {
            "strategy_id": normalized_id,
            "state": "PROBATION",
            "execution_allowed": True,
            "reason": "collecting_paper_evidence",
            "max_position_percent": policy.probation_max_position_percent,
            "evidence": evidence,
        }
    if expectancy <= policy.minimum_expectancy or profit_factor < policy.minimum_profit_factor:
        return {
            "strategy_id": normalized_id,
            "state": "SHADOW",
            "execution_allowed": False,
            "reason": "nonpositive_or_weak_ready_sample",
            "max_position_percent": 0.0,
            "evidence": evidence,
        }
    return {
        "strategy_id": normalized_id,
        "state": "ACTIVE",
        "execution_allowed": True,
        "reason": "positive_ready_paper_sample",
        "max_position_percent": 100.0,
        "evidence": evidence,
    }
