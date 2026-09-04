from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping


LIVE_CONFIRMATION_PHRASE = "ENABLE_LIVE_MICRO_TRADING"
LIVE_ENDPOINT = "https://api.alpaca.markets"


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LiveRiskSettings:
    enabled: bool = False
    order_submission_enabled: bool = False
    kill_switch: bool = True
    confirmation: str = ""
    private_dashboard_confirmed: bool = False
    entry_limits_enabled: bool = True
    maximum_account_equity: float = 500.0
    maximum_position_percent: float = 10.0
    maximum_position_notional: float = 30.0
    maximum_gross_exposure_percent: float = 30.0
    maximum_open_positions: int = 3
    maximum_new_orders_per_day: int = 1
    daily_loss_stop_percent: float = 1.0
    daily_loss_stop_dollars: float = 3.0
    minimum_cash_reserve_percent: float = 70.0
    minimum_strategy_score: float = 75.0
    minimum_confidence: float = 70.0
    stop_loss_percent: float = 5.0
    take_profit_percent: float = 10.0
    allowed_symbols: tuple[str, ...] = ()

    def validate(self) -> None:
        if not 0 < self.maximum_position_percent <= 10:
            raise ValueError("LIVE_MAX_POSITION_PERCENT must be in (0, 10]")
        if self.maximum_position_notional <= 0:
            raise ValueError("LIVE_MAX_POSITION_NOTIONAL must be positive")
        if not 0 < self.maximum_gross_exposure_percent <= 30:
            raise ValueError("LIVE_MAX_GROSS_EXPOSURE_PERCENT must be in (0, 30]")
        if not 1 <= self.maximum_open_positions <= 3:
            raise ValueError("LIVE_MAX_OPEN_POSITIONS must be between 1 and 3")
        if self.maximum_new_orders_per_day != 1:
            raise ValueError("LIVE_MAX_NEW_ORDERS_PER_DAY must remain 1 during micro launch")
        if not 0 < self.daily_loss_stop_percent <= 1:
            raise ValueError("LIVE_DAILY_LOSS_STOP_PERCENT must be in (0, 1]")
        if not 0 < self.daily_loss_stop_dollars <= 3:
            raise ValueError("LIVE_DAILY_LOSS_STOP_DOLLARS must be in (0, 3]")
        if not 50 <= self.minimum_cash_reserve_percent < 100:
            raise ValueError("LIVE_MINIMUM_CASH_RESERVE_PERCENT must be in [50, 100)")
        if not 0 < self.stop_loss_percent <= 5:
            raise ValueError("LIVE_STOP_LOSS_PERCENT must be in (0, 5]")
        if not 5 <= self.take_profit_percent <= 15:
            raise ValueError("LIVE_TAKE_PROFIT_PERCENT must be in [5, 15]")


def settings_from_environment(environ: Mapping[str, str] | None = None) -> LiveRiskSettings:
    env = dict(os.environ if environ is None else environ)
    symbols = tuple(
        dict.fromkeys(
            str(item or "").strip().upper()
            for item in str(env.get("LIVE_ALLOWED_SYMBOLS", "")).split(",")
            if str(item or "").strip()
        )
    )
    settings = LiveRiskSettings(
        enabled=_is_true(env.get("LIVE_TRADING_ENABLED", "false")),
        order_submission_enabled=_is_true(env.get("ALPACA_LIVE_ORDER_SUBMISSION_ENABLED", "false")),
        kill_switch=_is_true(env.get("LIVE_KILL_SWITCH", "true")),
        confirmation=str(env.get("LIVE_TRADING_CONFIRMATION", "")).strip(),
        private_dashboard_confirmed=_is_true(env.get("LIVE_PRIVATE_DASHBOARD_CONFIRMED", "false")),
        maximum_account_equity=_as_float(env.get("LIVE_MAX_ACCOUNT_EQUITY"), 500.0),
        maximum_position_percent=_as_float(env.get("LIVE_MAX_POSITION_PERCENT"), 10.0),
        maximum_position_notional=_as_float(env.get("LIVE_MAX_POSITION_NOTIONAL"), 30.0),
        maximum_gross_exposure_percent=_as_float(env.get("LIVE_MAX_GROSS_EXPOSURE_PERCENT"), 30.0),
        maximum_open_positions=_as_int(env.get("LIVE_MAX_OPEN_POSITIONS"), 3),
        maximum_new_orders_per_day=_as_int(env.get("LIVE_MAX_NEW_ORDERS_PER_DAY"), 1),
        daily_loss_stop_percent=_as_float(env.get("LIVE_DAILY_LOSS_STOP_PERCENT"), 1.0),
        daily_loss_stop_dollars=_as_float(env.get("LIVE_DAILY_LOSS_STOP_DOLLARS"), 3.0),
        minimum_cash_reserve_percent=_as_float(env.get("LIVE_MINIMUM_CASH_RESERVE_PERCENT"), 70.0),
        minimum_strategy_score=_as_float(env.get("LIVE_MINIMUM_STRATEGY_SCORE"), 75.0),
        minimum_confidence=_as_float(env.get("LIVE_MINIMUM_CONFIDENCE"), 70.0),
        stop_loss_percent=_as_float(env.get("LIVE_STOP_LOSS_PERCENT"), 5.0),
        take_profit_percent=_as_float(env.get("LIVE_TAKE_PROFIT_PERCENT"), 10.0),
        allowed_symbols=symbols,
    )
    settings.validate()
    return settings


