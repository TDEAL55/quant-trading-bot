from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping

from alpaca_live_broker import AlpacaLiveBroker
from correlation_engine import CorrelationPolicy, assess_symbol_correlation
from live_risk_policy import (
    LiveRiskSettings,
    evaluate_live_performance_controls,
    evaluate_live_readiness,
    live_entry_notional,
    settings_from_environment,
    stop_risk_position_size,
)
from scanner_runner import _symbol_records_from_list, run_scan
from stock_pnl_reconstruction import reconstruct_stock_realized_pnl
from strategies.paper_strategy_plugins import evaluate_all_strategies


FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day"}
ACCEPTED_ORDER_STATUSES = {"accepted", "new", "pending", "pending_new", "partially_filled", "filled"}
REGIME_STRATEGY_ROUTES = {
    "bull": ("stock_trend_pullback_v3", "stock_trend_ensemble_v2"),
    "weak_bull": ("stock_trend_pullback_v3", "stock_trend_ensemble_v2", "stock_mean_reversion_v2"),
    "sideways": ("stock_mean_reversion_v2",),
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_stock_universe(
    broker: Any,
    policy: LiveRiskSettings,
    env: Mapping[str, str],
) -> tuple[LiveRiskSettings, list[dict[str, Any]]]:
    if not _is_true(env.get("LIVE_FULL_STOCK_UNIVERSE", "true")):
        return policy, _symbol_records_from_list(list(policy.allowed_symbols))
    records = list(
        broker.get_tradable_stock_assets(
            include_etfs=_is_true(env.get("LIVE_INCLUDE_ETFS", "false"))
        )
        or []
    )
    maximum = max(int(env.get("LIVE_MAX_UNIVERSE_SIZE", "0") or 0), 0)
    if maximum:
        records = records[:maximum]
    symbols = tuple(str(item.get("symbol") or "").strip().upper() for item in records if item.get("symbol"))
    if not symbols:
        raise RuntimeError("alpaca_full_stock_universe_empty")
    return replace(policy, allowed_symbols=symbols), records


class LiveStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"orders_by_date": {}, "submissions": []}
        return dict(payload or {})

    def orders_submitted_on(self, date_key: str) -> int:
        state = self.load()
        return int(dict(state.get("orders_by_date") or {}).get(str(date_key), 0) or 0)

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(dict(state), stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def record_position_observations(
        self,
        positions: Mapping[str, Mapping[str, Any]],
        *,
        observed_at: datetime,
    ) -> None:
        """Persist price observations used for evidence-based MFE/MAE review."""
        state = self.load()
        paths = dict(state.get("price_paths") or {})
        timestamp = observed_at.astimezone(timezone.utc).isoformat()
        for raw_symbol, raw_position in dict(positions or {}).items():
            symbol = str(raw_symbol or "").strip().upper()
            position = dict(raw_position or {})
            current = _as_float(
                position.get("current_price") or position.get("market_price") or position.get("price"),
                0.0,
            )
            if not symbol or current <= 0:
                continue
            rows = list(paths.get(symbol) or [])
            rows.append({"timestamp": timestamp, "high": current, "low": current, "source": "live_cycle_observation"})
            paths[symbol] = rows[-10000:]
        state["price_paths"] = paths
        self._save(state)

    def record_account_observation(
        self,
        account: Mapping[str, Any],
        *,
        observed_at: datetime,
    ) -> None:
        state = self.load()
        rows = list(state.get("equity_history") or [])
        rows.append(
            {
                "timestamp": observed_at.astimezone(timezone.utc).isoformat(),
                "equity": _as_float(account.get("equity"), 0.0),
                "cash": _as_float(account.get("cash"), 0.0),
            }
        )
        state["equity_history"] = rows[-10000:]
        self._save(state)

    def record_cycle_result(self, result: Mapping[str, Any], *, recorded_at: str | None = None) -> None:
        """Persist the latest runner decision so the dashboard can explain pauses."""
        state = self.load()
        payload = dict(result or {})
        state["last_cycle"] = {
            "recorded_at": str(recorded_at or _utc_iso()),
            "status": str(payload.get("status") or "unknown"),
            "reasons": [str(item) for item in list(payload.get("reasons") or [])],
            "submitted": bool(payload.get("submitted")),
            "readiness": dict(payload.get("readiness") or {}),
            "candidate_symbol": str(dict(payload.get("candidate") or {}).get("symbol") or ""),
            "scan_summary": dict(payload.get("scan_summary") or {}),
        }
        self._save(state)

    def record_market_bars(
        self,
        price_history_by_symbol: Mapping[str, Any],
        *,
        symbols: set[str],
    ) -> None:
        """Merge scanner OHLC bars for held symbols into the durable review path."""
        if not symbols:
            return
        state = self.load()
        paths = dict(state.get("price_paths") or {})
        for symbol in sorted(symbols):
            incoming = list(dict(price_history_by_symbol or {}).get(symbol) or [])
            existing = list(paths.get(symbol) or [])
            merged: dict[str, dict[str, Any]] = {
                str(row.get("timestamp") or row.get("date") or ""): dict(row)
                for row in existing
                if isinstance(row, dict) and (row.get("timestamp") or row.get("date"))
            }
            for raw in incoming:
                row = dict(raw or {}) if isinstance(raw, dict) else {}
                timestamp = str(row.get("timestamp") or row.get("date") or row.get("t") or "")
                close = _as_float(row.get("close") or row.get("price") or row.get("c"), 0.0)
                high = _as_float(row.get("high") or row.get("h"), close)
                low = _as_float(row.get("low") or row.get("l"), close)
                if timestamp and high > 0 and low > 0:
                    merged[timestamp] = {
                        "timestamp": timestamp,
                        "high": high,
                        "low": low,
                        "source": "scanner_market_bar",
                    }
            paths[symbol] = [merged[key] for key in sorted(merged)][-10000:]
        state["price_paths"] = paths
        self._save(state)

    def record_submission(self, order: Mapping[str, Any], *, date_key: str, strategy: Mapping[str, Any]) -> None:
        state = self.load()
        orders_by_date = dict(state.get("orders_by_date") or {})
        orders_by_date[str(date_key)] = int(orders_by_date.get(str(date_key), 0) or 0) + 1
        submissions = list(state.get("submissions") or [])
        supporting = dict(strategy.get("supporting_factors") or {})
        confirmations = dict(supporting.get("confirmations") or {})
        components = dict(supporting.get("components") or {})
        factor_values = dict(supporting.get("factor_values") or {})
        confirmation_categories = sorted(str(name) for name, passed in confirmations.items() if passed)
        reference_price = _as_float(order.get("reference_price"), 0.0)
        stop_price = _as_float(order.get("stop_price"), 0.0)
        quantity = _as_float(order.get("requested_quantity"), 0.0)
        submissions.append(
            {
                "recorded_at": _utc_iso(),
                "date": str(date_key),
                "order_id": str(order.get("order_id") or ""),
                "client_order_id": str(order.get("client_order_id") or ""),
                "symbol": str(order.get("symbol") or "").upper(),
                "status": str(order.get("status") or "unknown").lower(),
                "quantity": quantity,
                "reference_price": reference_price,
                "stop_price": stop_price,
                "target_price": _as_float(order.get("target_price"), 0.0),
                "strategy_id": str(strategy.get("strategy_id") or ""),
                "strategy_version": str(strategy.get("strategy_version") or ""),
                "market_regime": str(strategy.get("market_regime") or strategy.get("ensemble_route") or "unknown"),
                "strategy_score": _as_float(strategy.get("strategy_score"), 0.0),
                "confidence": _as_float(strategy.get("confidence"), 0.0),
                "expected_reward_risk": _as_float(strategy.get("expected_reward_risk"), 0.0),
                "entry_reason": str(strategy.get("entry_reason") or ""),
                "target_or_exit_rule": str(strategy.get("target_or_exit_rule") or ""),
                "eligible_strategy_ids": list(strategy.get("eligible_strategy_ids") or []),
                "confirmation_categories": confirmation_categories,
                "confirmations": confirmations,
                "trend_strength": components.get("trend") or factor_values.get("trend_strength"),
                "volatility": components.get("volatility") or factor_values.get("volatility"),
                "volume": components.get("volume") or factor_values.get("volume"),
                "relative_strength": components.get("relative_strength") or factor_values.get("relative_strength"),
                "position_risk_dollars": round(max(reference_price - stop_price, 0.0) * quantity, 6),
                "rule_checks": dict(strategy.get("rule_checks") or {}),
            }
        )
        state.update({"orders_by_date": orders_by_date, "submissions": submissions[-5000:]})
        self._save(state)


def _protective_order_symbols(
    positions: Mapping[str, Mapping[str, Any]],
    open_orders: list[Mapping[str, Any]],
) -> tuple[set[str], list[dict[str, Any]], list[str]]:
    held = {str(symbol).upper() for symbol in dict(positions or {})}
    protective_counts: dict[str, int] = {symbol: 0 for symbol in held}
    unreconciled: list[dict[str, Any]] = []
    for raw_order in open_orders or []:
        order = dict(raw_order or {})
        symbol = str(order.get("symbol") or "").upper()
        side = str(order.get("side") or "").lower()
        status = str(order.get("status") or "").lower()
        if status in FINAL_ORDER_STATUSES:
            continue
        if symbol in held and side == "sell":
            protective_counts[symbol] = protective_counts.get(symbol, 0) + 1
        else:
            unreconciled.append(order)
    protected = {symbol for symbol, count in protective_counts.items() if count >= 2}
    unprotected = sorted(held.difference(protected))
    return protected, unreconciled, unprotected


def select_live_candidate(
    scan_payload: Mapping[str, Any],
    *,
    settings: LiveRiskSettings,
    positions: Mapping[str, Mapping[str, Any]],
    maximum_notional: float,
    account_equity: float,
) -> dict[str, Any] | None:
    held = {str(symbol).upper() for symbol in dict(positions or {})}
    allowed = set(settings.allowed_symbols)
    for raw_candidate in list(scan_payload.get("ranked_candidates") or []):
        candidate = dict(raw_candidate or {})
        symbol = str(candidate.get("symbol") or "").upper()
        price = _as_float(candidate.get("latest_price"), 0.0)
        if not symbol or symbol not in allowed or symbol in held or price <= 0:
            continue
        signals = [dict(item or {}) for item in evaluate_all_strategies(candidate)]
        regime = str(
            ((candidate.get("quantum_score") or {}).get("market_regime"))
            or next((item.get("market_regime") for item in signals if item.get("market_regime")), "unknown")
        ).strip().lower()
        routed_ids = set(REGIME_STRATEGY_ROUTES.get(regime, ()))
        eligible_signals = [
            item
            for item in signals
            if str(item.get("strategy_id") or "") in routed_ids
            and str(item.get("signal") or "").upper() == "BUY"
            and _as_float(item.get("strategy_score"), 0.0) >= settings.minimum_strategy_score
            and _as_float(item.get("confidence"), 0.0) >= settings.minimum_confidence
            and str(item.get("data_quality_status") or "").lower() in {"ok", "good"}
        ]
        if not eligible_signals:
            continue
        signal = max(
            eligible_signals,
            key=lambda item: (
                _as_float(item.get("strategy_score"), 0.0),
                _as_float(item.get("confidence"), 0.0),
            ),
        )
        signal["ensemble_route"] = regime
        signal["eligible_strategy_ids"] = sorted(
            str(item.get("strategy_id") or "") for item in eligible_signals
        )
        supporting = dict(signal.get("supporting_factors") or {})
        confirmations = dict(supporting.get("confirmations") or {})
        distinct_confirmation_count = sum(1 for passed in confirmations.values() if passed)
        stop = _as_float(signal.get("stop") or signal.get("stop_price"), 0.0)
        if not 0 < stop < price:
            stop = round(price * (1.0 - settings.stop_loss_percent / 100.0), 2)
        target = _as_float(signal.get("target") or signal.get("target_price"), 0.0)
        if target <= price:
            target = round(price * (1.0 + settings.take_profit_percent / 100.0), 2)

        correlation = assess_symbol_correlation(
            symbol,
            sorted(held),
            dict(scan_payload.get("price_history_by_symbol") or {}),
            CorrelationPolicy(
                lookback_days=settings.correlation_lookback_days,
                min_overlap_days=settings.correlation_min_overlap_days,
                max_correlation=settings.maximum_pair_correlation,
                allocation_reduction_factor=1.0,
            ),
        )
        if held and int(correlation.get("insufficient_pair_count") or 0) > 0:
            continue
        high_peers = {
            str(row.get("peer_symbol") or "").upper()
            for row in list(correlation.get("high_correlation_pairs") or [])
        }
        correlated_exposure = sum(
            abs(_as_float(dict(positions.get(peer) or {}).get("market_value"), 0.0))
            for peer in high_peers
        )
        correlated_room = max(
            account_equity * settings.maximum_correlated_exposure_percent / 100.0 - correlated_exposure,
            0.0,
        )
        correlation_notional_cap = min(maximum_notional, correlated_room) if high_peers else maximum_notional
        sizing = stop_risk_position_size(
            equity=account_equity,
            entry_price=price,
            stop_price=stop,
            maximum_notional=correlation_notional_cap,
            settings=settings,
        )
        quantity = int(sizing.get("quantity") or 0)
        if quantity < 1:
            continue
        signal["rule_checks"] = {
            "regime_route": str(signal.get("strategy_id") or "") in routed_ids,
            "quality_threshold": (
                _as_float(signal.get("strategy_score"), 0.0) >= settings.minimum_strategy_score
                and _as_float(signal.get("confidence"), 0.0) >= settings.minimum_confidence
            ),
            "independent_confirmations": distinct_confirmation_count >= 2,
            "risk_limit": _as_float(sizing.get("planned_risk_dollars"), 0.0) <= _as_float(sizing.get("allowed_risk_dollars"), 0.0),
            "correlation_limit": not held or int(correlation.get("insufficient_pair_count") or 0) == 0,
            "protective_exit": settings.stop_loss_percent > 0 and settings.take_profit_percent > 0,
        }
        signal["risk_sizing"] = sizing
        signal["correlation_assessment"] = correlation
        signal["correlated_exposure_before"] = round(correlated_exposure, 6)
        return {
            "symbol": symbol,
            "quantity": quantity,
            "reference_price": price,
            "notional": round(quantity * price, 2),
            "stop_price": stop,
            "target_price": target,
            "strategy": signal,
        }
    return None


def run_controlled_live_cycle(
    *,
    environ: Mapping[str, str] | None = None,
    settings: LiveRiskSettings | None = None,
    broker: Any | None = None,
    scanner: Callable[..., dict[str, Any]] = run_scan,
    state_store: LiveStateStore | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    policy = settings or settings_from_environment(env)
    now = (now_provider or (lambda: datetime.now(timezone.utc)))()
    date_key = now.astimezone(timezone.utc).date().isoformat()
    store = state_store or LiveStateStore(
        env.get("LIVE_STATE_PATH", "/var/lib/quant-bot/live-micro-state.json")
    )

    preflight_reasons: list[str] = []
    if str(env.get("TRADING_MODE", "")).strip().upper() != "LIVE":
        preflight_reasons.append("TRADING_MODE_must_be_LIVE")
    if not policy.enabled:
        preflight_reasons.append("live_trading_disabled")
    if not policy.order_submission_enabled:
        preflight_reasons.append("live_order_submission_disabled")
    if policy.kill_switch:
        preflight_reasons.append("live_kill_switch_active")
    if preflight_reasons:
        return {"status": "blocked", "reasons": preflight_reasons, "submitted": False}

    live_broker = broker or AlpacaLiveBroker(mode="LIVE", environ=env)
    try:
        policy, records = _resolve_stock_universe(live_broker, policy, env)
    except Exception as exc:
        return {
            "status": "blocked",
            "reasons": [f"stock_universe_unavailable_{type(exc).__name__}"],
            "submitted": False,
        }
    account = dict(live_broker.get_account() or {})
    store.record_account_observation(account, observed_at=now)
    positions = dict(live_broker.get_positions() or {})
    store.record_position_observations(positions, observed_at=now)
    open_orders = [dict(item or {}) for item in list(live_broker.get_open_orders() or [])]
    clock = dict(live_broker.get_market_clock() or {})
    _, unreconciled_orders, unprotected_positions = _protective_order_symbols(positions, open_orders)
    orders_submitted_today = store.orders_submitted_on(date_key)
    try:
        history_limit = 500
        order_history = [dict(item or {}) for item in list(live_broker.get_order_history(limit=history_limit) or [])]
        state = store.load()
        strategy_metadata: dict[str, Any] = {}
        for row in list(state.get("submissions") or []):
            for key in ("order_id", "client_order_id"):
                identifier = str(dict(row or {}).get(key) or "").strip()
                if identifier:
                    strategy_metadata[identifier] = dict(row or {})
        reconstruction = reconstruct_stock_realized_pnl(
            order_history,
            bot_orders_only=True,
            history_limit=history_limit,
            strategy_by_order_id=strategy_metadata,
        )
        history_complete = not bool(reconstruction.get("history_limit_reached")) and not bool(
            reconstruction.get("missing_timestamp_count") or reconstruction.get("unmatched_close_count")
        )
        performance_controls = evaluate_live_performance_controls(
            account,
            list(reconstruction.get("realized_events") or []),
            settings=policy,
            now=now,
            equity_history=list(state.get("equity_history") or []),
            history_available=True,
            history_complete=history_complete,
        )
    except Exception as exc:
        performance_controls = evaluate_live_performance_controls(
            account,
            [],
            settings=policy,
            now=now,
            equity_history=list(store.load().get("equity_history") or []),
            history_available=False,
            history_complete=False,
        )
        performance_controls["history_error_type"] = type(exc).__name__
    readiness = evaluate_live_readiness(
        account,
        positions,
        unreconciled_orders,
        settings=policy,
        market_is_open=bool(clock.get("is_open")),
        orders_submitted_today=orders_submitted_today,
    )
    if not performance_controls.get("approved"):
        readiness["approved"] = False
        readiness.setdefault("reasons", []).extend(list(performance_controls.get("reasons") or []))
    readiness["performance_controls"] = performance_controls
    if unprotected_positions:
        readiness["approved"] = False
        readiness.setdefault("reasons", []).append("unprotected_live_positions_require_manual_review")
        readiness["unprotected_positions"] = unprotected_positions
    if not readiness.get("approved"):
        return {
            "status": "blocked",
            "reasons": list(readiness.get("reasons") or []),
            "readiness": readiness,
            "submitted": False,
        }

    maximum_notional = live_entry_notional(account, positions, policy)
    if maximum_notional < 1.0:
        return {
            "status": "no_trade",
            "reasons": ["available_live_notional_below_one_dollar"],
            "readiness": readiness,
            "submitted": False,
        }

    scan_payload = scanner(records)
    store.record_market_bars(
        dict(scan_payload.get("price_history_by_symbol") or {}),
        symbols={str(symbol).upper() for symbol in positions},
    )
    candidate = select_live_candidate(
        scan_payload,
        settings=policy,
        positions=positions,
        maximum_notional=maximum_notional,
        account_equity=_as_float(account.get("equity"), 0.0),
    )
    if candidate is None:
        return {
            "status": "no_trade",
            "reasons": ["no_eligible_risk_checked_candidate"],
            "readiness": readiness,
            "scan_summary": dict(scan_payload.get("summary") or {}),
            "submitted": False,
        }

    client_order_id = f"qtb-live-micro-{date_key.replace('-', '')}-{candidate['symbol'].lower()}"
    order = dict(
        live_broker.submit_bracket_entry(
            symbol=candidate["symbol"],
            quantity=int(candidate["quantity"]),
            reference_price=float(candidate["reference_price"]),
            stop_price=float(candidate["stop_price"]),
            target_price=float(candidate["target_price"]),
            client_order_id=client_order_id,
        )
        or {}
    )
    order_status = str(order.get("status") or "unknown").lower()
    submitted = order_status in ACCEPTED_ORDER_STATUSES
    if submitted:
        store.record_submission(order, date_key=date_key, strategy=dict(candidate.get("strategy") or {}))
    return {
        "status": "submitted" if submitted else "rejected",
        "reasons": [] if submitted else [f"broker_order_status_{order_status}"],
        "readiness": readiness,
        "candidate": candidate,
        "scan_summary": dict(scan_payload.get("summary") or {}),
        "order": order,
        "submitted": submitted,
    }


def run_forever(*, interval_seconds: int = 60) -> None:
    while True:
        try:
            result = run_controlled_live_cycle()
            LiveStateStore(
                os.getenv("LIVE_STATE_PATH", "/var/lib/quant-bot/live-micro-state.json")
            ).record_cycle_result(result)
            print(json.dumps({"event": "controlled_live_cycle", "timestamp": _utc_iso(), **result}, default=str))
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "controlled_live_cycle_error",
                        "timestamp": _utc_iso(),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            )
        time.sleep(max(int(interval_seconds), 60))


def check_live_account(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read live account state without enabling or submitting orders."""
    env = dict(os.environ if environ is None else environ)
    broker = AlpacaLiveBroker(mode="LIVE", environ=env, read_only=True)
    account = dict(broker.get_account() or {})
    account.pop("account_number", None)
    positions = dict(broker.get_positions() or {})
    open_orders = list(broker.get_open_orders() or [])
    clock = dict(broker.get_market_clock() or {})
    return {
        "status": "checked",
        "submission_enabled": False,
        "account": account,
        "position_symbols": sorted(str(symbol).upper() for symbol in positions),
        "open_order_count": len(open_orders),
        "market_is_open": bool(clock.get("is_open")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Separately gated $300 micro-live stock runner")
    parser.add_argument("--once", action="store_true", help="Run one controlled live cycle")
    parser.add_argument("--show-policy", action="store_true", help="Print non-secret live risk policy")
    parser.add_argument("--check-account", action="store_true", help="Read live account state without order permission")
    args = parser.parse_args()
    if args.show_policy:
        print(json.dumps(asdict(settings_from_environment()), indent=2, sort_keys=True))
        return
    if args.check_account:
        print(json.dumps(check_live_account(), indent=2, sort_keys=True, default=str))
        return
    if args.once:
        print(json.dumps(run_controlled_live_cycle(), indent=2, sort_keys=True, default=str))
        return
    run_forever(interval_seconds=int(os.getenv("LIVE_SCAN_INTERVAL_SECONDS", "60")))


if __name__ == "__main__":
    main()
