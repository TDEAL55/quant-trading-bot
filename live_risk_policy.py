from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Mapping
from zoneinfo import ZoneInfo


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
    entry_cash_allocation_percent: float = 25.0
    maximum_account_equity: float = 500.0
    maximum_position_percent: float = 10.0
    maximum_position_notional: float = 30.0
    maximum_gross_exposure_percent: float = 30.0
    maximum_open_positions: int = 3
    maximum_new_orders_per_day: int = 1
    daily_loss_stop_percent: float = 1.0
    daily_loss_stop_dollars: float = 3.0
    weekly_drawdown_stop_percent: float = 3.0
    maximum_risk_per_trade_percent: float = 0.5
    maximum_consecutive_losses: int = 3
    consecutive_loss_cooldown_minutes: int = 60
    maximum_correlated_exposure_percent: float = 20.0
    maximum_pair_correlation: float = 0.80
    correlation_lookback_days: int = 90
    correlation_min_overlap_days: int = 40
    minimum_cash_reserve_percent: float = 70.0
    minimum_strategy_score: float = 75.0
    minimum_confidence: float = 70.0
    stop_loss_percent: float = 5.0
    take_profit_percent: float = 10.0
    allowed_symbols: tuple[str, ...] = ()

    def validate(self) -> None:
        if not 0 < self.entry_cash_allocation_percent <= 100:
            raise ValueError("LIVE_ENTRY_CASH_ALLOCATION_PERCENT must be in (0, 100]")
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
        if not 0 < self.weekly_drawdown_stop_percent <= 5:
            raise ValueError("LIVE_WEEKLY_DRAWDOWN_STOP_PERCENT must be in (0, 5]")
        if not 0 < self.maximum_risk_per_trade_percent <= 1:
            raise ValueError("LIVE_MAX_RISK_PER_TRADE_PERCENT must be in (0, 1]")
        if not 1 <= self.maximum_consecutive_losses <= 5:
            raise ValueError("LIVE_MAX_CONSECUTIVE_LOSSES must be between 1 and 5")
        if not 15 <= self.consecutive_loss_cooldown_minutes <= 1440:
            raise ValueError("LIVE_CONSECUTIVE_LOSS_COOLDOWN_MINUTES must be between 15 and 1440")
        if not 0 < self.maximum_correlated_exposure_percent <= 30:
            raise ValueError("LIVE_MAX_CORRELATED_EXPOSURE_PERCENT must be in (0, 30]")
        if not 0.5 <= self.maximum_pair_correlation <= 0.95:
            raise ValueError("LIVE_MAX_PAIR_CORRELATION must be in [0.5, 0.95]")
        if not 20 <= self.correlation_lookback_days <= 252:
            raise ValueError("LIVE_CORRELATION_LOOKBACK_DAYS must be between 20 and 252")
        if not 10 <= self.correlation_min_overlap_days <= self.correlation_lookback_days:
            raise ValueError("LIVE_CORRELATION_MIN_OVERLAP_DAYS must be between 10 and the lookback")
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
        entry_limits_enabled=_is_true(env.get("LIVE_ENTRY_LIMITS_ENABLED", "false")),
        entry_cash_allocation_percent=_as_float(env.get("LIVE_ENTRY_CASH_ALLOCATION_PERCENT"), 25.0),
        maximum_account_equity=_as_float(env.get("LIVE_MAX_ACCOUNT_EQUITY"), 500.0),
        maximum_position_percent=_as_float(env.get("LIVE_MAX_POSITION_PERCENT"), 10.0),
        maximum_position_notional=_as_float(env.get("LIVE_MAX_POSITION_NOTIONAL"), 30.0),
        maximum_gross_exposure_percent=_as_float(env.get("LIVE_MAX_GROSS_EXPOSURE_PERCENT"), 30.0),
        maximum_open_positions=_as_int(env.get("LIVE_MAX_OPEN_POSITIONS"), 3),
        maximum_new_orders_per_day=_as_int(env.get("LIVE_MAX_NEW_ORDERS_PER_DAY"), 1),
        daily_loss_stop_percent=_as_float(env.get("LIVE_DAILY_LOSS_STOP_PERCENT"), 1.0),
        daily_loss_stop_dollars=_as_float(env.get("LIVE_DAILY_LOSS_STOP_DOLLARS"), 3.0),
        weekly_drawdown_stop_percent=_as_float(env.get("LIVE_WEEKLY_DRAWDOWN_STOP_PERCENT"), 3.0),
        maximum_risk_per_trade_percent=_as_float(env.get("LIVE_MAX_RISK_PER_TRADE_PERCENT"), 0.5),
        maximum_consecutive_losses=_as_int(env.get("LIVE_MAX_CONSECUTIVE_LOSSES"), 3),
        consecutive_loss_cooldown_minutes=_as_int(env.get("LIVE_CONSECUTIVE_LOSS_COOLDOWN_MINUTES"), 60),
        maximum_correlated_exposure_percent=_as_float(env.get("LIVE_MAX_CORRELATED_EXPOSURE_PERCENT"), 20.0),
        maximum_pair_correlation=_as_float(env.get("LIVE_MAX_PAIR_CORRELATION"), 0.80),
        correlation_lookback_days=_as_int(env.get("LIVE_CORRELATION_LOOKBACK_DAYS"), 90),
        correlation_min_overlap_days=_as_int(env.get("LIVE_CORRELATION_MIN_OVERLAP_DAYS"), 40),
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
        # Disabling the legacy count/exposure caps must not turn one entry into
        # an all-cash order. A per-entry allocation leaves buying power for
        # subsequent qualifying stocks while keeping the daily count unlimited.
        allocation_cap = max(equity, 0.0) * settings.entry_cash_allocation_percent / 100.0
        return round(max(min(cash, allocation_cap), 0.0), 2)
    gross_exposure = sum(abs(_as_float(row.get("market_value"), 0.0)) for row in dict(positions or {}).values())
    position_cap = min(
        equity * settings.maximum_position_percent / 100.0,
        settings.maximum_position_notional,
    )
    gross_room = max(equity * settings.maximum_gross_exposure_percent / 100.0 - gross_exposure, 0.0)
    required_cash = equity * settings.minimum_cash_reserve_percent / 100.0
    cash_room = max(cash - required_cash, 0.0)
    return round(max(min(position_cap, gross_room, cash_room), 0.0), 2)


