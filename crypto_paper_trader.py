from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from alpaca_paper_broker import AlpacaPaperBroker
from crypto_market_data import AlpacaCryptoMarketData, analyze_crypto_bars
from crypto_universe import canonical_crypto_symbol, load_crypto_universe
from pnl_risk_policy import (
    evaluate_account_pnl_policy,
    load_recent_closed_trades,
    risk_adjusted_position_percent,
    settings_from_environment,
)


_TERMINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired"}


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _utc_iso(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _position_for_symbol(positions: dict[str, dict[str, Any]], symbol: str) -> dict[str, Any]:
    target = canonical_crypto_symbol(symbol)
    for raw_symbol, payload in dict(positions or {}).items():
        if canonical_crypto_symbol(raw_symbol) == target:
            return dict(payload or {})
    return {}


def _crypto_positions(positions: dict[str, dict[str, Any]], universe_symbols: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_symbol, payload in dict(positions or {}).items():
        symbol = canonical_crypto_symbol(raw_symbol)
        asset_class = str((payload or {}).get("asset_class") or "").strip().lower()
        if symbol not in universe_symbols and asset_class != "crypto" and "/" not in str(raw_symbol):
            continue
        quantity = _as_float((payload or {}).get("quantity"), 0.0)
        if quantity <= 0:
            continue
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "average_entry_price": _as_float((payload or {}).get("avg_price"), 0.0),
                "current_price": _as_float((payload or {}).get("current_price"), 0.0),
                "market_value": _as_float((payload or {}).get("market_value"), 0.0),
                "unrealized_pl": _as_float((payload or {}).get("unrealized_pl"), 0.0),
                "unrealized_plpc": _as_float((payload or {}).get("unrealized_plpc"), 0.0),
            }
        )
    return sorted(rows, key=lambda row: str(row.get("symbol") or ""))


def _quantity_for_notional(notional: float, price: float, minimum_increment: float) -> float:
    if notional <= 0 or price <= 0:
        return 0.0
    raw = notional / price
    increment = minimum_increment if minimum_increment > 0 else 0.00000001
    steps = math.floor((raw / increment) + 1e-12)
    return round(max(steps * increment, 0.0), 10)


def _client_order_id(symbol: str, side: str, now: datetime, interval_minutes: int) -> str:
    bucket = int(now.astimezone(timezone.utc).timestamp()) // max(int(interval_minutes) * 60, 60)
    digest = hashlib.sha256(f"crypto|{canonical_crypto_symbol(symbol)}|{side.upper()}|{bucket}".encode("utf-8")).hexdigest()[:24]
    return f"qtb-crypto-{digest}"


def _order_is_open(order: dict[str, Any]) -> bool:
    return str(order.get("status") or "").strip().lower() not in _TERMINAL_ORDER_STATUSES


