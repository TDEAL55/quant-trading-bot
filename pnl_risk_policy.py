from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Callable, Iterable


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PnLRiskSettings:
    daily_loss_stop_percent: float = 2.0
    maximum_position_percent: float = 10.0
    maximum_risk_per_trade_percent: float = 1.0
    maximum_consecutive_losses: int = 3
    loss_cooldown_minutes: int = 60

    def validate(self) -> None:
        daily_stop = float(self.daily_loss_stop_percent)
        trading_mode = str(os.getenv("TRADING_MODE", "SIMULATION")).strip().upper()
        if not 0 < daily_stop:
            raise ValueError("daily_loss_stop_percent must be > 0")
        if trading_mode == "LIVE" and daily_stop > 25:
            raise ValueError("daily_loss_stop_percent must be <= 25 in LIVE mode")
        if not 0 < float(self.maximum_position_percent) <= 10:
            raise ValueError("maximum_position_percent must be > 0 and <= 10")
        if not 0 < float(self.maximum_risk_per_trade_percent) <= 5:
            raise ValueError("maximum_risk_per_trade_percent must be > 0 and <= 5")
        if int(self.maximum_consecutive_losses) < 1:
            raise ValueError("maximum_consecutive_losses must be at least 1")
        if int(self.loss_cooldown_minutes) < 1:
            raise ValueError("loss_cooldown_minutes must be at least 1")


def settings_from_environment(*, maximum_position_percent: float = 10.0) -> PnLRiskSettings:
    settings = PnLRiskSettings(
        daily_loss_stop_percent=_as_float(os.getenv("PAPER_DAILY_LOSS_STOP_PERCENT", "2"), 2.0),
        maximum_position_percent=min(
            max(_as_float(maximum_position_percent, 10.0), 0.1),
            10.0,
        ),
        maximum_risk_per_trade_percent=_as_float(
            os.getenv("PAPER_MAX_RISK_PER_TRADE_PERCENT", "1"),
            1.0,
        ),
        maximum_consecutive_losses=int(
            _as_float(os.getenv("PAPER_MAX_CONSECUTIVE_LOSSES", "3"), 3.0)
        ),
        loss_cooldown_minutes=int(
            _as_float(os.getenv("PAPER_LOSS_COOLDOWN_MINUTES", "60"), 60.0)
        ),
    )
    settings.validate()
    return settings


