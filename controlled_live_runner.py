from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping

from alpaca_live_broker import AlpacaLiveBroker
from live_risk_policy import (
    LiveRiskSettings,
    evaluate_live_readiness,
    live_entry_notional,
    settings_from_environment,
)
from scanner_runner import _symbol_records_from_list, run_scan
from strategies.paper_strategy_plugins import evaluate_all_strategies


FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day"}
ACCEPTED_ORDER_STATUSES = {"accepted", "new", "pending", "pending_new", "partially_filled", "filled"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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

    def record_submission(self, order: Mapping[str, Any], *, date_key: str, strategy: Mapping[str, Any]) -> None:
        state = self.load()
        orders_by_date = dict(state.get("orders_by_date") or {})
        orders_by_date[str(date_key)] = int(orders_by_date.get(str(date_key), 0) or 0) + 1
        submissions = list(state.get("submissions") or [])
        submissions.append(
            {
                "recorded_at": _utc_iso(),
                "date": str(date_key),
                "order_id": str(order.get("order_id") or ""),
                "client_order_id": str(order.get("client_order_id") or ""),
                "symbol": str(order.get("symbol") or "").upper(),
                "status": str(order.get("status") or "unknown").lower(),
                "quantity": _as_float(order.get("requested_quantity"), 0.0),
                "reference_price": _as_float(order.get("reference_price"), 0.0),
                "stop_price": _as_float(order.get("stop_price"), 0.0),
                "target_price": _as_float(order.get("target_price"), 0.0),
                "strategy_id": str(strategy.get("strategy_id") or ""),
                "strategy_version": str(strategy.get("strategy_version") or ""),
            }
        )
        state.update({"orders_by_date": orders_by_date, "submissions": submissions[-100:]})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(state, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


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
) -> dict[str, Any] | None:
    held = {str(symbol).upper() for symbol in dict(positions or {})}
    allowed = set(settings.allowed_symbols)
    for raw_candidate in list(scan_payload.get("ranked_candidates") or []):
        candidate = dict(raw_candidate or {})
        symbol = str(candidate.get("symbol") or "").upper()
        price = _as_float(candidate.get("latest_price"), 0.0)
        if not symbol or symbol not in allowed or symbol in held or price <= 0:
            continue
        quantity = int(math.floor(float(maximum_notional) / price))
        if quantity < 1:
            continue
        signals = evaluate_all_strategies(candidate)
        signal = next(
            (
                dict(item or {})
                for item in signals
                if str((item or {}).get("strategy_id") or "") == "stock_trend_pullback_v3"
            ),
            None,
        )
        if not signal or str(signal.get("signal") or "").upper() != "BUY":
            continue
        if _as_float(signal.get("strategy_score"), 0.0) < settings.minimum_strategy_score:
            continue
        if _as_float(signal.get("confidence"), 0.0) < settings.minimum_confidence:
            continue
        if str(signal.get("data_quality_status") or "").lower() not in {"ok", "good"}:
            continue
        if str(signal.get("market_regime") or "").lower() not in {"bull", "weak_bull"}:
            continue
        stop = round(price * (1.0 - settings.stop_loss_percent / 100.0), 2)
        target = round(price * (1.0 + settings.take_profit_percent / 100.0), 2)
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
    account = dict(live_broker.get_account() or {})
    positions = dict(live_broker.get_positions() or {})
    open_orders = [dict(item or {}) for item in list(live_broker.get_open_orders() or [])]
    clock = dict(live_broker.get_market_clock() or {})
    _, unreconciled_orders, unprotected_positions = _protective_order_symbols(positions, open_orders)
    orders_submitted_today = store.orders_submitted_on(date_key)
    readiness = evaluate_live_readiness(
        account,
        positions,
        unreconciled_orders,
        settings=policy,
        market_is_open=bool(clock.get("is_open")),
        orders_submitted_today=orders_submitted_today,
    )
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

    records = _symbol_records_from_list(list(policy.allowed_symbols))
    scan_payload = scanner(records)
    candidate = select_live_candidate(
        scan_payload,
        settings=policy,
        positions=positions,
        maximum_notional=maximum_notional,
    )
    if candidate is None:
        return {
            "status": "no_trade",
            "reasons": ["no_eligible_whole_share_trend_pullback_candidate"],
            "readiness": readiness,
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
        "order": order,
        "submitted": submitted,
    }


def run_forever(*, interval_seconds: int = 60) -> None:
    while True:
        try:
            result = run_controlled_live_cycle()
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
