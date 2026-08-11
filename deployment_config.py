from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


class DeploymentConfigError(ValueError):
    pass


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(name: str, value: str | None, minimum: int | None = None, maximum: int | None = None) -> int:
    if value is None or str(value).strip() == "":
        raise DeploymentConfigError(f"{name} is required")
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise DeploymentConfigError(f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise DeploymentConfigError(f"{name} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise DeploymentConfigError(f"{name} must be at most {maximum}")
    return parsed


def _validate_sqlite_url(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        raise DeploymentConfigError("DATABASE_URL must use sqlite:/// for initial paper deployment")
    raw_path = database_url.replace("sqlite:///", "", 1).replace("\\", "/").lower()
    if raw_path.startswith("/tmp/") or raw_path.startswith("/var/tmp/") or raw_path.startswith("/dev/shm/"):
        raise DeploymentConfigError("DATABASE_URL must use persistent storage outside temporary directories")
    db_path = Path(database_url.replace("sqlite:///", "", 1)).resolve()
    blocked_prefixes = [Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")]
    for blocked in blocked_prefixes:
        try:
            db_path.relative_to(blocked)
            raise DeploymentConfigError("DATABASE_URL must use persistent storage outside temporary directories")
        except ValueError:
            continue


@dataclass(frozen=True)
class DeploymentConfig:
    app_env: str
    database_url: str
    trading_mode: str
    auto_approve_paper: bool
    max_daily_orders: int
    max_open_positions: int
    max_position_equity_percent: float
    paper_broker_backend: str
    alpaca_order_submission_enabled: bool
    alpaca_paper_base_url: str
    scan_symbols: tuple[str, ...]
    scan_only_during_market_hours: bool
    run_timezone: str
    run_hour: int
    run_minute: int
    scan_interval_minutes: int
    continuous_runner_dry_run: bool
    notifications_enabled: bool
    kill_switch: bool

    @property
    def database_path(self) -> Path:
        return Path(self.database_url.replace("sqlite:///", "", 1))

    @property
    def run_tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.run_timezone)


def load_deployment_config(environ: dict[str, str] | None = None) -> DeploymentConfig:
    env = dict(environ or os.environ)
    app_env = str(env.get("APP_ENV", "production")).strip() or "production"
    database_url = str(env.get("DATABASE_URL", "sqlite:////var/lib/quant-bot/quant-bot.db")).strip()
    trading_mode = str(env.get("TRADING_MODE", "PAPER")).strip().upper() or "PAPER"
    auto_approve_paper = _parse_bool(env.get("AUTO_APPROVE_PAPER"), default=False)
    max_daily_orders = _parse_int("MAX_DAILY_ORDERS", env.get("MAX_DAILY_ORDERS", "5"), minimum=1)
    max_open_positions = _parse_int("MAX_OPEN_POSITIONS", env.get("MAX_OPEN_POSITIONS", "10"), minimum=1)
    max_position_equity_percent = float(str(env.get("MAX_POSITION_EQUITY_PERCENT", "10")).strip() or "10")
    if max_position_equity_percent <= 0 or max_position_equity_percent > 100:
        raise DeploymentConfigError("MAX_POSITION_EQUITY_PERCENT must be > 0 and <= 100")
    paper_broker_backend = str(env.get("PAPER_BROKER_BACKEND", "SIMULATED")).strip().upper() or "SIMULATED"
    alpaca_order_submission_enabled = _parse_bool(env.get("ALPACA_ORDER_SUBMISSION_ENABLED"), default=False)
    alpaca_paper_base_url = str(env.get("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)).strip() or ALPACA_PAPER_ENDPOINT
    scan_symbols_raw = str(env.get("SCAN_SYMBOLS", "")).strip()
    scan_symbols = tuple(symbol.strip().upper() for symbol in scan_symbols_raw.split(",") if symbol.strip())
    scan_only_during_market_hours = _parse_bool(env.get("SCAN_ONLY_DURING_MARKET_HOURS"), default=True)
    run_timezone = str(env.get("RUN_TIMEZONE", "America/New_York")).strip() or "America/New_York"
    run_hour = _parse_int("RUN_HOUR", env.get("RUN_HOUR", "9"), minimum=0, maximum=23)
    run_minute = _parse_int("RUN_MINUTE", env.get("RUN_MINUTE", "30"), minimum=0, maximum=59)
    scan_interval_minutes = _parse_int("SCAN_INTERVAL_MINUTES", env.get("SCAN_INTERVAL_MINUTES", "5"), minimum=1)
    continuous_runner_dry_run = _parse_bool(env.get("CONTINUOUS_RUNNER_DRY_RUN"), default=True)
    notifications_enabled = _parse_bool(env.get("NOTIFICATIONS_ENABLED"), default=False)
    kill_switch = _parse_bool(env.get("KILL_SWITCH"), default=False)

    if app_env.lower() not in {"production", "staging", "development", "test"}:
        raise DeploymentConfigError("APP_ENV must be one of production, staging, development, test")

    try:
        ZoneInfo(run_timezone)
    except Exception as exc:
        raise DeploymentConfigError("RUN_TIMEZONE must be a valid IANA timezone") from exc

    if trading_mode == "LIVE":
        raise DeploymentConfigError("LIVE trading is hard-blocked")

    if auto_approve_paper and trading_mode != "PAPER":
        raise DeploymentConfigError("AUTO_APPROVE_PAPER may only be true in PAPER mode")

    if paper_broker_backend not in {"ALPACA", "SIMULATED"}:
        raise DeploymentConfigError("PAPER_BROKER_BACKEND must be ALPACA or SIMULATED")

    if paper_broker_backend == "ALPACA":
        normalized_url = alpaca_paper_base_url.rstrip("/").lower()
        if normalized_url != ALPACA_PAPER_ENDPOINT:
            raise DeploymentConfigError("ALPACA_PAPER_BASE_URL must be https://paper-api.alpaca.markets")
        if not str(env.get("ALPACA_API_KEY", "")).strip() or not str(env.get("ALPACA_API_SECRET", "")).strip():
            raise DeploymentConfigError("ALPACA_API_KEY and ALPACA_API_SECRET are required for PAPER_BROKER_BACKEND=ALPACA")

    _validate_sqlite_url(database_url)

    return DeploymentConfig(
        app_env=app_env,
        database_url=database_url,
        trading_mode=trading_mode,
        auto_approve_paper=auto_approve_paper,
        max_daily_orders=max_daily_orders,
        max_open_positions=max_open_positions,
        max_position_equity_percent=max_position_equity_percent,
        paper_broker_backend=paper_broker_backend,
        alpaca_order_submission_enabled=alpaca_order_submission_enabled,
        alpaca_paper_base_url=alpaca_paper_base_url,
        scan_symbols=scan_symbols,
        scan_only_during_market_hours=scan_only_during_market_hours,
        run_timezone=run_timezone,
        run_hour=run_hour,
        run_minute=run_minute,
        scan_interval_minutes=scan_interval_minutes,
        continuous_runner_dry_run=continuous_runner_dry_run,
        notifications_enabled=notifications_enabled,
        kill_switch=kill_switch,
    )
