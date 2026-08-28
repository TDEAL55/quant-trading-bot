from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from alpaca_paper_broker import AlpacaPaperBroker
from pnl_risk_policy import (
    evaluate_account_pnl_policy,
    load_recent_closed_trades,
    risk_adjusted_position_percent,
    settings_from_environment,
)
from options_market_data import (
    AlpacaOptionsMarketData,
    analyze_underlying_bars,
    parse_option_symbol,
    select_option_contract,
)


EASTERN_TZ = ZoneInfo("America/New_York")


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _market_is_open(now: datetime) -> bool:
    eastern = now.astimezone(EASTERN_TZ)
    return eastern.weekday() < 5 and (9, 30) <= (eastern.hour, eastern.minute) < (16, 0)


def _underlyings() -> list[str]:
    raw = os.getenv("OPTIONS_UNDERLYINGS", "SPY,QQQ,IWM,AAPL,MSFT,NVDA,AMZN,META,TSLA,AMD")
    excluded = {str(item).strip().upper() for item in os.getenv("OPTIONS_EXCLUDE_UNDERLYINGS", "").split(",") if str(item).strip()}
    return [symbol for symbol in dict.fromkeys(str(item).strip().upper() for item in raw.split(",") if str(item).strip()) if symbol not in excluded]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _client_order_id(symbol: str, side: str, now: datetime, interval_minutes: int) -> str:
    bucket = int(now.astimezone(timezone.utc).timestamp()) // max(int(interval_minutes) * 60, 60)
    digest = hashlib.sha256(f"option|{symbol.upper()}|{side.upper()}|{bucket}".encode("utf-8")).hexdigest()[:24]
    return f"qtb-option-{digest}"


