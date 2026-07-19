from __future__ import annotations

from performance_dashboard import fetch_performance_dashboard_payload


class _RepoStub:
    def __init__(self, database_url=None):
        self.db = type("_Db", (), {"enabled": True, "ensure_schema": lambda self: None})()

    def latest_run(self):
        return {"run_id": "perf-1", "status": "completed"}

    def fetch_daily_equity(self, run_id):
        return [
            {"equity_date": "2026-07-17", "portfolio_value": 10000.0, "daily_return": 0.0},
            {"equity_date": "2026-07-18", "portfolio_value": 10100.0, "daily_return": 0.01},
        ]

    def fetch_trade_statistics(self, run_id):
        return [{"trade_date": "2026-07-18", "trade_count": 1, "win_rate": 1.0}]

    def fetch_portfolio_snapshots(self, run_id):
        return [{"captured_at": "2026-07-18T20:00:00+00:00", "sector_allocation": {"Unknown": 1.0}}]

    def fetch_metrics(self, run_id):
        return [
            {"metric_name": "portfolio_value", "metric_value": 10100.0},
            {"metric_name": "sharpe_ratio", "metric_value": 1.5},
        ]

    def close(self):
        return None


def test_fetch_performance_dashboard_payload(monkeypatch):
    monkeypatch.setattr("performance_dashboard.PerformanceRepository", _RepoStub)
    payload = fetch_performance_dashboard_payload(database_url="sqlite:///unused.db")

    assert payload["latest_run"]["run_id"] == "perf-1"
    assert payload["metrics_map"]["portfolio_value"] == 10100.0
    assert payload["daily_report"]["as_of_date"] == "2026-07-18"
    assert payload["weekly_summary"]
    assert payload["monthly_summary"]
