from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


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
    run_timezone: str
    run_hour: int
    run_minute: int
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
    max_daily_orders = _parse_int("MAX_DAILY_ORDERS", env.get("MAX_DAILY_ORDERS", "1"), minimum=1)
    run_timezone = str(env.get("RUN_TIMEZONE", "America/New_York")).strip() or "America/New_York"
    run_hour = _parse_int("RUN_HOUR", env.get("RUN_HOUR", "9"), minimum=0, maximum=23)
    run_minute = _parse_int("RUN_MINUTE", env.get("RUN_MINUTE", "30"), minimum=0, maximum=59)
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

    _validate_sqlite_url(database_url)

    return DeploymentConfig(
        app_env=app_env,
        database_url=database_url,
        trading_mode=trading_mode,
        auto_approve_paper=auto_approve_paper,
        max_daily_orders=max_daily_orders,
        run_timezone=run_timezone,
        run_hour=run_hour,
        run_minute=run_minute,
        notifications_enabled=notifications_enabled,
        kill_switch=kill_switch,
    )