def _option_positions(positions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, raw in dict(positions or {}).items():
        payload = dict(raw or {})
        asset_class = str(payload.get("asset_class") or "").strip().lower()
        parsed = parse_option_symbol(symbol)
        if asset_class not in {"us_option", "option"} and not parsed:
            continue
        quantity = _float(payload.get("quantity"), 0.0)
        if quantity <= 0:
            continue
        rows.append(
            {
                **parsed,
                "symbol": str(symbol).upper(),
                "quantity": quantity,
                "average_entry_price": _float(payload.get("avg_price"), 0.0),
                "current_price": _float(payload.get("current_price"), 0.0),
                "market_value": _float(payload.get("market_value"), 0.0),
                "unrealized_pl": _float(payload.get("unrealized_pl"), 0.0),
                "unrealized_plpc": _float(payload.get("unrealized_plpc"), 0.0),
                "asset_class": asset_class or "us_option",
            }
        )
    return sorted(rows, key=lambda row: str(row.get("symbol") or ""))


def _status(
    *,
    now: datetime,
    cycle_status: str,
    underlyings: list[str],
    signals: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    order: dict[str, Any] | None = None,
    action_reason: str = "",
    error: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    return {
        "updated_at": _utc_iso(now),
        "enabled": True,
        "trading_mode": "PAPER",
        "market": "US regular hours",
        "strategy_scope": "long calls and long puts",
        "cycle_status": cycle_status,
        "underlying_count": len(underlyings),
        "scanned_count": len(signals),
        "call_signal_count": sum(1 for row in signals if str(row.get("signal") or "") == "CALL"),
        "put_signal_count": sum(1 for row in signals if str(row.get("signal") or "") == "PUT"),
        "top_signal": signals[0] if signals else {},
        "signals": signals,
        "positions": positions,
        "open_position_count": len(positions),
        "options_exposure": round(sum(abs(_float(row.get("market_value"), 0.0)) for row in positions), 4),
        "unrealized_pl": round(sum(_float(row.get("unrealized_pl"), 0.0) for row in positions), 4),
        "last_order": dict(order or {}),
        "action_reason": action_reason,
        "confirmed_order_count": 0,
        "dry_run": bool(dry_run),
        "error": str(error or ""),
    }


def run_options_paper_cycle(
    *,
    broker_factory: Callable[..., AlpacaPaperBroker] = AlpacaPaperBroker,
    market_data_factory: Callable[..., AlpacaOptionsMarketData] = AlpacaOptionsMarketData,
    now: datetime | None = None,
    status_path: str | Path | None = None,
    trades_path: str | Path | None = None,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    cycle_now = now or datetime.now(timezone.utc)
    if cycle_now.tzinfo is None:
        cycle_now = cycle_now.replace(tzinfo=timezone.utc)
    if str(os.getenv("TRADING_MODE", "PAPER")).strip().upper() != "PAPER":
        raise RuntimeError("options trader requires TRADING_MODE=PAPER")
    if not _enabled(os.getenv("OPTIONS_TRADING_ENABLED"), default=False):
        return {"cycle_status": "disabled", "confirmed_order_count": 0}

    resolved_status_path = Path(status_path or os.getenv("OPTIONS_STATUS_PATH", "/var/lib/quant-bot/options-status.json"))
    resolved_trades_path = Path(trades_path or os.getenv("OPTIONS_TRADES_LOG_PATH", "/var/lib/quant-bot/options-trades.jsonl"))
    resolved_dry_run = _enabled(os.getenv("OPTIONS_DRY_RUN"), default=True) if dry_run is None else bool(dry_run)
    underlyings = _underlyings()
    if not _market_is_open(cycle_now):
        result = _status(
            now=cycle_now,
            cycle_status="market_closed",
            underlyings=underlyings,
            signals=[],
            positions=[],
            action_reason="options_trade_window_closed",
            dry_run=resolved_dry_run,
        )
        _write_json_atomic(resolved_status_path, result)
        return result

    interval_minutes = max(int(os.getenv("OPTIONS_SCAN_INTERVAL_MINUTES", "15")), 1)
    call_score = _float(os.getenv("OPTIONS_CALL_SCORE", "60"), 60.0)
    put_score = _float(os.getenv("OPTIONS_PUT_SCORE", "40"), 40.0)
    min_dte = max(int(os.getenv("OPTIONS_MIN_DTE", "14")), 1)
    max_dte = max(int(os.getenv("OPTIONS_MAX_DTE", "45")), min_dte)
    exit_dte = max(int(os.getenv("OPTIONS_EXIT_DTE", "5")), 0)
    stop_loss_percent = max(_float(os.getenv("OPTIONS_STOP_LOSS_PERCENT", "25"), 25.0), 0.1)
    take_profit_percent = max(_float(os.getenv("OPTIONS_TAKE_PROFIT_PERCENT", "50"), 50.0), 0.1)
    target_delta = min(max(_float(os.getenv("OPTIONS_TARGET_DELTA", "0.55"), 0.55), 0.05), 0.95)
    maximum_spread = max(_float(os.getenv("OPTIONS_MAX_SPREAD_PERCENT", "35"), 35.0), 1.0)
    minimum_open_interest = max(int(os.getenv("OPTIONS_MIN_OPEN_INTEREST", "0")), 0)
    requested_position_percent = _float(os.getenv("OPTIONS_MAX_POSITION_EQUITY_PERCENT", "10"), 10.0)
    pnl_settings = settings_from_environment(
        maximum_position_percent=min(max(requested_position_percent, 0.1), 10.0)
    )
    maximum_position_percent = risk_adjusted_position_percent(
        stop_loss_percent=stop_loss_percent,
        settings=pnl_settings,
    )
    maximum_contracts = max(int(os.getenv("OPTIONS_MAX_CONTRACTS_PER_ORDER", "1000")), 1)

    broker = broker_factory(mode="PAPER")
    market_data = market_data_factory()
    bars_by_symbol = market_data.fetch_underlying_bars(
        underlyings,
        now=cycle_now,
        timeframe_minutes=int(os.getenv("OPTIONS_BAR_TIMEFRAME_MINUTES", "15")),
        lookback_bars=int(os.getenv("OPTIONS_LOOKBACK_BARS", "240")),
    )
    signals = [
        analyze_underlying_bars(
            symbol,
            bars_by_symbol.get(symbol),
            call_score=call_score,
            put_score=put_score,
            now=cycle_now,
            maximum_age_minutes=int(os.getenv("OPTIONS_MAX_DATA_AGE_MINUTES", "45")),
        )
        for symbol in underlyings
        if symbol in bars_by_symbol
    ]
    signals = sorted(signals, key=lambda row: (-abs(_float(row.get("score"), 50.0) - 50.0), str(row.get("symbol") or "")))
    signal_by_underlying = {str(row.get("symbol") or ""): row for row in signals}

    account = broker.get_account()
    pnl_policy = evaluate_account_pnl_policy(
        account,
        closed_trades=load_recent_closed_trades(os.getenv("DATABASE_URL")),
        settings=pnl_settings,
        now=cycle_now,
    )
    equity = _float(account.get("equity") or account.get("portfolio_value"), 0.0)
    options_buying_power = _float(account.get("options_buying_power"), 0.0)
    options_level = int(account.get("options_trading_level") or 0)
    all_positions = broker.get_positions()
    positions_before = _option_positions(all_positions)
    open_orders = list(broker.get_open_orders() or [])
    order: dict[str, Any] = {}
    action_reason = "no_options_order_selected"

    for position in positions_before:
        symbol = str(position.get("symbol") or "")
        underlying = str(position.get("underlying_symbol") or "")
        signal = signal_by_underlying.get(underlying) or {}
        pl_percent = _float(position.get("unrealized_plpc"), 0.0) * 100.0
        try:
            dte = (date.fromisoformat(str(position.get("expiration_date") or "")) - cycle_now.date()).days
        except ValueError:
            dte = 999
        contract_type = str(position.get("contract_type") or "")
        reversal = (contract_type == "call" and signal.get("signal") == "PUT") or (contract_type == "put" and signal.get("signal") == "CALL")
        exit_reason = ""
        if pl_percent <= -stop_loss_percent:
            exit_reason = "options_stop_loss"
        elif pl_percent >= take_profit_percent:
            exit_reason = "options_take_profit"
        elif dte <= exit_dte:
            exit_reason = "options_expiration_exit"
        elif reversal:
            exit_reason = "options_underlying_reversal"
        if not exit_reason:
            continue
        if any(
            str(row.get("symbol") or "").upper() == symbol
            and str(row.get("side") or "").lower() == "sell"
            and str(row.get("status") or "").lower() not in {"filled", "canceled", "cancelled", "rejected", "expired"}
            for row in open_orders
        ):
            action_reason = "options_close_order_already_open"
            break
        snapshots = market_data.fetch_option_snapshots([symbol])
        snapshot = snapshots.get(symbol) or {}
        limit_price = _float(snapshot.get("bid"), 0.0)
        order_payload = {
            "symbol": symbol,
            "underlying_symbol": underlying,
            "side": "SELL",
            "position_intent": "sell_to_close",
            "quantity": int(math.floor(_float(position.get("quantity"), 0.0))),
            "limit_price": round(limit_price, 2) if limit_price > 0 else 0.0,
            "reason": exit_reason,
            "client_order_id": _client_order_id(symbol, "SELL", cycle_now, interval_minutes),
        }
        if resolved_dry_run:
            order = {**order_payload, "status": "dry_run"}
        else:
            response = broker.submit_option_order(
                side="sell",
                ticker=symbol,
                quantity=order_payload["quantity"],
                limit_price=(order_payload["limit_price"] or None),
                client_order_id=order_payload["client_order_id"],
                wait_for_fill=False,
            )
            order = {**order_payload, **dict(response or {})}
        action_reason = exit_reason
        break

    if not order and bool(pnl_policy.get("block_new_entries")):
        action_reason = str(pnl_policy.get("reason") or "pnl_rule_blocked")
    elif not order:
        held_underlyings = {str(row.get("underlying_symbol") or "") for row in positions_before}
        directional = [
            row
            for row in signals
            if bool(row.get("eligible"))
            and str(row.get("signal") or "") in {"CALL", "PUT"}
            and str(row.get("symbol") or "") not in held_underlyings
        ]
        if directional and options_level < 2:
            action_reason = "options_level_2_required"
        elif directional and options_buying_power <= 0:
            action_reason = "insufficient_options_buying_power"
        elif directional:
            selected = directional[0]
            underlying = str(selected.get("symbol") or "")
            contract_type = str(selected.get("signal") or "").lower()
            contracts = broker.get_option_contracts(
                underlying,
                expiration_date_gte=(cycle_now.date() + timedelta(days=min_dte)),
                expiration_date_lte=(cycle_now.date() + timedelta(days=max_dte)),
                contract_type=contract_type,
                limit=1000,
            )
            underlying_price = _float(selected.get("latest_price"), 0.0)
            nearby = sorted(
                [
                    row
                    for row in contracts
                    if underlying_price > 0
                    and 0.8 <= (_float(row.get("strike_price"), 0.0) / underlying_price) <= 1.2
                ],
                key=lambda row: (
                    abs((_float(row.get("strike_price"), 0.0) / underlying_price) - 1.0),
                    str(row.get("expiration_date") or ""),
                ),
            )[:100]
            snapshots = market_data.fetch_option_snapshots([str(row.get("symbol") or "") for row in nearby])
            contract = select_option_contract(
                nearby,
                snapshots,
                underlying_price=underlying_price,
                target_delta=target_delta,
                maximum_spread_percent=maximum_spread,
                minimum_open_interest=minimum_open_interest,
                today=cycle_now.date(),
            )
            if not contract:
                action_reason = "no_liquid_option_contract"
            else:
                symbol = str(contract.get("symbol") or "")
                duplicate = any(
                    str(row.get("symbol") or "").upper() == symbol
                    and str(row.get("side") or "").lower() == "buy"
                    and str(row.get("status") or "").lower() not in {"filled", "canceled", "cancelled", "rejected", "expired"}
                    for row in open_orders
                )
                ask = _float(contract.get("ask"), 0.0)
                contract_cost = ask * int(contract.get("contract_multiplier") or 100)
                position_cap = min(equity * (maximum_position_percent / 100.0), options_buying_power)
                quantity = min(int(position_cap // contract_cost) if contract_cost > 0 else 0, maximum_contracts)
                if duplicate:
                    action_reason = "options_open_order_already_exists"
                elif quantity < 1:
                    action_reason = "option_premium_exceeds_position_cap"
                else:
                    order_payload = {
                        "symbol": symbol,
                        "underlying_symbol": underlying,
                        "contract_type": contract_type,
                        "side": "BUY",
                        "position_intent": "buy_to_open",
                        "quantity": quantity,
                        "limit_price": round(ask, 2),
                        "estimated_premium": round(quantity * contract_cost, 2),
                        "maximum_position_percent": maximum_position_percent,
                        "reason": str(selected.get("reason") or "options_directional_entry"),
                        "client_order_id": _client_order_id(symbol, "BUY", cycle_now, interval_minutes),
                        "contract": contract,
                    }
                    if resolved_dry_run:
                        order = {**order_payload, "status": "dry_run"}
                    else:
                        response = broker.submit_option_order(
                            side="buy",
                            ticker=symbol,
                            quantity=quantity,
                            limit_price=order_payload["limit_price"],
                            client_order_id=order_payload["client_order_id"],
                            wait_for_fill=False,
                        )
                        order = {**order_payload, **dict(response or {})}
                    action_reason = str(order_payload.get("reason") or "options_directional_entry")

    positions_after = _option_positions(broker.get_positions()) if order and not resolved_dry_run else positions_before
    confirmed = int(
        bool(order)
        and not resolved_dry_run
        and str(order.get("status") or "").lower() in {"accepted", "new", "partially_filled", "filled", "pending_new"}
    )
    cycle_status = "order_submitted" if confirmed else "dry_run" if order and resolved_dry_run else "no_trade"
    result = _status(
        now=cycle_now,
        cycle_status=cycle_status,
        underlyings=underlyings,
        signals=signals,
        positions=positions_after,
        order=order,
        action_reason=action_reason,
        dry_run=resolved_dry_run,
    )
    result.update(
        {
            "confirmed_order_count": confirmed,
            "maximum_position_percent": maximum_position_percent,
            "stop_loss_percent": stop_loss_percent,
            "take_profit_percent": take_profit_percent,
            "pnl_policy": pnl_policy,
            "options_buying_power": round(options_buying_power, 2),
            "options_trading_level": options_level,
            "equity": round(equity, 2),
        }
    )
    _write_json_atomic(resolved_status_path, result)
    if order:
        _append_jsonl(resolved_trades_path, {"timestamp": _utc_iso(cycle_now), **order})
    return result
