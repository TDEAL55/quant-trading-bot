from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config import BENCHMARK_SYMBOL
from paper_validation import create_approval, run_paper_validation
from paper_validation_data import fetch_paper_validation_dashboard_payload
from research_journal import journal_scanner_run
from scanner_runner import SAMPLE_SYMBOLS, run_scan, run_shortlist_only


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class PaperTestProfile:
    profile_id: str = "sprint_10_1_controlled"
    mode: str = "PAPER"
    benchmark: str = BENCHMARK_SYMBOL
    horizon: int = 20
    top_n: int = 1
    min_holdings: int = 1
    max_position_weight: float = 0.25
    sector_cap: float = 1.00
    maximum_orders: int = 1
    minimum_order_notional: float = 25.0
    maximum_order_notional: float = 2000.0
    rebalance_tolerance: float = 0.01
    cash_buffer: float = 0.10
    allow_fractional: bool = False
    quantity_precision: int = 4
    require_manual_approval: bool = True

    def portfolio_configuration(self) -> dict[str, Any]:
        return {
            "top_n": int(self.top_n),
            "weighting_method": "equal_weight",
            "min_holdings": int(self.min_holdings),
            "max_position_weight": float(self.max_position_weight),
            "sector_cap": float(self.sector_cap),
            "maximum_orders": int(self.maximum_orders),
            "minimum_order_notional": float(self.minimum_order_notional),
            "maximum_order_notional": float(self.maximum_order_notional),
            "rebalance_tolerance": float(self.rebalance_tolerance),
            "cash_buffer": float(self.cash_buffer),
            "allow_fractional": bool(self.allow_fractional),
            "quantity_precision": int(self.quantity_precision),
        }

    def risk_configuration(self) -> dict[str, Any]:
        return {
            "max_position_size": float(self.max_position_weight),
            "max_daily_loss": 500.0,
            "daily_loss_limit": 500.0,
        }


class SimulatedFillBroker:
    """Simple in-memory broker for deterministic paper fills."""

    def __init__(self, mode: str = "PAPER", buying_power: float = 10000.0, positions: dict[str, dict[str, float]] | None = None):
        selected_mode = str(mode or "PAPER").upper()
        if selected_mode == "LIVE":
            raise RuntimeError("LIVE mode is rejected")
        self.mode = selected_mode
        self._buying_power = float(buying_power)
        self._positions = {
            str(symbol).upper(): {
                "quantity": float((payload or {}).get("quantity") or 0.0),
                "avg_price": float((payload or {}).get("avg_price") or 0.0),
            }
            for symbol, payload in dict(positions or {}).items()
        }

    def get_positions(self) -> dict[str, dict[str, float]]:
        return {
            symbol: {"quantity": float(payload.get("quantity") or 0.0), "avg_price": float(payload.get("avg_price") or 0.0)}
            for symbol, payload in self._positions.items()
        }

    def get_buying_power(self) -> float:
        return float(self._buying_power)

    def submit_order(self, side: str, ticker: str, quantity: float, **kwargs: Any) -> dict[str, Any]:
        symbol = str(ticker or "").upper()
        normalized_side = str(side or "").strip().lower()
        qty = max(float(quantity or 0.0), 0.0)
        fill_price = float(kwargs.get("reference_price") or 100.0)
        if not symbol or qty <= 0 or fill_price <= 0:
            raise RuntimeError("invalid simulated order")

        position = self._positions.setdefault(symbol, {"quantity": 0.0, "avg_price": fill_price})
        if normalized_side == "buy":
            position["quantity"] = float(position.get("quantity") or 0.0) + qty
            position["avg_price"] = fill_price
            self._buying_power -= qty * fill_price
        elif normalized_side == "sell":
            position["quantity"] = max(float(position.get("quantity") or 0.0) - qty, 0.0)
            self._buying_power += qty * fill_price
            if position["quantity"] <= 0:
                self._positions.pop(symbol, None)
        else:
            raise RuntimeError("invalid side")

        return {
            "status": "filled",
            "filled_quantity": qty,
            "average_fill_price": fill_price,
            "order_id": f"sim-{symbol}-{int(time.time() * 1000)}",
        }


