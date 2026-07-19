from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config import BENCHMARK_SYMBOL
from factor_intelligence import FactorIntelligenceConfig, FactorIntelligenceEngine
from factor_registry import build_default_registry
from market_data import download_price_data
from paper_approval import build_approval_record
from paper_execution_repository import MonitoringPaperExecutionRepository, PaperValidationRunPayload
from paper_reconciliation import reconcile_paper_positions
from paper_validation_data import fetch_paper_validation_dashboard_payload
from research_journal import journal_scanner_run
from risk_manager import RiskManager
from scanner_runner import SAMPLE_SYMBOLS, run_scan, run_shortlist_only
from security_factor_explainability import build_security_explanation
from sprint_10_1_validation import SimulatedFillBroker


@dataclass(frozen=True)
class ExecutionProfile:
    profile_id: str = "sprint_10_2_execution_validation"
    mode: str = "PAPER"
    max_position_size: float = 0.25
    max_daily_loss: float = 500.0
    daily_loss_limit: float = 500.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _history_window(days: int = 1000) -> tuple[str, str]:
    end_date = _utc_now().date()
    start_date = end_date - timedelta(days=days)
    return start_date.isoformat(), end_date.isoformat()


def _market_snapshot(benchmark_symbol: str = BENCHMARK_SYMBOL, min_history_rows: int = 200) -> dict[str, Any]:
    start_date, end_date = _history_window(1000)
    prices = download_price_data(benchmark_symbol, start_date, end_date)
    if prices is None or prices.empty:
        raise RuntimeError("market data unavailable")

    latest_idx = prices.index[-1]
    latest_ts = latest_idx.to_pydatetime() if hasattr(latest_idx, "to_pydatetime") else latest_idx
    if getattr(latest_ts, "tzinfo", None) is None:
        latest_ts = latest_ts.replace(tzinfo=timezone.utc)
    latest_ts = latest_ts.astimezone(timezone.utc)

    now_dt = _utc_now()
    age_days = (now_dt.date() - latest_ts.date()).days
    stale = age_days > 3
    fresh = not stale
    session_type = "today" if latest_ts.date() == now_dt.date() else "latest_completed_session"
    sufficient_history = int(len(prices)) >= int(min_history_rows)

    return {
        "benchmark_symbol": benchmark_symbol,
        "market_timestamp": latest_ts.isoformat(),
        "rows": int(len(prices)),
        "age_days": int(age_days),
        "fresh": bool(fresh),
        "stale": bool(stale),
        "sufficient_history": bool(sufficient_history),
        "session_type": session_type,
    }


