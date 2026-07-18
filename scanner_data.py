from __future__ import annotations

from typing import Any

from monitoring_db import MonitoringDatabase


def fetch_latest_scan_status(database_url: str | None = None, database_factory=MonitoringDatabase) -> dict[str, Any]:
    db = database_factory(database_url=database_url)
    if not db.enabled:
        return {}
    db.ensure_schema()
    return db.fetch_latest_scanner_run() or {}


def fetch_top_ranked_stocks(database_url: str | None = None, limit: int = 20, database_factory=MonitoringDatabase) -> list[dict[str, Any]]:
    db = database_factory(database_url=database_url)
    if not db.enabled:
        return []
    db.ensure_schema()
    return db.fetch_top_scanner_results(limit=limit)


def fetch_scanner_sector_distribution(database_url: str | None = None, database_factory=MonitoringDatabase) -> list[dict[str, Any]]:
    db = database_factory(database_url=database_url)
    if not db.enabled:
        return []
    db.ensure_schema()
    return db.fetch_scanner_sector_distribution()


def fetch_scan_rejection_reasons(database_url: str | None = None, limit: int = 50, database_factory=MonitoringDatabase) -> list[dict[str, Any]]:
    db = database_factory(database_url=database_url)
    if not db.enabled:
        return []
    db.ensure_schema()
    return db.fetch_scanner_rejections(limit=limit)


def fetch_position_exit_watch(database_url: str | None = None, limit: int = 50, database_factory=MonitoringDatabase) -> list[dict[str, Any]]:
    db = database_factory(database_url=database_url)
    if not db.enabled:
        return []
    db.ensure_schema()
    return db.fetch_latest_position_reviews(limit=limit)


def fetch_scan_history(database_url: str | None = None, limit: int = 25, database_factory=MonitoringDatabase) -> list[dict[str, Any]]:
    db = database_factory(database_url=database_url)
    if not db.enabled:
        return []
    db.ensure_schema()
    return db.fetch_scanner_runs(limit=limit)
