from __future__ import annotations

from typing import Any


def _status_from_score(score: float | None, high: float, low: float) -> str:
    if score is None:
        return "insufficient_data"
    if score >= high:
        return "strong"
    if score >= low:
        return "acceptable"
    return "unstable"


def build_strategy_scorecard(
    metrics: dict[str, Any],
    walk_forward_summary: dict[str, Any],
    data_quality: dict[str, Any],
    min_windows: int = 2,
) -> dict[str, Any]:
    avg_net_excess = metrics.get("average_net_excess_return")
    pos_rate = metrics.get("positive_net_excess_rate")
    drawdown = metrics.get("maximum_drawdown")
    turnover = metrics.get("average_turnover")
    concentration = metrics.get("average_concentration")
    validation_rate = walk_forward_summary.get("positive_validation_window_rate")
    completed_windows = int(walk_forward_summary.get("completed_windows") or 0)
    sample_count = int(metrics.get("completed_portfolio_count") or 0)

    performance_score = None if avg_net_excess is None else float(avg_net_excess) * 100.0
    consistency_score = None if pos_rate is None else float(pos_rate) * 100.0
    drawdown_score = None if drawdown is None else max(0.0, 100.0 + float(drawdown) * 100.0)
    efficiency_score = None if turnover is None else max(0.0, 100.0 - float(turnover) * 100.0)
    concentration_score = None if concentration is None else max(0.0, 100.0 - float(concentration) * 100.0)
    wf_score = None if validation_rate is None else float(validation_rate) * 100.0
    sample_score = min(sample_count * 5.0, 100.0)
    quality_penalty = sum(int(value) for value in data_quality.values())
    data_quality_score = max(0.0, 100.0 - quality_penalty * 5.0)

    categories = [
        {
            "category": "net out-of-sample performance",
            "score": performance_score,
            "status": _status_from_score(performance_score, 1.0, 0.0),
            "supporting_metrics": {"average_net_excess_return": avg_net_excess},
            "explanation": "Higher average net excess return is favored.",
        },
        {
            "category": "consistency",
            "score": consistency_score,
            "status": _status_from_score(consistency_score, 60.0, 45.0),
            "supporting_metrics": {"positive_net_excess_rate": pos_rate},
            "explanation": "Share of positive net excess observations.",
        },
        {
            "category": "drawdown control",
            "score": drawdown_score,
            "status": _status_from_score(drawdown_score, 90.0, 80.0),
            "supporting_metrics": {"maximum_drawdown": drawdown},
            "explanation": "Lower drawdown maps to higher score.",
        },
        {
            "category": "turnover efficiency",
            "score": efficiency_score,
            "status": _status_from_score(efficiency_score, 75.0, 55.0),
            "supporting_metrics": {"average_turnover": turnover},
            "explanation": "Lower turnover is more efficient under fixed assumptions.",
        },
        {
            "category": "concentration",
            "score": concentration_score,
            "status": _status_from_score(concentration_score, 80.0, 60.0),
            "supporting_metrics": {"average_concentration": concentration},
            "explanation": "Less concentration improves diversification score.",
        },
        {
            "category": "regime robustness",
            "score": wf_score,
            "status": _status_from_score(wf_score, 55.0, 40.0),
            "supporting_metrics": {"positive_validation_window_rate": validation_rate},
            "explanation": "Validation-window positivity used as robustness proxy.",
        },
        {
            "category": "walk-forward stability",
            "score": wf_score,
            "status": "insufficient_data" if completed_windows < min_windows else _status_from_score(wf_score, 55.0, 40.0),
            "supporting_metrics": {"completed_windows": completed_windows},
            "explanation": "Requires minimum completed windows before assigning confidence.",
        },
        {
            "category": "sample sufficiency",
            "score": sample_score,
            "status": _status_from_score(sample_score, 50.0, 20.0),
            "supporting_metrics": {"completed_portfolio_count": sample_count},
            "explanation": "Larger sample count reduces over-interpretation risk.",
        },
        {
            "category": "data quality",
            "score": data_quality_score,
            "status": _status_from_score(data_quality_score, 85.0, 60.0),
            "supporting_metrics": data_quality,
            "explanation": "Penalizes malformed, duplicate, and missing required fields.",
        },
    ]

    composite_inputs = {
        "performance_component": performance_score,
        "stability_component": consistency_score,
        "risk_component": drawdown_score,
        "efficiency_component": efficiency_score,
        "sample_quality_component": (sample_score + data_quality_score) / 2.0,
    }
    valid_components = [float(v) for v in composite_inputs.values() if v is not None]
    composite_score = round(sum(valid_components) / len(valid_components), 6) if valid_components else None

    overall_status = "insufficient_data"
    if composite_score is not None:
        if composite_score >= 70.0:
            overall_status = "strong"
        elif composite_score >= 50.0:
            overall_status = "acceptable"
        else:
            overall_status = "unstable"

    return {
        "categories": categories,
        "composite_inputs": composite_inputs,
        "composite_score": composite_score,
        "overall_status": overall_status,
        "ordering_formula": "mean(performance, stability, risk, efficiency, sample_quality)",
    }