def _positions_list(positions: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, payload in sorted((positions or {}).items()):
        rows.append(
            {
                "symbol": str(symbol).upper(),
                "quantity": float((payload or {}).get("quantity") or 0.0),
                "entry_price": float((payload or {}).get("avg_price") or 0.0),
                "market_price": float((payload or {}).get("avg_price") or 0.0),
                "holding_days": 0,
            }
        )
    return rows


def _build_explainability(selected_candidate: dict[str, Any], universe_size: int) -> dict[str, Any]:
    symbol = str(selected_candidate.get("symbol") or "")
    component_scores = dict(selected_candidate.get("component_scores") or {})
    factor_rows = []
    for factor_id, score in sorted(component_scores.items()):
        value = float(score or 0.0)
        factor_rows.append(
            {
                "factor_id": str(factor_id),
                "factor_version": "v1",
                "factor_value": value,
                "normalized_value": max(min(value / 100.0, 1.0), -1.0),
                "percentile_rank": max(min(value, 100.0), 0.0),
                "direction": "higher_is_better",
                "name": str(factor_id),
            }
        )

    weight = 1.0 / max(len(factor_rows), 1)
    factor_weights = {str(row.get("factor_id")): weight for row in factor_rows}
    explanation = build_security_explanation(
        symbol=symbol,
        snapshot_id=f"scan:{_utc_now().strftime('%Y%m%d')}",
        factor_rows=factor_rows,
        factor_weights=factor_weights,
        universe_size=int(universe_size),
        final_rank=int(selected_candidate.get("rank") or 0) or None,
    )
    explanation["reason_selected"] = "highest-ranked eligible scanner candidate after portfolio selection constraints"
    return explanation


def _factor_intelligence_step(database_url: str | None) -> dict[str, Any]:
    registry = build_default_registry().list_factors(active_only=True)
    factor_ids = [item.factor_id for item in registry][: min(8, len(registry))]
    config = FactorIntelligenceConfig(
        start_date=None,
        end_date=None,
        forward_horizon=20,
        factor_ids=factor_ids,
        factor_versions={},
        minimum_sample_size=20,
        bucket_count=10,
        regime_filter=None,
        universe_filter=None,
        benchmark_mode="standard",
        force_recompute=False,
    )
    engine = FactorIntelligenceEngine(database_url=database_url)
    try:
        return engine.run(config)
    finally:
        engine.close()


def _execution_fingerprint(symbol: str, quantity: float, mode: str) -> str:
    payload = {
        "symbol": str(symbol).upper(),
        "quantity": round(float(quantity), 6),
        "mode": str(mode).upper(),
        "trade_date": _utc_now().date().isoformat(),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]


def _compute_expected_positions(
    positions: dict[str, dict[str, float]],
    symbol: str,
    side: str,
    quantity: float,
    fill_price: float,
) -> dict[str, dict[str, float]]:
    expected = {
        str(sym).upper(): {
            "quantity": float((payload or {}).get("quantity") or 0.0),
            "avg_price": float((payload or {}).get("avg_price") or 0.0),
        }
        for sym, payload in dict(positions or {}).items()
    }
    sym = str(symbol).upper()
    pos = expected.setdefault(sym, {"quantity": 0.0, "avg_price": float(fill_price)})
    if str(side).upper() == "BUY":
        pos["quantity"] = float(pos.get("quantity") or 0.0) + float(quantity)
        pos["avg_price"] = float(fill_price)
    else:
        pos["quantity"] = max(float(pos.get("quantity") or 0.0) - float(quantity), 0.0)
        if pos["quantity"] <= 0:
            expected.pop(sym, None)
    return expected


def run_sprint_10_2_execution_validation(
    database_url: str | None,
    profile: ExecutionProfile | None = None,
    manual_approval: str = "NO",
    symbols: list[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    selected_profile = profile or ExecutionProfile()
    if str(selected_profile.mode).upper() == "LIVE":
        raise RuntimeError("LIVE mode is rejected")

    started = time.perf_counter()
    market = _market_snapshot()
    if market.get("stale") or not market.get("sufficient_history"):
        raise RuntimeError("market data failed freshness or history checks")

    selected_symbols = [str(item).upper() for item in (symbols or SAMPLE_SYMBOLS) if str(item).strip()]
    universe = [{"symbol": sym, "company_name": sym, "sector": "Unknown", "industry": "Unknown"} for sym in selected_symbols]

    scan_payload = run_scan(universe)
    ranked = list(scan_payload.get("ranked_candidates") or [])

    research_run_id = f"research-{_utc_now().strftime('%Y%m%d%H%M%S%f')}"
    research_journal = journal_scanner_run(
        scanner_payload=scan_payload,
        research_run_id=research_run_id,
        database_url=database_url,
        data_source="live",
        data_mode="research",
    )

    factor_intelligence_error = ""
    factor_intelligence: dict[str, Any] = {}
    try:
        factor_intelligence = _factor_intelligence_step(database_url=database_url)
    except Exception as exc:
        factor_intelligence_error = f"{type(exc).__name__}: {exc}"

    broker = SimulatedFillBroker(mode=selected_profile.mode, buying_power=10000.0, positions={})
    pre_positions = broker.get_positions()
    cash_before = float(broker.get_buying_power() or 0.0)

    shortlist = run_shortlist_only(
        scan_payload,
        positions=_positions_list(pre_positions),
        cash=cash_before,
        portfolio_value=cash_before,
    )
    selected = list(shortlist.get("selected") or [])

    if not selected:
        top = ranked[0] if ranked else {}
        top_symbol = str(top.get("symbol") or "")
        top_scan = next((row for row in list(scan_payload.get("scan_results") or []) if str(row.get("symbol") or "") == top_symbol), {})
        filter_counts = Counter(str(reason) for row in list(scan_payload.get("scan_results") or []) for reason in list(row.get("rejection_reasons") or []))
        return {
            "status": "no_trade",
            "market": market,
            "universe_size": len(universe),
            "qualified_securities": 0,
            "highest_ranked_security": {
                "symbol": top_symbol,
                "overall_score": float(top.get("overall_score") or 0.0),
                "failure_reasons": list(top_scan.get("rejection_reasons") or []),
            },
            "blocked_filters": [{"filter": key, "count": value} for key, value in sorted(filter_counts.items(), key=lambda item: (-item[1], item[0]))],
            "research_journal": research_journal,
            "factor_intelligence": factor_intelligence,
            "factor_intelligence_error": factor_intelligence_error,
            "dashboard_updated": False,
            "duration_seconds": round(time.perf_counter() - started, 6),
        }

    selected_candidate = dict(selected[0])
    symbol = str(selected_candidate.get("symbol") or "")
    reference_row = next((row for row in list(scan_payload.get("scan_results") or []) if str(row.get("symbol") or "") == symbol), {})
    reference_price = float(reference_row.get("latest_price") or 0.0)
    if reference_price <= 0:
        raise RuntimeError("selected symbol missing valid latest price")

    explainability = _build_explainability(selected_candidate=selected_candidate, universe_size=len(universe))

    approval_required = True
    approval_granted = str(manual_approval).strip().upper() == "YES"
    if not approval_granted:
        return {
            "status": "approval_rejected",
            "market": market,
            "universe_size": len(universe),
            "qualified_securities": len(selected),
            "selected_symbol": symbol,
            "overall_score": float(selected_candidate.get("score") or selected_candidate.get("overall_score") or 0.0),
            "confidence": float(selected_candidate.get("confidence") or 0.0),
            "explainability": explainability,
            "approval": {"required": approval_required, "granted": False},
            "dashboard_updated": False,
            "duration_seconds": round(time.perf_counter() - started, 6),
        }

    risk_manager = RiskManager(
        max_position_size=float(selected_profile.max_position_size),
        max_daily_loss=float(selected_profile.max_daily_loss),
        daily_loss_limit=float(selected_profile.daily_loss_limit),
    )

    suggested_notional = float(selected_candidate.get("suggested_paper_notional") or 0.0)
    notional = min(max(suggested_notional, 0.0), cash_before * float(selected_profile.max_position_size))
    if notional <= 0:
        notional = min(cash_before * 0.1, cash_before)
    quantity = round(notional / reference_price, 4)
    if quantity <= 0:
        raise RuntimeError("calculated quantity is zero")
    notional = round(quantity * reference_price, 6)

    execution_fp = _execution_fingerprint(symbol=symbol, quantity=quantity, mode=selected_profile.mode)
    repo = MonitoringPaperExecutionRepository(database_url=database_url)

    approved_by_risk = risk_manager.approve_trade(cash_before, notional, current_loss=0.0)
    risk_checks = {
        "position_size": bool(notional <= cash_before * float(selected_profile.max_position_size)),
        "cash": bool(cash_before >= notional),
        "buying_power": bool(cash_before >= notional),
        "daily_loss": bool(risk_manager.daily_loss < float(selected_profile.daily_loss_limit)),
        "existing_position": bool(symbol not in pre_positions),
        "duplicate_protection": True,
    }

    existing = None
    if persist:
        existing = repo.fetch_latest_submitting_run_by_execution_fingerprint(execution_fp)
        risk_checks["duplicate_protection"] = existing is None

    if not approved_by_risk or not all(risk_checks.values()):
        repo.close()
        return {
            "status": "risk_rejected",
            "market": market,
            "universe_size": len(universe),
            "qualified_securities": len(selected),
            "selected_symbol": symbol,
            "overall_score": float(selected_candidate.get("score") or selected_candidate.get("overall_score") or 0.0),
            "confidence": float(selected_candidate.get("confidence") or 0.0),
            "explainability": explainability,
            "approval": {"required": approval_required, "granted": True},
            "risk_result": {"approved": False, "checks": risk_checks, "duplicate_run": existing},
            "dashboard_updated": False,
            "duration_seconds": round(time.perf_counter() - started, 6),
        }

    approval_id = f"{selected_profile.profile_id}-{execution_fp}"
    approval_payload = build_approval_record(
        approval_id=approval_id,
        strategy_id=selected_profile.profile_id,
        strategy_version="10.2",
        strategy_fingerprint=execution_fp,
        portfolio_configuration={"top_n": 1, "weighting_method": "equal_weight", "maximum_orders": 1},
        risk_configuration={
            "max_position_size": float(selected_profile.max_position_size),
            "max_daily_loss": float(selected_profile.max_daily_loss),
            "daily_loss_limit": float(selected_profile.daily_loss_limit),
        },
        benchmark=BENCHMARK_SYMBOL,
        horizon=20,
        approved_by="sprint10_2",
        approved_at=_utc_iso(),
        expires_at=(_utc_now() + timedelta(days=1)).isoformat(),
        enabled=True,
        notes="Sprint 10.2 execution validation",
    )

    if persist:
        repo.create_approval(approval_payload)

    fill = broker.submit_order(side="buy", ticker=symbol, quantity=quantity, reference_price=reference_price)
    fill_price = float(fill.get("average_fill_price") or reference_price)
    filled_qty = float(fill.get("filled_quantity") or quantity)
    order_id = str(fill.get("order_id") or "")
    post_cash = float(broker.get_buying_power() or cash_before)
    post_positions = broker.get_positions()

    expected_cash = round(cash_before - (filled_qty * fill_price), 6)
    expected_positions = _compute_expected_positions(pre_positions, symbol=symbol, side="BUY", quantity=filled_qty, fill_price=fill_price)

    planned_positions = {symbol: {"quantity": float((expected_positions.get(symbol) or {}).get("quantity") or 0.0), "weight": 1.0}}
    actual_positions = {symbol: {"quantity": float((post_positions.get(symbol) or {}).get("quantity") or 0.0), "weight": 1.0}}
    reconciliation = reconcile_paper_positions(
        planned_positions=planned_positions,
        actual_positions=actual_positions,
        expected_cash=expected_cash,
        actual_cash=post_cash,
        expected_buying_power=expected_cash,
        actual_buying_power=post_cash,
        orders=[
            {
                "submission_status": "filled",
                "filled_quantity": filled_qty,
                "quantity": quantity,
                "average_fill_price": fill_price,
            }
        ],
        tolerance=0.01,
    )

    run_id = f"sprint-10-2-{_utc_now().strftime('%Y%m%d%H%M%S%f')}"
    run_payload = {
        "run_id": run_id,
        "run_fingerprint": execution_fp,
        "execution_fingerprint": execution_fp,
        "approval_id": approval_id,
        "strategy_id": selected_profile.profile_id,
        "strategy_version": "10.2",
        "strategy_fingerprint": execution_fp,
        "research_run_id": str(research_journal.get("research_run_id") or ""),
        "scanner_timestamp": str(market.get("market_timestamp") or ""),
        "started_at": _utc_iso(),
        "completed_at": _utc_iso(),
        "mode": selected_profile.mode,
        "status": "completed",
        "dry_run": False,
        "proposed_order_count": 1,
        "approved_order_count": 1,
        "rejected_order_count": 0,
        "submitted_order_count": 1,
        "filled_order_count": 1,
        "failed_order_count": 0,
        "configuration": {
            "portfolio": {"top_n": 1, "maximum_orders": 1},
            "risk": risk_checks,
            "expected_positions": expected_positions,
            "expected_cash": expected_cash,
            "expected_buying_power": expected_cash,
        },
        "risk_snapshot": {
            "max_position_size": selected_profile.max_position_size,
            "max_daily_loss": selected_profile.max_daily_loss,
            "daily_loss_limit": selected_profile.daily_loss_limit,
            "rejection_reasons": {},
        },
        "performance": {
            "total_duration": round(time.perf_counter() - started, 6),
        },
        "warnings": [],
        "error_message": None,
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
    }

    order_row = {
        "paper_order_id": f"{run_id}-0001",
        "symbol": symbol,
        "side": "BUY",
        "quantity": quantity,
        "notional": notional,
        "target_weight": 1.0,
        "current_weight": 0.0,
        "weight_delta": 1.0,
        "reference_price": reference_price,
        "proposed_at": _utc_iso(),
        "risk_status": "approved",
        "risk_reason": "approved",
        "submission_status": "filled",
        "broker_order_id": order_id,
        "submitted_at": _utc_iso(),
        "filled_quantity": filled_qty,
        "average_fill_price": fill_price,
        "filled_at": _utc_iso(),
        "error_message": None,
        "order_payload": {"source": "sprint_10_2_execution_validation"},
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
    }

    snapshot_row = {
        "snapshot_id": f"{run_id}-post",
        "captured_at": _utc_iso(),
        "positions": post_positions,
        "cash": post_cash,
        "buying_power": post_cash,
        "portfolio_value": post_cash + sum(float((v or {}).get("quantity") or 0.0) * float((v or {}).get("avg_price") or 0.0) for v in post_positions.values()),
        "gross_exposure": 1.0,
        "net_exposure": 1.0,
        "concentration": {},
        "reconciliation_status": reconciliation.get("reconciliation_status"),
        "warnings": reconciliation.get("warnings") or [],
    }

    if persist:
        repo.save_validation_run(PaperValidationRunPayload(run=run_payload, orders=[order_row], position_snapshots=[snapshot_row]))
    repo.close()

    dashboard_payload = fetch_paper_validation_dashboard_payload(database_url)
    dashboard_updated = str((dashboard_payload.get("latest_run") or {}).get("run_id") or "") == run_id

    return {
        "status": "completed",
        "market": market,
        "universe_size": len(universe),
        "qualified_securities": len(selected),
        "selected_symbol": symbol,
        "overall_score": float(selected_candidate.get("score") or selected_candidate.get("overall_score") or 0.0),
        "confidence": float(selected_candidate.get("confidence") or 0.0),
        "ranking": {
            "universe_rank": int(selected_candidate.get("rank") or 0),
            "universe_size": len(universe),
        },
        "explainability": explainability,
        "approval": {"required": approval_required, "granted": True, "approval_id": approval_id},
        "risk_result": {"approved": True, "checks": risk_checks},
        "paper_order": {
            "order_id": order_id,
            "symbol": symbol,
            "shares": quantity,
            "fill_price": fill_price,
            "timestamp": _utc_iso(),
        },
        "cash_before": cash_before,
        "cash_after": post_cash,
        "buying_power": post_cash,
        "open_positions": len(post_positions),
        "reconciliation": reconciliation,
        "research_journal": research_journal,
        "factor_intelligence": factor_intelligence,
        "factor_intelligence_error": factor_intelligence_error,
        "dashboard_updated": bool(dashboard_updated),
        "dashboard_payload": dashboard_payload,
        "duration_seconds": round(time.perf_counter() - started, 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 10.2 execution validation runner")
    parser.add_argument("--database-url", default="sqlite:///ignore.db")
    parser.add_argument("--manual-approval", default="NO")
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()

    symbols = [part.strip().upper() for part in str(args.symbols or "").split(",") if part.strip()]
    result = run_sprint_10_2_execution_validation(
        database_url=args.database_url,
        manual_approval=args.manual_approval,
        symbols=symbols or None,
        persist=True,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
