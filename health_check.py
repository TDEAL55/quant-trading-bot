from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import BENCHMARK_SYMBOL
from deployment_config import DeploymentConfigError, load_deployment_config
from daily_run_repository import DailyRunRepository
from market_data import download_price_data
from monitoring_db import MonitoringDatabase


def _sqlite_path(database_url: str) -> Path:
    return Path(database_url.replace("sqlite:///", "", 1))


def run_health_check(database_url: str | None = None, minimum_free_gb: float = 1.0) -> dict[str, Any]:
    config = load_deployment_config({"DATABASE_URL": database_url} if database_url else None)
    checks: dict[str, bool] = {}
    errors: list[str] = []

    db = MonitoringDatabase(database_url=config.database_url)
    try:
        checks["database_connection"] = bool(db.enabled)
        db.ensure_schema()

        db_path = _sqlite_path(config.database_url)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=db_path.parent, delete=True):
            checks["writable_storage"] = True

        checks["configuration"] = True
        now = datetime.now(timezone.utc).date()
        start = (now - timedelta(days=30)).isoformat()
        end = now.isoformat()
        market_prices = download_price_data(BENCHMARK_SYMBOL, start, end)
        checks["market_data_dependency"] = bool(market_prices is not None and not market_prices.empty)

        free_bytes = shutil.disk_usage(db_path.parent).free
        checks["disk_space"] = free_bytes >= int(float(minimum_free_gb) * 1024 * 1024 * 1024)

        latest_successful_run = db.fetch_latest_successful_run()
        if latest_successful_run is None:
            daily_repo = DailyRunRepository(database_url=config.database_url)
            try:
                latest_daily = daily_repo.latest_run() or {}
            finally:
                daily_repo.close()
            latest_successful_run = latest_daily if str(latest_daily.get("execution_status") or "").lower() == "completed" else None
        checks["latest_successful_run"] = latest_successful_run is not None
    except DeploymentConfigError as exc:
        errors.append(str(exc))
        checks.setdefault("configuration", False)
        checks.setdefault("database_connection", False)
        checks.setdefault("writable_storage", False)
        checks.setdefault("market_data_dependency", False)
        checks.setdefault("disk_space", False)
        checks.setdefault("latest_successful_run", False)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        db.close()

    healthy = all(checks.get(name, False) for name in ["database_connection", "writable_storage", "configuration", "market_data_dependency", "disk_space", "latest_successful_run"]) and not errors
    return {"healthy": healthy, "checks": checks, "errors": errors, "database_url": config.database_url if 'config' in locals() else database_url}


def main() -> int:
    parser = argparse.ArgumentParser(description="Deployment health check")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--minimum-free-gb", type=float, default=1.0)
    args = parser.parse_args()

    result = run_health_check(database_url=args.database_url, minimum_free_gb=args.minimum_free_gb)
    print(result)
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