def evaluate_live_readiness(
    account: Mapping[str, Any],
    positions: Mapping[str, Mapping[str, Any]],
    open_orders: list[Mapping[str, Any]],
    *,
    settings: LiveRiskSettings,
    market_is_open: bool,
    orders_submitted_today: int,
) -> dict[str, Any]:
    equity = _as_float(account.get("equity"), 0.0)
    last_equity = _as_float(account.get("last_equity"), equity)
    cash = _as_float(account.get("cash"), 0.0)
    day_pl = _as_float(account.get("day_pl"), equity - last_equity)
    multiplier = _as_float(account.get("multiplier"), 1.0)
    stock_positions = {
        str(symbol).upper(): dict(payload or {})
        for symbol, payload in dict(positions or {}).items()
        if "/" not in str(symbol) and "option" not in str((payload or {}).get("asset_class") or "").lower()
    }
    gross_exposure = sum(abs(_as_float(row.get("market_value"), 0.0)) for row in stock_positions.values())
    gross_percent = gross_exposure / equity * 100.0 if equity > 0 else 0.0
    daily_loss_limit = min(
        equity * settings.daily_loss_stop_percent / 100.0,
        settings.daily_loss_stop_dollars,
    )

    reasons: list[str] = []
    if not settings.enabled:
        reasons.append("live_trading_disabled")
    if not settings.order_submission_enabled:
        reasons.append("live_order_submission_disabled")
    if settings.kill_switch:
        reasons.append("live_kill_switch_active")
    if settings.confirmation != LIVE_CONFIRMATION_PHRASE:
        reasons.append("live_confirmation_missing")
    if not settings.private_dashboard_confirmed:
        reasons.append("private_dashboard_not_confirmed")
    if str(account.get("status") or "").strip().upper() != "ACTIVE":
        reasons.append("account_not_active")
    if bool(account.get("trading_blocked")) or bool(account.get("account_blocked")):
        reasons.append("account_trading_blocked")
    if equity <= 0:
        reasons.append("account_equity_unavailable")
    if equity > settings.maximum_account_equity:
        reasons.append("account_equity_above_micro_launch_limit")
    if cash < 0 or multiplier > 2:
        reasons.append("margin_or_negative_cash_not_allowed")
    if day_pl <= -daily_loss_limit:
        reasons.append("daily_loss_stop_active")
    if settings.entry_limits_enabled:
        if len(stock_positions) >= settings.maximum_open_positions:
            reasons.append("maximum_open_positions_reached")
        if gross_percent >= settings.maximum_gross_exposure_percent:
            reasons.append("maximum_gross_exposure_reached")
        if int(orders_submitted_today) >= settings.maximum_new_orders_per_day:
            reasons.append("daily_new_order_limit_reached")
    if open_orders:
        reasons.append("open_orders_require_reconciliation")
    if not market_is_open:
        reasons.append("regular_market_closed")
    if not settings.allowed_symbols:
        reasons.append("live_symbol_allowlist_empty")

    return {
        "approved": not reasons,
        "reasons": reasons,
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "day_pl": round(day_pl, 2),
        "daily_loss_limit": round(daily_loss_limit, 2),
        "gross_exposure": round(gross_exposure, 2),
        "gross_exposure_percent": round(gross_percent, 4),
        "open_stock_positions": len(stock_positions),
        "orders_submitted_today": int(orders_submitted_today),
    }


def live_entry_notional(account: Mapping[str, Any], positions: Mapping[str, Mapping[str, Any]], settings: LiveRiskSettings) -> float:
    equity = _as_float(account.get("equity"), 0.0)
    cash = _as_float(account.get("cash"), 0.0)
    if not settings.entry_limits_enabled:
        return round(max(cash, 0.0), 2)
    gross_exposure = sum(abs(_as_float(row.get("market_value"), 0.0)) for row in dict(positions or {}).values())
    position_cap = min(
        equity * settings.maximum_position_percent / 100.0,
        settings.maximum_position_notional,
    )
    gross_room = max(equity * settings.maximum_gross_exposure_percent / 100.0 - gross_exposure, 0.0)
    required_cash = equity * settings.minimum_cash_reserve_percent / 100.0
    cash_room = max(cash - required_cash, 0.0)
    return round(max(min(position_cap, gross_room, cash_room), 0.0), 2)