def _symbol_records_from_symbols(symbols: list[str]) -> list[dict[str, Any]]:
    return [{"symbol": symbol, "company_name": symbol, "sector": "Unknown", "industry": "Unknown"} for symbol in symbols]


def _build_explainability(candidate: dict[str, Any]) -> dict[str, Any]:
    components = dict(candidate.get("component_scores") or {})
    ranked_components = sorted(components.items(), key=lambda item: (-float(item[1] or 0.0), str(item[0])))
    top_components = [{"factor": key, "score": float(value)} for key, value in ranked_components[:3]]
    return {
        "symbol": str(candidate.get("symbol") or ""),
        "signal": str(candidate.get("signal") or "HOLD"),
        "overall_score": float(candidate.get("overall_score") or 0.0),
        "confidence": float(candidate.get("confidence") or 0.0),
        "top_factor_components": top_components,
        "reasons": list(candidate.get("reasons") or []),
        "warnings": list(candidate.get("warnings") or []),
    }


def _summarize_rejections(scan_payload: dict[str, Any]) -> dict[str, Any]:
    reason_counts: dict[str, int] = {}
    for row in list(scan_payload.get("scan_results") or []):
        for reason in list(row.get("rejection_reasons") or []):
            key = str(reason).strip() or "unknown"
            reason_counts[key] = reason_counts.get(key, 0) + 1
    ordered = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    return {
        "reason_counts": [{"reason": key, "count": value} for key, value in ordered],
    }


def _approval_id(profile: PaperTestProfile, strategy_fingerprint: str) -> str:
    return f"{profile.profile_id}-{strategy_fingerprint}"


def _strategy_fingerprint(profile: PaperTestProfile, candidate: dict[str, Any], run_tag: str) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "symbol": str(candidate.get("symbol") or ""),
        "signal": str(candidate.get("signal") or ""),
        "score": float(candidate.get("overall_score") or 0.0),
        "run_tag": run_tag,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]