def load_recent_closed_trades(
    database_url: str | None = None,
    *,
    repository_factory: Callable[..., Any] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Best-effort load for the cross-asset loss-streak rule."""
    factory = repository_factory
    if factory is None:
        from paper_execution_repository import MonitoringPaperExecutionRepository

        factory = MonitoringPaperExecutionRepository
    repository = None
    try:
        repository = factory(database_url=database_url)
        loader = getattr(repository, "list_closed_trades", None)
        return [dict(row or {}) for row in list(loader(limit=int(limit)) or [])] if callable(loader) else []
    except Exception:
        return []
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


def risk_adjusted_position_percent(
    *,
    stop_loss_percent: float,
    settings: PnLRiskSettings,
) -> float:
    """Cap position size so a full stop costs no more than the per-trade risk budget."""
    settings.validate()
    stop = abs(_as_float(stop_loss_percent, 0.0))
    if stop <= 0:
        return round(float(settings.maximum_position_percent), 6)
    risk_limited = (float(settings.maximum_risk_per_trade_percent) / stop) * 100.0
    return round(min(float(settings.maximum_position_percent), risk_limited), 6)


def volatility_adjusted_position_percent(
    *,
    base_position_percent: float,
    strategy_signal: dict[str, Any] | None,
    target_annualized_volatility_percent: float = 20.0,
    minimum_scale: float = 0.25,
) -> float:
    """Reduce, but never increase, position size as observed volatility rises."""
    signal = dict(strategy_signal or {})
    factors = dict((signal.get("supporting_factors") or {}).get("factor_values") or {})
    volatility = dict(factors.get("volatility_quality") or {})
    realized = _as_float(volatility.get("realized_volatility_pct"), 0.0)
    atr_pct = _as_float(volatility.get("atr_pct"), 0.0)
    scale = 1.0
    if realized > 0:
        scale = min(scale, float(target_annualized_volatility_percent) / realized)
    if atr_pct > 0:
        # Four percent daily ATR is already a high-volatility stock for this
        # medium-horizon strategy; taper exposure before that point.
        scale = min(scale, 2.5 / atr_pct)
    scale = max(min(float(scale), 1.0), float(minimum_scale))
    return round(max(float(base_position_percent), 0.0) * scale, 6)


def confidence_adjusted_position_percent(
    *,
    stop_loss_percent: float,
    settings: PnLRiskSettings,
    strategy_signal: dict[str, Any] | None,
    strategy_leaderboard: Iterable[dict[str, Any]] | None = None,
    trading_mode: str | None = None,
) -> float:
    """Allow bounded PAPER conviction sizing without bypassing risk-per-trade.

    Ten percent is the absolute cap in every mode. Confidence can reduce risk
    through the risk budget but cannot override portfolio concentration.
    """
    settings.validate()
    base_cap = float(settings.maximum_position_percent)
    mode = str(trading_mode or os.getenv("TRADING_MODE", "SIMULATION")).strip().upper()
    if mode != "PAPER" or str(os.getenv("PAPER_CONFIDENCE_SIZING_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        return risk_adjusted_position_percent(stop_loss_percent=stop_loss_percent, settings=settings)

    requested_cap = min(base_cap, 10.0)

    stop = abs(_as_float(stop_loss_percent, 0.0))
    risk_limited = (
        (float(settings.maximum_risk_per_trade_percent) / stop) * 100.0
        if stop > 0
        else requested_cap
    )
    return round(min(requested_cap, risk_limited, 10.0), 6)


def evaluate_account_pnl_policy(
    account: dict[str, Any] | None,
    *,
    closed_trades: Iterable[dict[str, Any]] | None = None,
    settings: PnLRiskSettings | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a read-only P&L gate. It blocks entries, never exits."""
    policy = settings or PnLRiskSettings()
    policy.validate()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    snapshot = dict(account or {})
    equity = _as_float(snapshot.get("equity") or snapshot.get("portfolio_value"), 0.0)
    last_equity = _as_float(snapshot.get("last_equity"), 0.0)
    reported_day_pl = snapshot.get("day_pl")
    day_pl = _as_float(reported_day_pl, equity - last_equity) if reported_day_pl is not None else equity - last_equity
    reference_equity = last_equity if last_equity > 0 else max(equity - day_pl, equity, 0.0)
    day_return_percent = (day_pl / reference_equity) * 100.0 if reference_equity > 0 else 0.0
    daily_loss_stop_active = bool(
        reference_equity > 0
        and day_return_percent <= -abs(float(policy.daily_loss_stop_percent))
    )

    normalized_trades: list[tuple[datetime, float]] = []
    for row in list(closed_trades or []):
        timestamp = _parse_timestamp((row or {}).get("exit_timestamp"))
        if timestamp is None:
            continue
        net_pnl = _as_float(
            (row or {}).get("net_pnl"),
            _as_float((row or {}).get("realized_gross_pnl"), 0.0),
        )
        normalized_trades.append((timestamp, net_pnl))
    normalized_trades.sort(key=lambda item: item[0], reverse=True)

    consecutive_losses = 0
    latest_loss_at: datetime | None = None
    for timestamp, net_pnl in normalized_trades:
        if net_pnl >= 0:
            break
        consecutive_losses += 1
        if latest_loss_at is None:
            latest_loss_at = timestamp

    cooldown_until = (
        latest_loss_at + timedelta(minutes=int(policy.loss_cooldown_minutes))
        if latest_loss_at is not None and consecutive_losses >= int(policy.maximum_consecutive_losses)
        else None
    )
    loss_streak_cooldown_active = bool(cooldown_until is not None and current < cooldown_until)
    block_new_entries = bool(daily_loss_stop_active or loss_streak_cooldown_active)
    reason = (
        "daily_loss_stop"
        if daily_loss_stop_active
        else "consecutive_loss_cooldown"
        if loss_streak_cooldown_active
        else "pnl_rules_clear"
    )

    return {
        "enabled": True,
        "status": "BLOCKED" if block_new_entries else "ARMED",
        "reason": reason,
        "block_new_entries": block_new_entries,
        "exits_allowed": True,
        "equity": round(equity, 6),
        "reference_equity": round(reference_equity, 6),
        "day_pl": round(day_pl, 6),
        "day_return_percent": round(day_return_percent, 6),
        "daily_loss_stop_percent": float(policy.daily_loss_stop_percent),
        "daily_loss_stop_active": daily_loss_stop_active,
        "consecutive_losses": consecutive_losses,
        "maximum_consecutive_losses": int(policy.maximum_consecutive_losses),
        "loss_cooldown_minutes": int(policy.loss_cooldown_minutes),
        "loss_streak_cooldown_active": loss_streak_cooldown_active,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until is not None else "",
        "maximum_position_percent": float(policy.maximum_position_percent),
        "maximum_risk_per_trade_percent": float(policy.maximum_risk_per_trade_percent),
        "evaluated_at": current.isoformat(),
    }