def _order_age_minutes(order: dict[str, Any], now: datetime) -> float | None:
    raw_timestamp = str(order.get("updated_at") or order.get("submitted_at") or "").strip()
    if not raw_timestamp:
        return None
    try:
        timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max((now.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() / 60.0, 0.0)


def _clear_stale_bot_orders(
    broker: AlpacaPaperBroker,
    open_orders: list[dict[str, Any]],
    *,
    now: datetime,
    stale_after_minutes: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    active_orders: list[dict[str, Any]] = []
    canceled_orders: list[dict[str, Any]] = []
    cancellation_failures: list[dict[str, Any]] = []
    for order in open_orders:
        client_order_id = str(order.get("client_order_id") or "")
        order_id = str(order.get("order_id") or "")
        age_minutes = _order_age_minutes(order, now)
        is_stale_bot_order = bool(
            _order_is_open(order)
            and client_order_id.startswith("qtb-crypto-")
            and order_id
            and age_minutes is not None
            and age_minutes >= stale_after_minutes
        )
        if not is_stale_bot_order:
            active_orders.append(order)
            continue
        try:
            canceled = dict(broker.cancel_order(order_id) or {})
        except Exception as exc:
            active_orders.append(order)
            cancellation_failures.append(
                {
                    "order_id": order_id,
                    "symbol": canonical_crypto_symbol(order.get("symbol")),
                    "error": str(exc),
                }
            )
            continue
        canceled_record = {
            **order,
            **canceled,
            "cancellation_reason": "stale_unfilled_bot_order",
            "cancellation_explanation": "Canceled automatically because the bot order remained unfilled past the stale-order timeout.",
            "order_age_minutes": round(float(age_minutes or 0.0), 3),
            "canceled_at": str(canceled.get("updated_at") or _utc_iso(now)),
        }
        if _order_is_open(canceled_record):
            active_orders.append(canceled or order)
        else:
            canceled_orders.append(canceled_record)
    return active_orders, canceled_orders, cancellation_failures


def _load_recent_cancellations(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    return [dict(row) for row in list(payload.get("recent_cancellations") or []) if isinstance(row, dict)]


def _safe_status(
    *,
    now: datetime,
    cycle_status: str,
    universe: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    order: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "updated_at": _utc_iso(now),
        "enabled": True,
        "trading_mode": "PAPER",
        "market": "24/7",
        "cycle_status": str(cycle_status),
        "universe_count": len(universe),
        "scanned_count": len(signals),
        "eligible_count": sum(1 for row in signals if bool(row.get("eligible"))),
        "buy_signal_count": sum(1 for row in signals if str(row.get("signal") or "").upper() == "BUY"),
        "sell_signal_count": sum(1 for row in signals if str(row.get("signal") or "").upper() == "SELL"),
        "top_signal": (signals[0] if signals else {}),
        "signals": signals,
        "positions": positions,
        "open_position_count": len(positions),
        "crypto_exposure": round(sum(abs(_as_float(row.get("market_value"), 0.0)) for row in positions), 6),
        "unrealized_pl": round(sum(_as_float(row.get("unrealized_pl"), 0.0) for row in positions), 6),
        "last_order": dict(order or {}),
        "error": str(error or ""),
    }


def run_crypto_paper_cycle(
    *,
    broker_factory: Callable[..., AlpacaPaperBroker] = AlpacaPaperBroker,
    market_data_factory: Callable[..., AlpacaCryptoMarketData] = AlpacaCryptoMarketData,
    universe_loader: Callable[..., list[dict[str, Any]]] = load_crypto_universe,
    now: datetime | None = None,
    status_path: str | Path | None = None,
    trades_path: str | Path | None = None,
    dry_run: bool | None = None,
    runtime_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cycle_started = perf_counter()
    cycle_now = now or datetime.now(timezone.utc)
    if cycle_now.tzinfo is None:
        cycle_now = cycle_now.replace(tzinfo=timezone.utc)
    if str(os.getenv("TRADING_MODE", "PAPER")).strip().upper() != "PAPER":
        raise RuntimeError("crypto trader requires TRADING_MODE=PAPER")
    if not _enabled(os.getenv("CRYPTO_TRADING_ENABLED"), default=False):
        return {"cycle_status": "disabled", "confirmed_order_count": 0}

    resolved_status_path = Path(status_path or os.getenv("CRYPTO_STATUS_PATH", "/var/lib/quant-bot/crypto-status.json"))
    resolved_trades_path = Path(trades_path or os.getenv("CRYPTO_TRADES_LOG_PATH", "/var/lib/quant-bot/crypto-trades.jsonl"))
    resolved_dry_run = _enabled(os.getenv("CRYPTO_DRY_RUN"), default=False) if dry_run is None else bool(dry_run)
    interval_minutes = max(int(os.getenv("CRYPTO_SCAN_INTERVAL_MINUTES", "1")), 1)
    buy_score = _as_float(os.getenv("CRYPTO_BUY_SCORE", "60"), 60.0)
    exit_score = _as_float(os.getenv("CRYPTO_EXIT_SCORE", "40"), 40.0)
    stop_loss_percent = max(_as_float(os.getenv("CRYPTO_STOP_LOSS_PERCENT", "5"), 5.0), 0.1)
    take_profit_percent = max(_as_float(os.getenv("CRYPTO_TAKE_PROFIT_PERCENT", "10"), 10.0), 0.1)
    requested_position_percent = _as_float(os.getenv("CRYPTO_MAX_POSITION_EQUITY_PERCENT", "10"), 10.0)
    pnl_settings = settings_from_environment(
        maximum_position_percent=min(max(requested_position_percent, 0.1), 10.0)
    )
    maximum_position_percent = risk_adjusted_position_percent(
        stop_loss_percent=stop_loss_percent,
        settings=pnl_settings,
    )
    minimum_order_notional = max(_as_float(os.getenv("CRYPTO_MIN_ORDER_NOTIONAL", "10"), 10.0), 1.0)
    maximum_order_notional = min(max(_as_float(os.getenv("CRYPTO_MAX_ORDER_NOTIONAL", "200000"), 200000.0), minimum_order_notional), 200000.0)
    buying_power_usage_percent = min(max(_as_float(os.getenv("CRYPTO_BUYING_POWER_USAGE_PERCENT", "95"), 95.0), 1.0), 99.0)
    stale_order_minutes = max(_as_float(os.getenv("CRYPTO_STALE_ORDER_MINUTES", "10"), 10.0), 2.0)
    universe_refresh_seconds = max(int(os.getenv("CRYPTO_UNIVERSE_REFRESH_SECONDS", "900")), 60)
    signal_refresh_seconds = max(int(os.getenv("CRYPTO_SIGNAL_REFRESH_SECONDS", "60")), 10)
    timeframe_minutes = int(os.getenv("CRYPTO_BAR_TIMEFRAME_MINUTES", "15"))
    lookback_bars = int(os.getenv("CRYPTO_LOOKBACK_BARS", "240"))
    maximum_age_minutes = int(os.getenv("CRYPTO_MAX_DATA_AGE_MINUTES", "45"))

    cache = runtime_cache if isinstance(runtime_cache, dict) else None
    broker = cache.get("broker") if cache is not None else None
    if broker is None:
        broker = broker_factory(mode="PAPER")
        if cache is not None:
            cache["broker"] = broker

    cached_universe_at = cache.get("universe_at") if cache is not None else None
    universe_age = (
        (cycle_now - cached_universe_at).total_seconds()
        if isinstance(cached_universe_at, datetime) and cycle_now >= cached_universe_at
        else None
    )
    universe_refreshed = bool(
        cache is None
        or not cache.get("universe")
        or universe_age is None
        or universe_age >= float(universe_refresh_seconds)
    )
    if universe_refreshed:
        universe = list(universe_loader(broker_factory=lambda **_: broker) or [])
        if cache is not None:
            cache["universe"] = universe
            cache["universe_at"] = cycle_now
    else:
        universe = list(cache.get("universe") or [])
    symbols = [canonical_crypto_symbol(row.get("symbol")) for row in universe]
    symbol_set = set(symbols)
    signal_cache_key = (tuple(symbols), buy_score, exit_score, timeframe_minutes, lookback_bars, maximum_age_minutes)
    cached_signals_at = cache.get("signals_at") if cache is not None else None
    signals_age = (
        (cycle_now - cached_signals_at).total_seconds()
        if isinstance(cached_signals_at, datetime) and cycle_now >= cached_signals_at
        else None
    )
    signals_refreshed = bool(
        cache is None
        or universe_refreshed
        or cache.get("signals_key") != signal_cache_key
        or not cache.get("signals")
        or signals_age is None
        or signals_age >= float(signal_refresh_seconds)
    )
    if signals_refreshed:
        market_data = cache.get("market_data") if cache is not None else None
        if market_data is None:
            market_data = market_data_factory()
            if cache is not None:
                cache["market_data"] = market_data
        bars_by_symbol = market_data.fetch_bars(
            symbols,
            now=cycle_now,
            timeframe_minutes=timeframe_minutes,
            lookback_bars=lookback_bars,
        )
        signals = [
            analyze_crypto_bars(
                symbol,
                bars_by_symbol.get(symbol),
                buy_score=buy_score,
                exit_score=exit_score,
                now=cycle_now,
                maximum_age_minutes=maximum_age_minutes,
            )
            for symbol in symbols
            if symbol in bars_by_symbol
        ]
        signals = sorted(signals, key=lambda row: (-_as_float(row.get("score"), 0.0), str(row.get("symbol") or "")))
        if cache is not None:
            cache["signals"] = signals
            cache["signals_at"] = cycle_now
            cache["signals_key"] = signal_cache_key
    else:
        signals = list(cache.get("signals") or [])

    account = broker.get_account()
    equity = _as_float(account.get("equity") or account.get("portfolio_value"), 0.0)
    non_marginable_buying_power = _as_float(
        account.get("non_marginable_buying_power"),
        _as_float(account.get("cash"), 0.0),
    )
    positions_before = broker.get_positions()
    pnl_policy = evaluate_account_pnl_policy(
        account,
        closed_trades=load_recent_closed_trades(os.getenv("DATABASE_URL")),
        settings=pnl_settings,
        now=cycle_now,
    )
    crypto_positions_before = _crypto_positions(positions_before, symbol_set)
    open_orders = list(broker.get_open_orders() or [])
    open_orders, stale_orders_canceled, stale_order_cancel_failures = _clear_stale_bot_orders(
        broker,
        open_orders,
        now=cycle_now,
        stale_after_minutes=stale_order_minutes,
    )
    order: dict[str, Any] = {}
    action_reason = "no_crypto_order_selected"
    blocked_buy_candidates: list[str] = []

    signal_by_symbol = {str(row.get("symbol") or ""): row for row in signals}
    for position in crypto_positions_before:
        symbol = str(position.get("symbol") or "")
        signal = signal_by_symbol.get(symbol) or {}
        entry_price = _as_float(position.get("average_entry_price"), 0.0)
        current_price = _as_float(position.get("current_price"), _as_float(signal.get("latest_price"), 0.0))
        return_percent = ((current_price / entry_price) - 1.0) * 100.0 if entry_price > 0 and current_price > 0 else 0.0
        exit_reason = ""
        if return_percent <= -stop_loss_percent:
            exit_reason = "crypto_stop_loss"
        elif return_percent >= take_profit_percent:
            exit_reason = "crypto_take_profit"
        elif str(signal.get("signal") or "").upper() == "SELL":
            exit_reason = str(signal.get("reason") or "crypto_strategy_exit")
        if not exit_reason:
            continue
        if any(
            canonical_crypto_symbol(row.get("symbol")) == symbol
            and str(row.get("side") or "").lower() == "sell"
            and _order_is_open(row)
            for row in open_orders
        ):
            action_reason = "crypto_sell_order_already_open"
            break
        order_payload = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": _as_float(position.get("quantity"), 0.0),
            "reference_price": current_price,
            "reason": exit_reason,
            "client_order_id": _client_order_id(symbol, "SELL", cycle_now, interval_minutes),
        }
        if resolved_dry_run:
            order = {**order_payload, "status": "dry_run"}
        else:
            response = broker.submit_order(
                side="sell",
                ticker=symbol,
                quantity=order_payload["quantity"],
                client_order_id=order_payload["client_order_id"],
                order_type="market",
                time_in_force="gtc",
                allow_fractional=True,
                reference_price=current_price,
                wait_for_fill=True,
            )
            order = {**order_payload, **dict(response or {})}
        action_reason = exit_reason
        break

    if not order and bool(pnl_policy.get("block_new_entries")):
        action_reason = str(pnl_policy.get("reason") or "pnl_rule_blocked")
    elif not order:
        held_symbols = {str(row.get("symbol") or "") for row in crypto_positions_before}
        candidates = [
            row
            for row in signals
            if bool(row.get("eligible"))
            and str(row.get("signal") or "").upper() == "BUY"
            and str(row.get("symbol") or "") not in held_symbols
        ]
        if candidates:
            spendable_buying_power = non_marginable_buying_power * (buying_power_usage_percent / 100.0)
            for candidate in candidates:
                symbol = str(candidate.get("symbol") or "")
                if any(
                    canonical_crypto_symbol(row.get("symbol")) == symbol
                    and str(row.get("side") or "").lower() == "buy"
                    and _order_is_open(row)
                    for row in open_orders
                ):
                    blocked_buy_candidates.append(symbol)
                    continue
                price = _as_float(candidate.get("latest_price"), 0.0)
                position_cap = equity * (maximum_position_percent / 100.0)
                target_notional = min(position_cap, spendable_buying_power, maximum_order_notional)
                asset = next((row for row in universe if canonical_crypto_symbol(row.get("symbol")) == symbol), {})
                quantity = _quantity_for_notional(target_notional, price, _as_float(asset.get("min_trade_increment"), 0.0))
                notional = quantity * price
                if notional < minimum_order_notional:
                    action_reason = "insufficient_non_marginable_buying_power"
                    break
                order_payload = {
                    "symbol": symbol,
                    "side": "BUY",
                    "quantity": quantity,
                    "notional": round(notional, 6),
                    "reference_price": price,
                    "reason": str(candidate.get("reason") or "crypto_strategy_entry"),
                    "client_order_id": _client_order_id(symbol, "BUY", cycle_now, interval_minutes),
                    "maximum_position_percent": maximum_position_percent,
                }
                if resolved_dry_run:
                    order = {**order_payload, "status": "dry_run"}
                else:
                    response = broker.submit_order(
                        side="buy",
                        ticker=symbol,
                        quantity=quantity,
                        client_order_id=order_payload["client_order_id"],
                        order_type="market",
                        time_in_force="gtc",
                        allow_fractional=True,
                        reference_price=price,
                        wait_for_fill=True,
                    )
                    order = {**order_payload, **dict(response or {})}
                action_reason = str(order_payload.get("reason"))
                break
            if not order and blocked_buy_candidates and len(blocked_buy_candidates) == len(candidates):
                action_reason = "all_crypto_buy_candidates_already_open"

    positions_after = broker.get_positions() if order and not resolved_dry_run else positions_before
    crypto_positions_after = _crypto_positions(positions_after, symbol_set)
    confirmed = int(
        bool(order)
        and str(order.get("status") or "").lower() in {"accepted", "new", "partially_filled", "filled", "pending_new"}
        and not resolved_dry_run
    )
    cycle_status = "order_submitted" if confirmed else "dry_run" if order and resolved_dry_run else "no_trade"
    previous_cancellations = _load_recent_cancellations(resolved_status_path)
    cancellations_by_id: dict[str, dict[str, Any]] = {}
    for cancellation in [*stale_orders_canceled, *previous_cancellations]:
        identity = str(cancellation.get("order_id") or cancellation.get("client_order_id") or "").strip()
        if identity and identity not in cancellations_by_id:
            cancellations_by_id[identity] = dict(cancellation)
    recent_cancellations = list(cancellations_by_id.values())[:20]

    status = _safe_status(
        now=cycle_now,
        cycle_status=cycle_status,
        universe=universe,
        signals=signals,
        positions=crypto_positions_after,
        order=order,
    )
    status.update(
        {
            "action_reason": action_reason,
            "confirmed_order_count": confirmed,
            "dry_run": resolved_dry_run,
            "maximum_position_percent": maximum_position_percent,
            "stop_loss_percent": stop_loss_percent,
            "take_profit_percent": take_profit_percent,
            "pnl_policy": pnl_policy,
            "equity": round(equity, 6),
            "non_marginable_buying_power": round(non_marginable_buying_power, 6),
            "spendable_crypto_buying_power": round(non_marginable_buying_power * (buying_power_usage_percent / 100.0), 6),
            "buying_power_usage_percent": buying_power_usage_percent,
            "blocked_buy_candidates": blocked_buy_candidates,
            "stale_order_minutes": stale_order_minutes,
            "stale_orders_canceled": stale_orders_canceled,
            "recent_cancellations": recent_cancellations,
            "stale_order_cancel_failures": stale_order_cancel_failures,
            "signal_data_reused": not signals_refreshed,
            "signal_refresh_seconds": signal_refresh_seconds,
            "signal_refreshed_at": _utc_iso(cache.get("signals_at") if cache is not None else cycle_now),
            "universe_data_reused": not universe_refreshed,
            "cycle_duration_ms": round((perf_counter() - cycle_started) * 1000.0, 3),
        }
    )
    _write_json_atomic(resolved_status_path, status)
    for cancellation in stale_orders_canceled:
        _append_jsonl(
            resolved_trades_path,
            {
                "timestamp": str(cancellation.get("canceled_at") or _utc_iso(cycle_now)),
                "event": "crypto_order_canceled",
                **cancellation,
            },
        )
    if order:
        _append_jsonl(resolved_trades_path, {"timestamp": _utc_iso(cycle_now), **order})
    return status
