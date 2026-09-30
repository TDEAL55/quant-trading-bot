from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def index_submission_records(records: Iterable[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Index durable entry evidence without exposing credentials."""
    indexed: dict[str, dict[str, Any]] = {}
    for raw in records or []:
        row = dict(raw or {})
        for key in ("order_id", "client_order_id"):
            value = str(row.get(key) or "").strip()
            if value:
                indexed[value] = row
    return indexed


def _entry_evidence(event: Mapping[str, Any], submissions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    lots = list(event.get("matched_lots") or [])
    for lot in lots:
        for key in ("entry_order_id", "order_id", "order_key", "client_order_id"):
            identifier = str((lot or {}).get(key) or "").strip()
            if identifier and identifier in submissions:
                return dict(submissions[identifier])
    return {}


def _price_excursions(
    event: Mapping[str, Any],
    price_paths: Mapping[str, Iterable[Mapping[str, Any]]] | None,
) -> tuple[float | None, float | None]:
    symbol = str(event.get("symbol") or "").upper()
    bars = list((price_paths or {}).get(symbol) or [])
    lots = list(event.get("matched_lots") or [])
    if not bars or not lots:
        return None, None
    entry_time = _timestamp(lots[0].get("entry_timestamp"))
    exit_time = _timestamp(event.get("exit_timestamp"))
    entry_price = _number(lots[0].get("entry_price"))
    direction = str(lots[0].get("direction") or "long").lower()
    if entry_time is None or exit_time is None or entry_price <= 0:
        return None, None
    highs: list[float] = []
    lows: list[float] = []
    for bar in bars:
        at = _timestamp((bar or {}).get("timestamp"))
        if at is None or at < entry_time or at > exit_time:
            continue
        highs.append(_number((bar or {}).get("high")))
        lows.append(_number((bar or {}).get("low")))
    if not highs or not lows:
        return None, None
    if direction == "short":
        return round(entry_price - min(lows), 6), round(max(highs) - entry_price, 6)
    return round(max(highs) - entry_price, 6), round(entry_price - min(lows), 6)


def _rule_compliance(evidence: Mapping[str, Any]) -> tuple[bool | None, list[str]]:
    if not evidence:
        return None, ["entry_decision_evidence_not_recorded"]
    checks = dict(evidence.get("rule_checks") or {})
    violations = [str(name) for name, passed in checks.items() if passed is False]
    if violations:
        return False, violations
    required = ("regime_route", "quality_threshold", "risk_limit", "protective_exit")
    if all(checks.get(name) is True for name in required):
        return True, []
    return None, [name for name in required if name not in checks]


def _breakdown(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    result = []
    for key, group in sorted(grouped.items()):
        wins = sum(1 for row in group if _number(row.get("actual_profit_loss")) > 0)
        result.append(
            {
                field: key,
                "trade_count": len(group),
                "pnl": round(sum(_number(row.get("actual_profit_loss")) for row in group), 6),
                "win_rate": round(wins / len(group), 6) if group else None,
            }
        )
    return result


def _maximum_drawdown(pnls: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 6)


def _streaks(pnls: list[float]) -> tuple[int, int]:
    best_win = best_loss = current_win = current_loss = 0
    for pnl in pnls:
        if pnl > 0:
            current_win += 1
            current_loss = 0
        elif pnl < 0:
            current_loss += 1
            current_win = 0
        else:
            current_win = current_loss = 0
        best_win = max(best_win, current_win)
        best_loss = max(best_loss, current_loss)
    return best_win, best_loss


def build_trade_review(
    reconstruction: Mapping[str, Any] | None,
    *,
    submission_records: Iterable[Mapping[str, Any]] | None = None,
    price_paths: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    minimum_sample: int = 30,
) -> dict[str, Any]:
    """Build an evidence-first review; never infer causes from missing data."""
    diagnostic = dict(reconstruction or {})
    submissions = index_submission_records(submission_records)
    trades: list[dict[str, Any]] = []
    missing_fields: set[str] = set()
    for raw_event in list(diagnostic.get("realized_events") or []):
        event = dict(raw_event or {})
        lots = list(event.get("matched_lots") or [])
        first_lot = dict(lots[0] or {}) if lots else {}
        evidence = _entry_evidence(event, submissions)
        pnl = _number(event.get("realized_pnl"))
        quantity = _number(event.get("quantity_closed"))
        entry_price = (
            sum(_number(lot.get("entry_price")) * _number(lot.get("quantity")) for lot in lots)
            / sum(_number(lot.get("quantity")) for lot in lots)
            if sum(_number(lot.get("quantity")) for lot in lots) > 0
            else 0.0
        )
        mfe, mae = _price_excursions(event, price_paths)
        compliant, violations = _rule_compliance(evidence)
        confirmations = list(evidence.get("confirmation_categories") or [])
        entry_time = str(first_lot.get("entry_timestamp") or "")
        parsed_entry = _timestamp(entry_time)
        row = {
            "strategy": str(evidence.get("strategy_id") or event.get("entry_strategy_id") or first_lot.get("strategy_id") or "unknown"),
            "symbol": str(event.get("symbol") or "").upper(),
            "side": str(first_lot.get("direction") or "long"),
            "entry_timestamp": entry_time,
            "exit_timestamp": str(event.get("exit_timestamp") or ""),
            "entry_price": round(entry_price, 6),
            "exit_price": _number(event.get("average_exit_price")),
            "position_size": quantity,
            "stop_loss": evidence.get("stop_price"),
            "profit_target": evidence.get("target_price"),
            "actual_profit_loss": pnl,
            "market_regime": str(evidence.get("market_regime") or "unknown"),
            "trend_strength": evidence.get("trend_strength"),
            "volatility": evidence.get("volatility"),
            "volume": evidence.get("volume"),
            "relative_strength": evidence.get("relative_strength"),
            "entry_confirmations": confirmations,
            "reason_for_entry": str(evidence.get("entry_reason") or "not_recorded"),
            "reason_for_exit": str(event.get("exit_reason") or "broker_close_fill"),
            "maximum_favorable_excursion": mfe,
            "maximum_adverse_excursion": mae,
            "followed_strategy_rules": compliant,
            "rule_review_notes": violations,
            "risk_reward": evidence.get("expected_reward_risk"),
            "time_of_day": parsed_entry.strftime("%H:00") if parsed_entry else "unknown",
            "data_confidence": str(event.get("confidence") or diagnostic.get("confidence") or "unknown"),
        }
        for name in (
            "market_regime", "trend_strength", "volatility", "volume", "relative_strength",
            "stop_loss", "profit_target", "maximum_favorable_excursion", "maximum_adverse_excursion",
            "followed_strategy_rules",
        ):
            if row.get(name) in (None, "unknown"):
                missing_fields.add(name)
        trades.append(row)

    trades.sort(key=lambda row: (row.get("exit_timestamp") or "", row.get("symbol") or ""))
    pnls = [_number(row.get("actual_profit_loss")) for row in trades]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl < 0]
    win_streak, loss_streak = _streaks(pnls)
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    risk_rewards = [_number(row.get("risk_reward")) for row in trades if row.get("risk_reward") is not None]
    count = len(trades)
    metrics = {
        "number_of_trades": count,
        "total_pnl": round(sum(pnls), 6),
        "win_rate": round(len(winners) / count, 6) if count else None,
        "average_winner": round(sum(winners) / len(winners), 6) if winners else None,
        "average_loser": round(sum(losers) / len(losers), 6) if losers else None,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else (None if not gross_profit else float("inf")),
        "expectancy_per_trade": round(sum(pnls) / count, 6) if count else None,
        "maximum_drawdown": _maximum_drawdown(pnls),
        "average_risk_reward": round(sum(risk_rewards) / len(risk_rewards), 6) if risk_rewards else None,
        "maximum_consecutive_wins": win_streak,
        "maximum_consecutive_losses": loss_streak,
    }
    sufficient = count >= max(int(minimum_sample), 1)
    conclusions = {
        "sample_sufficient": sufficient,
        "minimum_sample": max(int(minimum_sample), 1),
        "loss_cause": (
            "no_completed_losses_to_diagnose" if not losers else
            "insufficient_evidence_do_not_change_strategy" if not sufficient else
            "requires_trade_level_review"
        ),
        "best_strategy": None,
        "worst_strategy": None,
    }
    by_strategy = _breakdown(trades, "strategy")
    if sufficient and by_strategy:
        conclusions["best_strategy"] = max(by_strategy, key=lambda row: row["pnl"])["strategy"]
        conclusions["worst_strategy"] = min(by_strategy, key=lambda row: row["pnl"])["strategy"]

    return {
        "trades": trades,
        "metrics": metrics,
        "breakdowns": {
            "by_strategy": by_strategy,
            "by_market_regime": _breakdown(trades, "market_regime"),
            "by_symbol": _breakdown(trades, "symbol"),
            "by_time_of_day": _breakdown(trades, "time_of_day"),
        },
        "conclusions": conclusions,
        "data_quality": {
            "reconstruction_confidence": str(diagnostic.get("confidence") or "unknown"),
            "missing_fields": sorted(missing_fields),
            "entry_evidence_records": len(submissions),
        },
        "priority_changes": [
            "capture_complete_entry_context_before_changing_strategy_rules",
            "collect_a_meaningful_backtest_and_paper_sample_per_strategy_and_regime",
            "compare_backtest_paper_and_live_slippage_and_expectancy",
            "change_one_measurable_rule_at_a_time_then_retest_before_more_live_capital",
        ],
    }