def run_sprint_10_1_validation(
    database_url: str | None,
    profile: PaperTestProfile | None = None,
    manual_approval: str = "NO",
    approved_by: str = "sprint10_1_runner",
    execute: bool = True,
    persist: bool = True,
    symbols: list[str] | None = None,
    broker: Any | None = None,
    run_tag: str | None = None,
) -> dict[str, Any]:
    selected_profile = profile or PaperTestProfile()
    if str(selected_profile.mode).upper() == "LIVE":
        raise RuntimeError("LIVE mode is rejected")

    started = time.perf_counter()
    validation_run_tag = str(run_tag or _utc_now().strftime("%Y%m%d%H%M%S%f"))
    selected_symbols = [str(item).upper() for item in (symbols or SAMPLE_SYMBOLS) if str(item).strip()]
    universe = _symbol_records_from_symbols(selected_symbols)

    scan_payload = run_scan(universe)
    broker_instance = broker or SimulatedFillBroker(mode=selected_profile.mode)
    current_positions = list((broker_instance.get_positions() or {}).values())
    shortlist_payload = run_shortlist_only(
        scan_payload,
        positions=current_positions,
        cash=float(broker_instance.get_buying_power() or 0.0),
        portfolio_value=float(broker_instance.get_buying_power() or 0.0),
    )

    research_run_id = f"research-{validation_run_tag}"
    journal_result: dict[str, Any] | None = None
    journal_error = ""
    try:
        journal_result = journal_scanner_run(
            scanner_payload=scan_payload,
            research_run_id=research_run_id,
            database_url=database_url,
            data_source="live",
            data_mode="research",
        )
    except Exception as exc:
        journal_error = f"{type(exc).__name__}: {exc}"

    selected_rows = list(shortlist_payload.get("selected") or [])
    ranked_rows = list(scan_payload.get("ranked_candidates") or [])
    selected_candidate = selected_rows[0] if selected_rows else (ranked_rows[0] if ranked_rows else {})
    explainability = _build_explainability(selected_candidate) if selected_candidate else {}
    rejection_summary = _summarize_rejections(scan_payload)

    manual_required = bool(selected_profile.require_manual_approval)
    approved = (not manual_required) or str(manual_approval).strip().upper() == "YES"
    if not approved:
        return {
            "status": "approval_rejected",
            "profile": selected_profile.__dict__,
            "manual_approval": {"required": manual_required, "approved": False, "value": str(manual_approval)},
            "scanner": scan_payload,
            "shortlist": shortlist_payload,
            "no_candidate": {
                "reason_counts": rejection_summary.get("reason_counts") or [],
                "closest_candidate": selected_candidate,
            },
            "explainability": explainability,
            "research_journal": journal_result or {"status": "error", "error": journal_error},
            "duration_seconds": round(time.perf_counter() - started, 6),
        }

    if not selected_rows:
        return {
            "status": "no_candidate",
            "profile": selected_profile.__dict__,
            "manual_approval": {"required": manual_required, "approved": True, "value": str(manual_approval)},
            "scanner": scan_payload,
            "shortlist": shortlist_payload,
            "no_candidate": {
                "reason_counts": rejection_summary.get("reason_counts") or [],
                "closest_candidate": selected_candidate,
            },
            "explainability": explainability,
            "research_journal": journal_result or {"status": "error", "error": journal_error},
            "duration_seconds": round(time.perf_counter() - started, 6),
        }

    strategy_fingerprint = _strategy_fingerprint(selected_profile, selected_candidate, validation_run_tag)
    approval_id = _approval_id(selected_profile, strategy_fingerprint)
    approved_at = _utc_iso()
    expires_at = (_utc_now() + timedelta(days=1)).isoformat()

    approval_result = create_approval(
        database_url=database_url,
        approval_id=approval_id,
        strategy_id=selected_profile.profile_id,
        strategy_version="10.1",
        strategy_fingerprint=strategy_fingerprint,
        portfolio_configuration=selected_profile.portfolio_configuration(),
        risk_configuration=selected_profile.risk_configuration(),
        benchmark=selected_profile.benchmark,
        horizon=int(selected_profile.horizon),
        approved_by=str(approved_by),
        approved_at=approved_at,
        expires_at=expires_at,
        enabled=True,
        notes="Sprint 10.1 controlled paper validation",
    )

    validation_result = run_paper_validation(
        database_url=database_url,
        approval_id=approval_id,
        mode=selected_profile.mode,
        dry_run=not bool(execute),
        execute=bool(execute),
        confirm=bool(execute),
        broker=broker_instance,
        persist=bool(persist),
    )

    metrics = dict(validation_result.get("metrics") or {})
    if int(metrics.get("submitted_orders") or 0) > int(selected_profile.maximum_orders):
        raise RuntimeError("submitted order count exceeded profile maximum")

    dashboard_payload = fetch_paper_validation_dashboard_payload(database_url)

    return {
        "status": "completed",
        "profile": selected_profile.__dict__,
        "manual_approval": {"required": manual_required, "approved": True, "value": str(manual_approval)},
        "approval": approval_result,
        "scanner": scan_payload,
        "shortlist": shortlist_payload,
        "explainability": explainability,
        "research_journal": journal_result or {"status": "error", "error": journal_error},
        "paper_validation": validation_result,
        "dashboard_payload": dashboard_payload,
        "duration_seconds": round(time.perf_counter() - started, 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 10.1 controlled paper trading validation runner")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--manual-approval", default="NO", help="Set to YES to simulate manual approval")
    parser.add_argument("--approved-by", default="sprint10_1_runner")
    parser.add_argument("--execute", action="store_true", help="Execute one paper validation trade path")
    parser.add_argument("--symbols", default="", help="Comma-separated symbol universe override")
    args = parser.parse_args()

    symbols = [part.strip().upper() for part in str(args.symbols or "").split(",") if part.strip()]
    result = run_sprint_10_1_validation(
        database_url=args.database_url,
        manual_approval=args.manual_approval,
        approved_by=args.approved_by,
        execute=bool(args.execute),
        persist=True,
        symbols=symbols or None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
