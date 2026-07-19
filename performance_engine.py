from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config import BENCHMARK_SYMBOL, is_safe_mode, TRADING_MODE
from market_data import download_price_data
from performance_metrics import (
    build_benchmark_metrics,
    build_equity_curve_metrics,
    build_exposure_metrics,
    build_risk_ratios,
    build_trade_statistics,
)
from performance_repository import PerformanceRepository, PerformanceRunPayload


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _hash_id(parts: list[str], length: int = 24) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def _parse_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


def _run_date(run_row: dict[str, Any]) -> str:
    return _parse_date(run_row.get("completed_at") or run_row.get("scanner_timestamp") or run_row.get("started_at"))


def _build_positions(positions_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for symbol, payload in sorted((positions_payload or {}).items()):
        qty = _safe_float((payload or {}).get("quantity"), 0.0)
        price = _safe_float((payload or {}).get("avg_price"), 0.0)
        value = qty * price
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "quantity": qty,
                "avg_price": price,
                "market_value": value,
                "sector": "Unknown",
            }
        )
    return rows


def _build_daily_rows(source_runs: list[dict[str, Any]], snapshots_by_run: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for run in source_runs:
        run_id = str(run.get("run_id") or "")
        run_date = _run_date(run)
        if not run_date:
            continue
        snapshots = snapshots_by_run.get(run_id) or []
        latest_snapshot = snapshots[-1] if snapshots else {}
        portfolio_value = _safe_float(latest_snapshot.get("portfolio_value"), 0.0)
        cash = _safe_float(latest_snapshot.get("cash"), 0.0)
        buying_power = _safe_float(latest_snapshot.get("buying_power"), 0.0)
        if portfolio_value <= 0:
            portfolio_value = max(cash, buying_power)
        candidate = {
            "equity_date": run_date,
            "source_validation_run_id": run_id,
            "portfolio_value": portfolio_value,
            "cash": cash,
            "buying_power": buying_power,
            "captured_at": latest_snapshot.get("captured_at") or run.get("completed_at"),
            "positions": latest_snapshot.get("positions") or {},
        }
        existing = grouped.get(run_date)
        if existing is None or str(candidate.get("captured_at") or "") > str(existing.get("captured_at") or ""):
            grouped[run_date] = candidate

    return [grouped[key] for key in sorted(grouped.keys())]


def _benchmark_returns(start_date: str, end_date: str, count_hint: int) -> list[float]:
    if not start_date or not end_date or count_hint < 2:
        return []
    prices = download_price_data(BENCHMARK_SYMBOL, start_date, end_date)
    if prices is None or prices.empty:
        return []
    series = prices.get("Close")
    if series is None or len(series) < 2:
        return []
    returns = []
    values = [float(item) for item in series.tolist()]
    for idx, value in enumerate(values):
        if idx == 0:
            continue
        prev = values[idx - 1]
        returns.append((value / prev) - 1.0 if prev > 0 else 0.0)
    return returns[-(count_hint - 1) :]


def run_performance_intelligence(database_url: str | None, benchmark_symbol: str = BENCHMARK_SYMBOL) -> dict[str, Any]:
    if not is_safe_mode(TRADING_MODE):
        raise RuntimeError("Performance Intelligence is blocked in LIVE mode")

    repository = PerformanceRepository(database_url=database_url)
    run_id = f"perf-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    started_at = _utc_iso()

    try:
        source_runs = repository.fetch_source_runs(limit=5000)
        if not source_runs:
            run_payload = {
                "run_id": run_id,
                "started_at": started_at,
                "completed_at": _utc_iso(),
                "status": "empty",
                "source_run_count": 0,
                "source_trade_count": 0,
                "analysis_start_date": None,
                "analysis_end_date": None,
                "benchmark_symbol": benchmark_symbol,
                "configuration": {"benchmark_symbol": benchmark_symbol},
                "warnings": ["no completed paper validation runs available"],
                "error_message": None,
                "created_at": started_at,
                "updated_at": _utc_iso(),
            }
            repository.save_run(PerformanceRunPayload(run=run_payload, daily_equity=[], portfolio_snapshots=[], trade_statistics=[], metrics=[]))
            return {"status": "empty", "run_id": run_id, "warnings": run_payload["warnings"]}

        snapshots_by_run: dict[str, list[dict[str, Any]]] = {}
        orders_by_run: dict[str, list[dict[str, Any]]] = {}
        for run in source_runs:
            source_run_id = str(run.get("run_id") or "")
            snapshots_by_run[source_run_id] = repository.fetch_snapshots_for_run(source_run_id)
            orders_by_run[source_run_id] = repository.fetch_orders_for_run(source_run_id)

        daily_rows = _build_daily_rows(source_runs, snapshots_by_run)
        equity_metrics = build_equity_curve_metrics(daily_rows)
        portfolio_returns = list(equity_metrics.get("daily_returns") or [])[1:]

        trade_pnl: list[float] = []
        hold_days: list[float] = []
        turnover_total = 0.0
        total_portfolio_reference = 0.0

        trade_stats_rows = []
        snapshot_rows = []
        for run in source_runs:
            source_run_id = str(run.get("run_id") or "")
            run_date = _run_date(run)
            orders = orders_by_run.get(source_run_id) or []
            snapshots = snapshots_by_run.get(source_run_id) or []
            latest_snapshot = snapshots[-1] if snapshots else {}
            positions_payload = latest_snapshot.get("positions") or {}
            positions = _build_positions(positions_payload)
            portfolio_value = _safe_float(latest_snapshot.get("portfolio_value"), 0.0)
            exposure = build_exposure_metrics(positions, portfolio_value)

            turnover = sum(_safe_float(order.get("notional"), 0.0) for order in orders if str(order.get("submission_status") or "") in {"submitted", "filled", "partially_filled", "pending"})
            turnover_total += turnover
            total_portfolio_reference += max(portfolio_value, 0.0)

            row_trade_pnl = []
            row_hold_days = []
            for order in orders:
                side = str(order.get("side") or "").upper()
                status = str(order.get("submission_status") or "")
                qty = _safe_float(order.get("filled_quantity") or order.get("quantity"), 0.0)
                fill_price = _safe_float(order.get("average_fill_price") or order.get("reference_price"), 0.0)
                if status not in {"filled", "submitted", "partially_filled", "pending"}:
                    continue
                signed = (qty * fill_price) if side == "SELL" else -(qty * fill_price)
                row_trade_pnl.append(signed)
                hold_hint = _safe_float((order.get("order_payload") or {}).get("hold_days"), 0.0)
                if hold_hint > 0:
                    row_hold_days.append(hold_hint)

            trade_pnl.extend(row_trade_pnl)
            hold_days.extend(row_hold_days)
            row_stats = build_trade_statistics(row_trade_pnl, row_hold_days)
            trade_stats_rows.append(
                {
                    "trade_stat_id": _hash_id([run_id, source_run_id, run_date, "trade-stats"], length=32),
                    "source_validation_run_id": source_run_id,
                    "trade_date": run_date,
                    "trade_count": len(row_trade_pnl),
                    "win_rate": row_stats.get("win_rate"),
                    "loss_rate": row_stats.get("loss_rate"),
                    "average_winner": row_stats.get("average_winner"),
                    "average_loser": row_stats.get("average_loser"),
                    "profit_factor": row_stats.get("profit_factor"),
                    "largest_winner": row_stats.get("largest_winner"),
                    "largest_loser": row_stats.get("largest_loser"),
                    "average_hold_time_days": row_stats.get("average_hold_time"),
                    "turnover": turnover,
                    "created_at": _utc_iso(),
                }
            )

            snapshot_rows.append(
                {
                    "snapshot_id": _hash_id([run_id, source_run_id, "snapshot"], length=32),
                    "source_validation_run_id": source_run_id,
                    "captured_at": latest_snapshot.get("captured_at") or run.get("completed_at") or _utc_iso(),
                    "portfolio_value": portfolio_value,
                    "cash": _safe_float(latest_snapshot.get("cash"), 0.0),
                    "buying_power": _safe_float(latest_snapshot.get("buying_power"), 0.0),
                    "exposure_pct": exposure.get("exposure_pct"),
                    "position_concentration": exposure.get("position_concentration"),
                    "sector_allocation": exposure.get("sector_allocation"),
                    "positions": positions_payload,
                    "created_at": _utc_iso(),
                }
            )

        aggregated_trade = build_trade_statistics(trade_pnl, hold_days)
        benchmark_returns = _benchmark_returns(
            start_date=daily_rows[0].get("equity_date") if daily_rows else "",
            end_date=daily_rows[-1].get("equity_date") if daily_rows else "",
            count_hint=len(daily_rows),
        )
        benchmark_metrics = build_benchmark_metrics(portfolio_returns, benchmark_returns)
        risk_metrics = build_risk_ratios(portfolio_returns, equity_metrics.get("maximum_drawdown", 0.0))

        avg_portfolio = (total_portfolio_reference / len(source_runs)) if source_runs else 0.0
        turnover_ratio = (turnover_total / avg_portfolio) if avg_portfolio > 0 else 0.0
        exposure_latest = snapshot_rows[-1] if snapshot_rows else {}

        all_metrics = {
            "portfolio_value": equity_metrics.get("portfolio_value"),
            "cash": equity_metrics.get("cash"),
            "buying_power": equity_metrics.get("buying_power"),
            "daily_return": equity_metrics.get("daily_return"),
            "total_return": equity_metrics.get("total_return"),
            "cumulative_return": equity_metrics.get("cumulative_return"),
            "maximum_drawdown": equity_metrics.get("maximum_drawdown"),
            "current_drawdown": equity_metrics.get("current_drawdown"),
            "volatility": equity_metrics.get("volatility"),
            "win_rate": aggregated_trade.get("win_rate"),
            "loss_rate": aggregated_trade.get("loss_rate"),
            "average_winner": aggregated_trade.get("average_winner"),
            "average_loser": aggregated_trade.get("average_loser"),
            "profit_factor": aggregated_trade.get("profit_factor"),
            "largest_winner": aggregated_trade.get("largest_winner"),
            "largest_loser": aggregated_trade.get("largest_loser"),
            "average_hold_time": aggregated_trade.get("average_hold_time"),
            "exposure_pct": exposure_latest.get("exposure_pct", 0.0),
            "position_concentration": exposure_latest.get("position_concentration", 0.0),
            "turnover": turnover_ratio,
            "alpha": benchmark_metrics.get("alpha"),
            "beta": benchmark_metrics.get("beta"),
            "tracking_error": benchmark_metrics.get("tracking_error"),
            "excess_return": benchmark_metrics.get("excess_return"),
            "information_ratio": benchmark_metrics.get("information_ratio"),
            "sharpe_ratio": risk_metrics.get("sharpe_ratio"),
            "sortino_ratio": risk_metrics.get("sortino_ratio"),
            "calmar_ratio": risk_metrics.get("calmar_ratio"),
        }

        metrics_rows = []
        for name, value in sorted(all_metrics.items()):
            group = "benchmark" if name in {"alpha", "beta", "tracking_error", "excess_return", "information_ratio"} else "risk" if name in {"sharpe_ratio", "sortino_ratio", "calmar_ratio", "maximum_drawdown", "current_drawdown", "volatility"} else "trade" if name in {"win_rate", "loss_rate", "average_winner", "average_loser", "profit_factor", "largest_winner", "largest_loser", "average_hold_time", "turnover"} else "portfolio"
            metrics_rows.append(
                {
                    "metric_id": _hash_id([run_id, name], length=32),
                    "metric_group": group,
                    "metric_name": name,
                    "metric_value": None if value is None else float(value),
                    "as_of_date": daily_rows[-1].get("equity_date") if daily_rows else None,
                    "metadata": {"benchmark_symbol": benchmark_symbol},
                    "created_at": _utc_iso(),
                }
            )

        daily_equity_rows = []
        drawdown_series = list(equity_metrics.get("drawdown_series") or [])
        daily_returns = list(equity_metrics.get("daily_returns") or [])
        start_value = _safe_float(daily_rows[0].get("portfolio_value"), 0.0) if daily_rows else 0.0
        max_drawdowns = []
        running_min = 0.0
        for value in drawdown_series:
            running_min = min(running_min, _safe_float(value, 0.0))
            max_drawdowns.append(running_min)

        for idx, row in enumerate(daily_rows):
            value = _safe_float(row.get("portfolio_value"), 0.0)
            cumulative = ((value / start_value) - 1.0) if start_value > 0 else 0.0
            daily_equity_rows.append(
                {
                    "daily_equity_id": _hash_id([run_id, str(row.get("equity_date"))], length=32),
                    "equity_date": row.get("equity_date"),
                    "portfolio_value": value,
                    "cash": _safe_float(row.get("cash"), 0.0),
                    "buying_power": _safe_float(row.get("buying_power"), 0.0),
                    "daily_return": daily_returns[idx] if idx < len(daily_returns) else 0.0,
                    "total_return": cumulative,
                    "cumulative_return": cumulative,
                    "max_drawdown": max_drawdowns[idx] if idx < len(max_drawdowns) else 0.0,
                    "current_drawdown": drawdown_series[idx] if idx < len(drawdown_series) else 0.0,
                    "volatility": equity_metrics.get("volatility", 0.0),
                    "turnover": turnover_ratio,
                    "exposure_pct": exposure_latest.get("exposure_pct", 0.0),
                    "position_concentration": exposure_latest.get("position_concentration", 0.0),
                    "created_at": _utc_iso(),
                }
            )

        run_payload = {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": _utc_iso(),
            "status": "completed",
            "source_run_count": len(source_runs),
            "source_trade_count": len(trade_pnl),
            "analysis_start_date": daily_rows[0].get("equity_date") if daily_rows else None,
            "analysis_end_date": daily_rows[-1].get("equity_date") if daily_rows else None,
            "benchmark_symbol": benchmark_symbol,
            "configuration": {"benchmark_symbol": benchmark_symbol},
            "warnings": [],
            "error_message": None,
            "created_at": started_at,
            "updated_at": _utc_iso(),
        }

        save_result = repository.save_run(
            PerformanceRunPayload(
                run=run_payload,
                daily_equity=daily_equity_rows,
                portfolio_snapshots=snapshot_rows,
                trade_statistics=trade_stats_rows,
                metrics=metrics_rows,
            )
        )

        return {
            "status": "completed",
            "run_id": run_id,
            "source_run_count": len(source_runs),
            "source_trade_count": len(trade_pnl),
            "metrics": all_metrics,
            "persistence": save_result,
        }
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Performance Intelligence Engine (read-only analytics)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    result = run_performance_intelligence(database_url=args.database_url)
    print(result)


if __name__ == "__main__":
    main()
