from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from statistics import median
from typing import Any


TRADE_MEMORY_VERSION = "trade_memory_v1"
STRATEGY_LEADERBOARD_VERSION = "strategy_leaderboard_v2"
REGIME_VERSION = "regime_v2"
FACTOR_EFFECTIVENESS_VERSION = "factor_effectiveness_v1"
WEIGHT_RECOMMENDATION_VERSION = "weight_reco_v1"
STATE_RECOMMENDATION_VERSION = "strategy_state_reco_v1"
ALLOCATION_RECOMMENDATION_VERSION = "allocation_reco_v1"
REPORT_VERSION = "intelligence_report_v1"

STRATEGY_STATES = {"ACTIVE", "WATCH", "REDUCED", "PAUSED", "RETIRED", "EXPERIMENTAL"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_div(a: float, b: float) -> float:
    if abs(b) <= 1e-12:
        return 0.0
    return float(a) / float(b)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def _sortino(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    downside = [value for value in values if value < 0]
    if not downside:
        return 0.0
    downside_std = _std(downside)
    return _safe_div(mean, downside_std)


def _max_drawdown_from_pnl(pnl: list[float]) -> float:
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in pnl:
        running += float(value)
        peak = max(peak, running)
        if peak > 0:
            max_dd = max(max_dd, (peak - running) / peak)
    return max_dd


def _consecutive_streaks(pnl: list[float]) -> tuple[int, int]:
    best_wins = 0
    best_losses = 0
    cur_wins = 0
    cur_losses = 0
    for value in pnl:
        if value > 0:
            cur_wins += 1
            cur_losses = 0
        elif value < 0:
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = 0
            cur_losses = 0
        best_wins = max(best_wins, cur_wins)
        best_losses = max(best_losses, cur_losses)
    return best_wins, best_losses


def _score_bucket(score: float) -> str:
    value = max(0.0, min(100.0, float(score)))
    lower = int(value // 20) * 20
    upper = min(lower + 20, 100)
    if lower == 100:
        lower = 80
        upper = 100
    return f"{lower}-{upper}"


def _stable_id(parts: list[str], length: int = 32) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def is_completed_trade_status(order_status: str | None) -> bool:
    status = str(order_status or "").strip().lower()
    return status in {"filled", "closed"}


def build_trade_memory_record(
    closed_trade: dict[str, Any],
    *,
    broker_order_ids: list[str] | None = None,
    client_order_ids: list[str] | None = None,
    source_order_status: str | None = "filled",
    quantum_score: dict[str, Any] | None = None,
    strategy_signal: dict[str, Any] | None = None,
    benchmark_return_during_trade: float = 0.0,
    sector: str = "Unknown",
    industry: str = "Unknown",
    execution_mode: str = "ALPACA_PAPER",
) -> dict[str, Any] | None:
    if not is_completed_trade_status(source_order_status):
        return None

    trade_id = str(closed_trade.get("trade_id") or "")
    if not trade_id:
        return None

    quantum = dict(quantum_score or {})
    signal = dict(strategy_signal or {})

    entry_ts = str(closed_trade.get("entry_timestamp") or "")
    exit_ts = str(closed_trade.get("exit_timestamp") or "")
    if not entry_ts or not exit_ts:
        return None

    record = {
        "trade_memory_id": _stable_id([TRADE_MEMORY_VERSION, trade_id, str(execution_mode)]),
        "trade_id": trade_id,
        "run_id": str(closed_trade.get("run_id") or ""),
        "broker_order_ids": sorted({str(item) for item in (broker_order_ids or []) if str(item).strip()}),
        "client_order_ids": sorted({str(item) for item in (client_order_ids or []) if str(item).strip()}),
        "symbol": str(closed_trade.get("symbol") or "").upper(),
        "strategy_id": str(closed_trade.get("strategy_id") or "unknown"),
        "strategy_version": str(closed_trade.get("strategy_version") or "unknown"),
        "quantum_score_version": str(quantum.get("score_version") or "quantum_v1"),
        "quantum_score_at_entry": _safe_float(quantum.get("final_score"), 0.0),
        "strategy_specific_score": _safe_float(signal.get("strategy_score"), 0.0),
        "factor_values": dict(quantum.get("factor_values") or {}),
        "component_scores": dict(quantum.get("normalized_component_scores") or {}),
        "factor_weights": dict(quantum.get("component_weights") or {}),
        "entry_timestamp": entry_ts,
        "exit_timestamp": exit_ts,
        "entry_price": _safe_float(closed_trade.get("entry_price"), 0.0),
        "exit_price": _safe_float(closed_trade.get("exit_price"), 0.0),
        "quantity": _safe_float(closed_trade.get("quantity"), 0.0),
        "realized_gross_pnl": _safe_float(closed_trade.get("realized_gross_pnl"), 0.0),
        "estimated_fees": _safe_float(closed_trade.get("estimated_fees"), 0.0),
        "estimated_slippage": _safe_float(closed_trade.get("estimated_slippage"), 0.0),
        "net_pnl": _safe_float(closed_trade.get("net_pnl"), 0.0),
        "percentage_return": _safe_float(closed_trade.get("percentage_return"), 0.0),
        "holding_duration_hours": _safe_float(closed_trade.get("holding_duration_hours"), 0.0),
        "max_adverse_excursion": _safe_float(closed_trade.get("max_adverse_excursion"), 0.0),
        "max_favorable_excursion": _safe_float(closed_trade.get("max_favorable_excursion"), 0.0),
        "market_regime_entry": str(signal.get("market_regime") or quantum.get("market_regime") or closed_trade.get("market_regime") or "unknown"),
        "market_regime_exit": str(closed_trade.get("market_regime") or signal.get("market_regime") or quantum.get("market_regime") or "unknown"),
        "benchmark_return_during_trade": float(benchmark_return_during_trade),
        "sector": str(closed_trade.get("sector") or sector or "Unknown"),
        "industry": str(closed_trade.get("industry") or industry or "Unknown"),
        "entry_reason": str(signal.get("entry_reason") or closed_trade.get("entry_reason") or "strategy_signal"),
        "exit_reason": str(closed_trade.get("exit_reason") or "strategy_exit"),
        "stop_level": _safe_float(signal.get("stop"), 0.0),
        "target_level": _safe_float(signal.get("target") or signal.get("target_or_exit_rule"), 0.0),
        "confidence": _safe_float(signal.get("confidence"), _safe_float(quantum.get("final_score"), 0.0)),
        "data_quality_status": str(quantum.get("data_quality_status") or "unknown"),
        "execution_mode": str(execution_mode),
        "completed_only": True,
        "source_order_status": str(source_order_status or ""),
        "created_at": _utc_iso(),
    }
    return record


def build_strategy_leaderboard(trade_memory: list[dict[str, Any]], minimum_sample: int = 30) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in trade_memory:
        key = (str(row.get("strategy_id") or "unknown"), str(row.get("strategy_version") or "unknown"))
        grouped.setdefault(key, []).append(dict(row))

    results: list[dict[str, Any]] = []
    for (strategy_id, strategy_version), rows in sorted(grouped.items()):
        rows_sorted = sorted(rows, key=lambda item: str(item.get("exit_timestamp") or ""))
        pnl = [_safe_float(item.get("net_pnl"), 0.0) for item in rows_sorted]
        returns = [_safe_float(item.get("percentage_return"), 0.0) for item in rows_sorted]
        winners = [value for value in pnl if value > 0]
        losers = [value for value in pnl if value < 0]

        win_rate = _safe_div(len(winners), len(pnl))
        loss_rate = _safe_div(len(losers), len(pnl))
        gross_profit = sum(winners)
        gross_loss = abs(sum(losers))
        net_profit = sum(pnl)
        avg_return = _safe_div(sum(returns), len(returns))
        med_return = median(returns) if returns else 0.0
        expectancy = _safe_div(net_profit, len(pnl))
        std_ret = _std(returns)
        sharpe = _safe_div(avg_return, std_ret)
        sortino = _sortino(returns)
        max_dd = _max_drawdown_from_pnl(pnl)
        avg_winner = _safe_div(sum(winners), len(winners)) if winners else 0.0
        avg_loser = _safe_div(sum(losers), len(losers)) if losers else 0.0
        payoff_ratio = _safe_div(abs(avg_winner), abs(avg_loser)) if avg_loser != 0 else 0.0
        avg_hold = _safe_div(sum(_safe_float(item.get("holding_duration_hours"), 0.0) for item in rows_sorted), len(rows_sorted))
        best_trade = max(pnl) if pnl else 0.0
        worst_trade = min(pnl) if pnl else 0.0
        cons_wins, cons_losses = _consecutive_streaks(pnl)

        by_regime: dict[str, dict[str, float]] = {}
        by_sector: dict[str, dict[str, float]] = {}
        by_score_bucket: dict[str, dict[str, float]] = {}
        for item in rows_sorted:
            regime = str(item.get("market_regime_entry") or "unknown")
            sector = str(item.get("sector") or "Unknown")
            bucket = _score_bucket(_safe_float(item.get("quantum_score_at_entry"), 0.0))
            for key, grouping in ((regime, by_regime), (sector, by_sector), (bucket, by_score_bucket)):
                payload = grouping.setdefault(key, {"sample_count": 0.0, "win_rate": 0.0, "average_return": 0.0, "expectancy": 0.0, "profit_factor": 0.0, "pnl": []})
                payload["sample_count"] += 1
                payload["pnl"].append(_safe_float(item.get("net_pnl"), 0.0))
                payload.setdefault("returns", []).append(_safe_float(item.get("percentage_return"), 0.0))

        def _finalize(grouping: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
            result: dict[str, dict[str, float]] = {}
            for key, payload in sorted(grouping.items()):
                gp = sum(value for value in payload["pnl"] if value > 0)
                gl = abs(sum(value for value in payload["pnl"] if value < 0))
                wins = len([value for value in payload["pnl"] if value > 0])
                returns_local = payload.get("returns", [])
                result[key] = {
                    "sample_count": float(payload["sample_count"]),
                    "win_rate": _safe_div(wins, payload["sample_count"]),
                    "average_return": _safe_div(sum(returns_local), len(returns_local)) if returns_local else 0.0,
                    "expectancy": _safe_div(sum(payload["pnl"]), payload["sample_count"]),
                    "profit_factor": _safe_div(gp, gl),
                }
            return result

        recent20 = rows_sorted[-20:]
        recent60 = rows_sorted[-60:]

        def _window_metrics(window_rows: list[dict[str, Any]]) -> dict[str, float]:
            values = [_safe_float(item.get("net_pnl"), 0.0) for item in window_rows]
            returns_local = [_safe_float(item.get("percentage_return"), 0.0) for item in window_rows]
            gp = sum(v for v in values if v > 0)
            gl = abs(sum(v for v in values if v < 0))
            return {
                "sample_count": float(len(window_rows)),
                "net_profit": float(sum(values)),
                "expectancy": _safe_div(sum(values), len(values)) if values else 0.0,
                "win_rate": _safe_div(len([v for v in values if v > 0]), len(values)) if values else 0.0,
                "profit_factor": _safe_div(gp, gl),
                "average_return": _safe_div(sum(returns_local), len(returns_local)) if returns_local else 0.0,
            }

        sample_status = "INSUFFICIENT_SAMPLE" if len(rows_sorted) < int(minimum_sample) else "READY"

        results.append(
            {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "completed_trade_count": len(rows_sorted),
                "win_rate": round(win_rate, 6),
                "loss_rate": round(loss_rate, 6),
                "average_return": round(avg_return, 6),
                "median_return": round(float(med_return), 6),
                "gross_profit": round(gross_profit, 6),
                "gross_loss": round(gross_loss, 6),
                "net_profit": round(net_profit, 6),
                "profit_factor": round(_safe_div(gross_profit, gross_loss), 6),
                "expectancy": round(expectancy, 6),
                "sharpe_ratio": round(sharpe, 6),
                "sortino_ratio": round(sortino, 6),
                "maximum_drawdown": round(max_dd, 6),
                "average_winner": round(avg_winner, 6),
                "average_loser": round(avg_loser, 6),
                "payoff_ratio": round(payoff_ratio, 6),
                "average_holding_time": round(avg_hold, 6),
                "best_trade": round(best_trade, 6),
                "worst_trade": round(worst_trade, 6),
                "consecutive_wins": int(cons_wins),
                "consecutive_losses": int(cons_losses),
                "performance_by_regime": _finalize(by_regime),
                "performance_by_sector": _finalize(by_sector),
                "performance_by_score_range": _finalize(by_score_bucket),
                "recent_20": _window_metrics(recent20),
                "recent_60": _window_metrics(recent60),
                "full_history": _window_metrics(rows_sorted),
                "sample_status": sample_status,
                "leaderboard_version": STRATEGY_LEADERBOARD_VERSION,
            }
        )
    return results


def classify_market_regime(
    evidence: dict[str, Any],
    *,
    regime_version: str = REGIME_VERSION,
) -> dict[str, Any]:
    spy_vs_ema20 = _safe_float(evidence.get("spy_vs_ema20_pct"), 0.0)
    spy_vs_ema50 = _safe_float(evidence.get("spy_vs_ema50_pct"), 0.0)
    spy_vs_ema200 = _safe_float(evidence.get("spy_vs_ema200_pct"), 0.0)
    slope20 = _safe_float(evidence.get("ema20_slope_pct"), 0.0)
    slope50 = _safe_float(evidence.get("ema50_slope_pct"), 0.0)
    slope200 = _safe_float(evidence.get("ema200_slope_pct"), 0.0)
    momentum20 = _safe_float(evidence.get("benchmark_momentum_20d_pct"), 0.0)
    realized_vol = _safe_float(evidence.get("realized_volatility_pct"), 0.0)
    atr_pct = _safe_float(evidence.get("atr_pct"), 0.0)
    breadth_above_200 = _safe_float(evidence.get("breadth_above_200_pct"), 50.0)
    sector_participation = _safe_float(evidence.get("sector_participation_pct"), 50.0)
    drawdown = _safe_float(evidence.get("drawdown_from_high_pct"), 0.0)

    warnings: list[str] = []

    regime_score = 50.0
    regime_score += 8.0 if spy_vs_ema20 > 0 else -8.0
    regime_score += 10.0 if spy_vs_ema50 > 0 else -10.0
    regime_score += 12.0 if spy_vs_ema200 > 0 else -12.0
    regime_score += max(min(slope20 * 40.0, 6.0), -6.0)
    regime_score += max(min(slope50 * 35.0, 6.0), -6.0)
    regime_score += max(min(slope200 * 30.0, 5.0), -5.0)
    regime_score += max(min(momentum20 * 1.2, 8.0), -8.0)
    regime_score += max(min((breadth_above_200 - 50.0) * 0.3, 10.0), -10.0)
    regime_score += max(min((sector_participation - 50.0) * 0.2, 6.0), -6.0)

    vol_penalty = 0.0
    if realized_vol > 40 or atr_pct > 6:
        vol_penalty += 18.0
        warnings.append("elevated volatility")
    elif realized_vol > 28 or atr_pct > 4:
        vol_penalty += 8.0

    regime_score -= vol_penalty

    if drawdown <= -20:
        regime_id = "panic_risk_off"
    elif drawdown <= -12 and momentum20 > 0:
        regime_id = "recovery"
    elif regime_score >= 80:
        regime_id = "strong_bull"
    elif regime_score >= 65:
        regime_id = "normal_bull"
    elif regime_score >= 55:
        regime_id = "weak_bull"
    elif regime_score >= 45:
        regime_id = "sideways"
    elif regime_score >= 35:
        regime_id = "high_volatility_sideways"
    elif regime_score >= 20:
        regime_id = "weak_bear"
    else:
        regime_id = "strong_bear"

    confidence = max(0.0, min(100.0, abs(regime_score - 50.0) * 2.0))

    return {
        "regime_id": regime_id,
        "regime_version": regime_version,
        "inputs": dict(evidence),
        "score": max(0.0, min(100.0, regime_score)),
        "confidence": confidence,
        "warnings": warnings,
        "calculation_timestamp": _utc_iso(),
    }


def build_strategy_regime_metrics(
    trade_memory: list[dict[str, Any]],
    *,
    min_samples: int = 30,
    max_acceptable_drawdown: float = 0.20,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in trade_memory:
        key = (
            str(row.get("strategy_id") or "unknown"),
            str(row.get("strategy_version") or "unknown"),
            str(row.get("market_regime_entry") or "unknown"),
        )
        grouped.setdefault(key, []).append(dict(row))

    rows: list[dict[str, Any]] = []
    for (strategy_id, strategy_version, regime_id), items in sorted(grouped.items()):
        items = sorted(items, key=lambda item: str(item.get("exit_timestamp") or ""))
        pnl = [_safe_float(item.get("net_pnl"), 0.0) for item in items]
        returns = [_safe_float(item.get("percentage_return"), 0.0) for item in items]
        recent = pnl[-20:]
        earlier = pnl[-40:-20] if len(pnl) >= 40 else pnl[:-20]
        recent_expectancy = _safe_div(sum(recent), len(recent)) if recent else 0.0
        earlier_expectancy = _safe_div(sum(earlier), len(earlier)) if earlier else recent_expectancy
        degradation = recent_expectancy - earlier_expectancy
        expectancy = _safe_div(sum(pnl), len(pnl)) if pnl else 0.0
        drawdown = _max_drawdown_from_pnl(pnl)
        win_rate = _safe_div(len([v for v in pnl if v > 0]), len(pnl)) if pnl else 0.0

        compatibility = 50.0
        compatibility += max(min(expectancy * 0.5, 20.0), -20.0)
        compatibility += max(min((win_rate - 0.5) * 40.0, 15.0), -15.0)
        compatibility -= max(min(drawdown * 100.0 * 0.6, 20.0), 0.0)
        compatibility += max(min(degradation * 0.5, 10.0), -10.0)
        compatibility = max(0.0, min(100.0, compatibility))

        enough_samples = len(items) >= int(min_samples)
        pause_recommended = bool(
            enough_samples
            and expectancy < 0
            and drawdown > float(max_acceptable_drawdown)
            and degradation < 0
        )
        reasons = []
        if not enough_samples:
            reasons.append("insufficient_sample")
        if expectancy < 0:
            reasons.append("negative_expectancy")
        if drawdown > float(max_acceptable_drawdown):
            reasons.append("drawdown_unacceptable")
        if degradation < 0:
            reasons.append("recent_degradation")

        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "regime_id": regime_id,
                "sample_count": len(items),
                "win_rate": round(win_rate, 6),
                "expectancy": round(expectancy, 6),
                "drawdown": round(drawdown, 6),
                "recent_degradation": round(degradation, 6),
                "compatibility_score": round(compatibility, 6),
                "pause_recommended": pause_recommended,
                "reasons": reasons,
            }
        )
    return rows


def build_factor_effectiveness(
    trade_memory: list[dict[str, Any]],
    *,
    min_samples: int = 30,
    min_stability: float = 0.45,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}

    for row in trade_memory:
        strategy_id = str(row.get("strategy_id") or "unknown")
        regime_id = str(row.get("market_regime_entry") or "unknown")
        component_scores = dict(row.get("component_scores") or {})
        for factor_name, value in sorted(component_scores.items()):
            bucket = _score_bucket(_safe_float(value, 0.0))
            grouped.setdefault((factor_name, bucket, strategy_id, regime_id), []).append(dict(row))

    results: list[dict[str, Any]] = []
    for (factor_name, bucket, strategy_id, regime_id), items in sorted(grouped.items()):
        pnl = [_safe_float(item.get("net_pnl"), 0.0) for item in items]
        returns = [_safe_float(item.get("percentage_return"), 0.0) for item in items]
        wins = len([value for value in pnl if value > 0])
        gross_profit = sum(value for value in pnl if value > 0)
        gross_loss = abs(sum(value for value in pnl if value < 0))
        expectancy = _safe_div(sum(pnl), len(pnl)) if pnl else 0.0

        comp_values = [_safe_float((item.get("component_scores") or {}).get(factor_name), 0.0) for item in items]
        corr = 0.0
        if len(comp_values) >= 3 and len(returns) == len(comp_values):
            mean_x = _safe_div(sum(comp_values), len(comp_values))
            mean_y = _safe_div(sum(returns), len(returns))
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(comp_values, returns))
            den_x = math.sqrt(sum((x - mean_x) ** 2 for x in comp_values))
            den_y = math.sqrt(sum((y - mean_y) ** 2 for y in returns))
            corr = _safe_div(num, den_x * den_y) if den_x > 0 and den_y > 0 else 0.0

        recent = returns[-20:]
        older = returns[:-20] if len(returns) > 20 else returns
        recent_avg = _safe_div(sum(recent), len(recent)) if recent else 0.0
        older_avg = _safe_div(sum(older), len(older)) if older else recent_avg
        stability = max(0.0, 1.0 - min(abs(recent_avg - older_avg), 1.0))

        predictive_status = "NOT_PREDICTIVE"
        if len(items) >= int(min_samples) and stability >= float(min_stability) and abs(corr) >= 0.15:
            predictive_status = "PREDICTIVE"

        results.append(
            {
                "analysis_version": FACTOR_EFFECTIVENESS_VERSION,
                "factor_name": factor_name,
                "factor_bucket": bucket,
                "strategy_id": strategy_id,
                "regime_id": regime_id,
                "sample_count": len(items),
                "win_rate": round(_safe_div(wins, len(items)), 6),
                "average_return": round(_safe_div(sum(returns), len(returns)) if returns else 0.0, 6),
                "median_return": round(float(median(returns)) if returns else 0.0, 6),
                "expectancy": round(expectancy, 6),
                "profit_factor": round(_safe_div(gross_profit, gross_loss), 6),
                "drawdown_contribution": round(_max_drawdown_from_pnl(pnl), 6),
                "forward_return_correlation": round(corr, 6),
                "stability_score": round(stability, 6),
                "predictive_status": predictive_status,
            }
        )
    return results


def recommend_weight_changes(
    current_weights: dict[str, float],
    factor_effectiveness_rows: list[dict[str, Any]],
    *,
    max_single_change: float = 3.0,
    min_samples: int = 30,
    require_walk_forward: bool = True,
    require_oos: bool = True,
) -> list[dict[str, Any]]:
    by_factor: dict[str, list[dict[str, Any]]] = {}
    for row in factor_effectiveness_rows:
        by_factor.setdefault(str(row.get("factor_name") or ""), []).append(dict(row))

    recos: list[dict[str, Any]] = []
    for factor_name, current in sorted(current_weights.items()):
        rows = by_factor.get(factor_name, [])
        sample_size = sum(_safe_int(item.get("sample_count"), 0) for item in rows)
        if sample_size <= 0:
            continue

        weighted_expectancy = _safe_div(
            sum(_safe_float(item.get("expectancy"), 0.0) * _safe_int(item.get("sample_count"), 0) for item in rows),
            sample_size,
        )
        weighted_stability = _safe_div(
            sum(_safe_float(item.get("stability_score"), 0.0) * _safe_int(item.get("sample_count"), 0) for item in rows),
            sample_size,
        )
        predictive_fraction = _safe_div(
            len([item for item in rows if str(item.get("predictive_status")) == "PREDICTIVE"]),
            len(rows),
        )

        evidence_ok = sample_size >= int(min_samples) and weighted_stability >= 0.45 and predictive_fraction >= 0.5
        delta = max_single_change if weighted_expectancy > 0 else -max_single_change
        proposed = float(current) + float(delta)

        rejected_reason = ""
        if not evidence_ok:
            proposed = float(current)
            rejected_reason = "weak_or_unstable_evidence"

        recos.append(
            {
                "recommendation_id": _stable_id([WEIGHT_RECOMMENDATION_VERSION, factor_name, _utc_iso()]),
                "recommendation_version": WEIGHT_RECOMMENDATION_VERSION,
                "factor_name": factor_name,
                "current_weight": float(current),
                "proposed_weight": float(proposed),
                "evidence": {
                    "weighted_expectancy": weighted_expectancy,
                    "weighted_stability": weighted_stability,
                    "predictive_fraction": predictive_fraction,
                },
                "sample_size": int(sample_size),
                "expected_benefit": float(weighted_expectancy),
                "risk_score": float(max(0.0, 1.0 - weighted_stability)),
                "confidence": float(max(0.0, min(1.0, predictive_fraction * weighted_stability))),
                "rollback_plan": f"revert {factor_name} to {current}",
                "walk_forward_passed": bool(evidence_ok and require_walk_forward),
                "out_of_sample_passed": bool(evidence_ok and require_oos),
                "review_required": True,
                "rejected_reason": rejected_reason,
            }
        )

    # Re-normalize if any accepted recommendation changed values.
    accepted = [row for row in recos if not row.get("rejected_reason")]
    if accepted:
        total = sum(float(row.get("proposed_weight") or 0.0) for row in recos)
        if total > 0:
            for row in recos:
                row["proposed_weight"] = round((_safe_float(row.get("proposed_weight"), 0.0) / total) * 100.0, 6)
    return recos


def recommend_strategy_states(
    leaderboard_rows: list[dict[str, Any]],
    strategy_regime_metrics: list[dict[str, Any]],
    *,
    min_samples: int = 30,
    review_only_default: bool = True,
) -> list[dict[str, Any]]:
    metrics_by_strategy: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in strategy_regime_metrics:
        key = (str(row.get("strategy_id") or "unknown"), str(row.get("strategy_version") or "unknown"))
        metrics_by_strategy.setdefault(key, []).append(dict(row))

    recos: list[dict[str, Any]] = []
    for row in leaderboard_rows:
        strategy_id = str(row.get("strategy_id") or "unknown")
        strategy_version = str(row.get("strategy_version") or "unknown")
        sample = _safe_int(row.get("completed_trade_count"), 0)
        expectancy = _safe_float(row.get("expectancy"), 0.0)
        profit_factor = _safe_float(row.get("profit_factor"), 0.0)
        sharpe = _safe_float(row.get("sharpe_ratio"), 0.0)
        drawdown = _safe_float(row.get("maximum_drawdown"), 0.0)
        recent = dict(row.get("recent_20") or {})
        full = dict(row.get("full_history") or {})
        degradation = _safe_float(recent.get("expectancy"), expectancy) - _safe_float(full.get("expectancy"), expectancy)

        regime_rows = metrics_by_strategy.get((strategy_id, strategy_version), [])
        regime_score = _safe_div(sum(_safe_float(item.get("compatibility_score"), 0.0) for item in regime_rows), len(regime_rows)) if regime_rows else 50.0

        current_state = "ACTIVE"
        proposed_state = "WATCH"
        reasons: list[str] = []

        if sample < int(min_samples):
            proposed_state = "WATCH"
            reasons.append("insufficient_sample")
        elif expectancy < 0 and drawdown > 0.20 and degradation < 0:
            proposed_state = "PAUSED"
            reasons.extend(["negative_expectancy", "drawdown", "recent_degradation"])
        elif expectancy < 0 or profit_factor < 1.0:
            proposed_state = "REDUCED"
            reasons.append("underperforming")
        elif sharpe > 0.8 and profit_factor > 1.2 and degradation >= 0:
            proposed_state = "ACTIVE"
            reasons.append("stable_positive_performance")
        else:
            proposed_state = "WATCH"
            reasons.append("mixed_signals")

        recos.append(
            {
                "recommendation_id": _stable_id([STATE_RECOMMENDATION_VERSION, strategy_id, strategy_version, _utc_iso()]),
                "recommendation_version": STATE_RECOMMENDATION_VERSION,
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "current_state": current_state,
                "proposed_state": proposed_state,
                "sample_size": sample,
                "net_expectancy": expectancy,
                "profit_factor": profit_factor,
                "sharpe_ratio": sharpe,
                "drawdown": drawdown,
                "recent_degradation": degradation,
                "regime_specific_result": regime_score,
                "stability_score": max(0.0, min(1.0, _safe_float(row.get("win_rate"), 0.0))),
                "automation_allowed": False,
                "review_required": bool(review_only_default),
                "reasons": reasons,
                "warnings": ["recommendation_only_default"],
            }
        )
    return recos


def recommend_position_size(
    *,
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    quantum_score: float,
    strategy_score: float,
    historical_expectancy: float,
    profit_factor: float,
    drawdown: float,
    sample_size: int,
    market_regime: str,
    portfolio_concentration: float,
    recent_stability: float,
    max_allowed_allocation: float,
    hard_cap_allocation: float,
    hard_cap_risk: float,
    risk_policy_checks: dict[str, bool],
) -> dict[str, Any]:
    tier = "skip"
    base_alloc = 0.0
    if quantum_score >= 94:
        tier = "high_confidence"
        base_alloc = 0.08
    elif quantum_score >= 88:
        tier = "medium"
        base_alloc = 0.05
    elif quantum_score >= 80:
        tier = "small"
        base_alloc = 0.03
    elif quantum_score >= 70:
        tier = "very_small"
        base_alloc = 0.01

    reasons = [f"quantum_score={quantum_score:.2f}", f"strategy_score={strategy_score:.2f}"]
    warnings: list[str] = []

    evidence_mult = 1.0
    if sample_size < 30:
        evidence_mult *= 0.5
        warnings.append("insufficient_sample")
    if historical_expectancy < 0:
        evidence_mult *= 0.4
        warnings.append("negative_expectancy")
    if profit_factor < 1.0:
        evidence_mult *= 0.5
        warnings.append("low_profit_factor")
    if drawdown > 0.20:
        evidence_mult *= 0.6
        warnings.append("high_drawdown")
    if portfolio_concentration > 0.35:
        evidence_mult *= 0.5
        warnings.append("portfolio_concentration_high")
    if recent_stability < 0.5:
        evidence_mult *= 0.6
        warnings.append("recent_stability_low")
    if market_regime in {"strong_bear", "panic_risk_off"}:
        evidence_mult *= 0.4
        warnings.append("risk_off_regime")

    policy_passed = all(bool(value) for value in risk_policy_checks.values())
    if not policy_passed:
        base_alloc = 0.0
        warnings.append("hard_risk_policy_failed")

    recommended_allocation = min(base_alloc * evidence_mult, max_allowed_allocation, hard_cap_allocation)
    recommended_risk = min(recommended_allocation * 0.25, hard_cap_risk)

    return {
        "recommendation_id": _stable_id([ALLOCATION_RECOMMENDATION_VERSION, strategy_id, strategy_version, symbol, _utc_iso()]),
        "recommendation_version": ALLOCATION_RECOMMENDATION_VERSION,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "symbol": symbol,
        "quantum_score": float(quantum_score),
        "strategy_score": float(strategy_score),
        "historical_expectancy": float(historical_expectancy),
        "profit_factor": float(profit_factor),
        "drawdown": float(drawdown),
        "sample_size": int(sample_size),
        "market_regime": str(market_regime),
        "portfolio_concentration": float(portfolio_concentration),
        "stability_score": float(recent_stability),
        "recommended_allocation_pct": round(recommended_allocation * 100.0, 6),
        "recommended_risk_pct": round(recommended_risk * 100.0, 6),
        "confidence_tier": tier,
        "reasons": reasons,
        "warnings": warnings,
        "max_allowed_allocation_pct": round(min(max_allowed_allocation, hard_cap_allocation) * 100.0, 6),
        "policy_passed": bool(policy_passed),
        "review_required": True,
    }


def recommend_strategy_allocation_actions(
    leaderboard: list[dict[str, Any]],
    *,
    min_samples: int = 30,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in sorted(list(leaderboard or []), key=lambda item: str(item.get("strategy_id") or "")):
        strategy_id = str(row.get("strategy_id") or "unknown")
        strategy_version = str(row.get("strategy_version") or "unknown")
        sample = _safe_int(row.get("completed_trade_count"), 0)
        win_rate = _safe_float(row.get("win_rate"), 0.0)
        expectancy = _safe_float(row.get("expectancy"), 0.0)
        profit_factor = _safe_float(row.get("profit_factor"), 0.0)
        sharpe = _safe_float(row.get("sharpe_ratio"), 0.0)
        sortino = _safe_float(row.get("sortino_ratio"), 0.0)
        drawdown = _safe_float(row.get("maximum_drawdown"), 0.0)
        recent20 = dict(row.get("recent_20") or {})
        recent60 = dict(row.get("recent_60") or {})
        recent20_expectancy = _safe_float(recent20.get("expectancy"), expectancy)
        recent60_expectancy = _safe_float(recent60.get("expectancy"), expectancy)

        action = "MAINTAIN"
        reasons: list[str] = []
        multiplier = 1.0

        if sample < int(min_samples):
            action = "INSUFFICIENT_SAMPLE"
            reasons.append("fewer_than_minimum_completed_trades")
            multiplier = 1.0
        elif drawdown >= 0.30:
            action = "PAUSE_RECOMMENDED"
            reasons.append("severe_drawdown")
            multiplier = 0.0
        elif recent20_expectancy < 0 or recent60_expectancy < 0 or profit_factor < 1.0:
            action = "REDUCE_RECOMMENDED"
            reasons.append("weak_recent_performance")
            multiplier = 0.75
        elif expectancy > 0 and win_rate >= 0.55 and profit_factor >= 1.2 and sharpe > 0.6 and sortino > 0.4:
            action = "PROMOTE_RECOMMENDED"
            reasons.append("strong_historical_and_recent_evidence")
            multiplier = 1.20
        else:
            action = "MAINTAIN"
            reasons.append("mixed_or_neutral_evidence")
            multiplier = 1.0

        if sample < int(min_samples) and multiplier > 1.0:
            multiplier = 1.0

        actions.append(
            {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "completed_trade_count": sample,
                "win_rate": win_rate,
                "expectancy": expectancy,
                "profit_factor": profit_factor,
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "maximum_drawdown": drawdown,
                "recent20_expectancy": recent20_expectancy,
                "recent60_expectancy": recent60_expectancy,
                "action": action,
                "allocation_multiplier": multiplier,
                "review_required": True,
                "automation_allowed": False,
                "reasons": reasons,
            }
        )

    return actions


def build_daily_report(
    *,
    market_date: str,
    account_equity: float,
    daily_pnl: float,
    open_positions: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
    top_quantum_candidates: list[dict[str, Any]],
    rejected_candidates: list[dict[str, Any]],
    strategy_leaderboard: list[dict[str, Any]],
    current_market_regime: dict[str, Any],
    strategy_regime_metrics: list[dict[str, Any]],
    factor_updates: list[dict[str, Any]],
    risk_limit_events: list[str],
    system_errors: list[str],
    dry_run_mode: bool,
) -> dict[str, Any]:
    by_strategy: dict[str, int] = {}
    for row in closed_trades:
        sid = str(row.get("strategy_id") or "unknown")
        by_strategy[sid] = by_strategy.get(sid, 0) + 1

    pnl_values = [_safe_float(item.get("net_pnl"), 0.0) for item in closed_trades]
    best_trade = max(pnl_values) if pnl_values else 0.0
    worst_trade = min(pnl_values) if pnl_values else 0.0

    return {
        "report_type": "daily",
        "report_version": REPORT_VERSION,
        "market_date": market_date,
        "account_equity": float(account_equity),
        "daily_pnl": float(daily_pnl),
        "open_positions": list(open_positions),
        "closed_trades": list(closed_trades),
        "trades_by_strategy": by_strategy,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "top_quantum_candidates": list(top_quantum_candidates),
        "rejected_candidates": list(rejected_candidates),
        "strategy_leaderboard": list(strategy_leaderboard),
        "current_market_regime": dict(current_market_regime),
        "strategy_regime_compatibility": list(strategy_regime_metrics),
        "factor_performance_updates": list(factor_updates),
        "risk_limit_events": list(risk_limit_events),
        "system_errors": list(system_errors),
        "dry_run_mode": bool(dry_run_mode),
        "created_at": _utc_iso(),
    }


def build_weekly_report(
    *,
    period_start: str,
    period_end: str,
    state_recommendations: list[dict[str, Any]],
    weight_recommendations: list[dict[str, Any]],
    regime_performance: list[dict[str, Any]],
    score_bucket_performance: list[dict[str, Any]],
    drawdown_analysis: dict[str, Any],
    walk_forward_results: dict[str, Any],
    recommended_changes: list[str],
    unresolved_data_quality_issues: list[str],
    account_return: Any = "N/A",
    portfolio_return: Any = "N/A",
    benchmark_return: Any = "N/A",
    proposed_vs_actual_allocations: Any = "N/A",
    sector_exposure: Any = "N/A",
    strategy_exposure: Any = "N/A",
    average_correlation: Any = "N/A",
    maximum_correlation: Any = "N/A",
    cash_reserve: Any = "N/A",
    concentration_warnings: Any = "N/A",
    strongest_strategy: Any = "N/A",
    weakest_strategy: Any = "N/A",
    highest_risk_position: Any = "N/A",
    diversification_score: Any = "N/A",
    portfolio_risk_score: Any = "N/A",
) -> dict[str, Any]:
    return {
        "report_type": "weekly",
        "report_version": REPORT_VERSION,
        "period_start": period_start,
        "period_end": period_end,
        "strategy_state_recommendations": list(state_recommendations),
        "factor_weight_recommendations": list(weight_recommendations),
        "regime_performance": list(regime_performance),
        "score_bucket_performance": list(score_bucket_performance),
        "drawdown_analysis": dict(drawdown_analysis),
        "walk_forward_validation": dict(walk_forward_results),
        "changes_recommended_for_review": list(recommended_changes),
        "unresolved_data_quality_issues": list(unresolved_data_quality_issues),
        "account_return": account_return,
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "proposed_vs_actual_allocations": proposed_vs_actual_allocations,
        "sector_exposure": sector_exposure,
        "strategy_exposure": strategy_exposure,
        "average_correlation": average_correlation,
        "maximum_correlation": maximum_correlation,
        "cash_reserve": cash_reserve,
        "concentration_warnings": concentration_warnings,
        "strongest_strategy": strongest_strategy,
        "weakest_strategy": weakest_strategy,
        "highest_risk_position": highest_risk_position,
        "diversification_score": diversification_score,
        "portfolio_risk_score": portfolio_risk_score,
        "recommendation_only": True,
        "created_at": _utc_iso(),
    }


def recommendation_policy_summary() -> dict[str, Any]:
    return {
        "live_blocked": True,
        "dry_run_submission_blocked": True,
        "recommendation_review_only_default": True,
        "no_auto_weight_change": True,
        "no_secret_modification": True,
        "no_self_allocation_escalation_without_policy": True,
        "risk_checks_cannot_be_bypassed": True,
        "stale_or_failed_data_rejected": True,
        "minimum_sample_default": 30,
        "weight_change_max_step_default_pct": 3.0,
    }