def _event_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_live_performance_controls(
    account: Mapping[str, Any],
    closed_trades: list[Mapping[str, Any]],
    *,
    settings: LiveRiskSettings,
    now: datetime,
    equity_history: list[Mapping[str, Any]] | None = None,
    history_available: bool = True,
    history_complete: bool = True,
) -> dict[str, Any]:
    """Evaluate loss-history controls without inferring missing broker history."""
    reasons: list[str] = []
    if not history_available:
        reasons.append("live_order_history_unavailable")
    if not history_complete:
        reasons.append("live_order_history_incomplete")

    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    market_now = now_utc.astimezone(ZoneInfo("America/New_York"))
    week_start_local = (market_now - timedelta(days=market_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start = week_start_local.astimezone(timezone.utc)
    ordered: list[tuple[datetime, float]] = []
    for raw in closed_trades or []:
        row = dict(raw or {})
        at = _event_timestamp(row.get("exit_timestamp") or row.get("closed_at"))
        if at is None:
            continue
        ordered.append((at, _as_float(row.get("realized_pnl"), 0.0)))
    ordered.sort(key=lambda item: item[0])

    weekly_pnl = sum(pnl for at, pnl in ordered if week_start <= at <= now_utc)
    equity = _as_float(account.get("equity"), 0.0)
    approximate_week_start_equity = max(equity - weekly_pnl, equity, 0.0)
    weekly_loss_limit = approximate_week_start_equity * settings.weekly_drawdown_stop_percent / 100.0
    weekly_equities: list[float] = []
    for raw in list(equity_history or []):
        row = dict(raw or {})
        observed_at = _event_timestamp(row.get("timestamp"))
        observed_equity = _as_float(row.get("equity"), 0.0)
        if observed_at is not None and week_start <= observed_at <= now_utc and observed_equity > 0:
            weekly_equities.append(observed_equity)
    weekly_peak_equity = max([equity, *weekly_equities], default=max(equity, 0.0))
    weekly_drawdown_dollars = max(weekly_peak_equity - equity, 0.0)
    equity_drawdown_limit = weekly_peak_equity * settings.weekly_drawdown_stop_percent / 100.0
    if (
        (weekly_loss_limit > 0 and weekly_pnl <= -weekly_loss_limit)
        or (equity_drawdown_limit > 0 and weekly_drawdown_dollars >= equity_drawdown_limit)
    ):
        reasons.append("weekly_drawdown_stop_active")

    consecutive_losses = 0
    latest_loss_at: datetime | None = None
    for at, pnl in reversed(ordered):
        if pnl < 0:
            consecutive_losses += 1
            latest_loss_at = latest_loss_at or at
        else:
            break
    cooldown_until = None
    if consecutive_losses >= settings.maximum_consecutive_losses and latest_loss_at is not None:
        cooldown_until = latest_loss_at + timedelta(minutes=settings.consecutive_loss_cooldown_minutes)
        if now_utc < cooldown_until:
            reasons.append("consecutive_loss_cooldown_active")

    return {
        "approved": not reasons,
        "reasons": reasons,
        "weekly_realized_pnl": round(weekly_pnl, 6),
        "weekly_loss_limit": round(weekly_loss_limit, 6),
        "weekly_peak_equity": round(weekly_peak_equity, 6),
        "weekly_drawdown_dollars": round(weekly_drawdown_dollars, 6),
        "week_start": week_start.isoformat(),
        "consecutive_losses": consecutive_losses,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "closed_trade_count": len(ordered),
    }


def stop_risk_position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    maximum_notional: float,
    settings: LiveRiskSettings,
) -> dict[str, Any]:
    """Size a whole-share entry from allowed dollar risk and stop distance."""
    allowed_risk = max(float(equity), 0.0) * settings.maximum_risk_per_trade_percent / 100.0
    risk_per_share = max(float(entry_price) - float(stop_price), 0.0)
    if allowed_risk <= 0 or risk_per_share <= 0 or entry_price <= 0 or maximum_notional <= 0:
        return {
            "quantity": 0,
            "allowed_risk_dollars": round(allowed_risk, 6),
            "risk_per_share": round(risk_per_share, 6),
            "planned_risk_dollars": 0.0,
        }
    risk_quantity = int(allowed_risk // risk_per_share)
    notional_quantity = int(float(maximum_notional) // float(entry_price))
    quantity = max(min(risk_quantity, notional_quantity), 0)
    return {
        "quantity": quantity,
        "allowed_risk_dollars": round(allowed_risk, 6),
        "risk_per_share": round(risk_per_share, 6),
        "planned_risk_dollars": round(quantity * risk_per_share, 6),
    }
