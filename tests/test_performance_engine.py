from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from performance_engine import run_performance_intelligence


class _RepoStub:
    def __init__(self, database_url=None):
        self.db = type("_Db", (), {"enabled": True, "ensure_schema": lambda self: None})()
        self.saved_payload = None

    def fetch_source_runs(self, limit=5000):
        return [
            {
                "run_id": "paper-1",
                "completed_at": "2026-07-17T20:00:00+00:00",
                "scanner_timestamp": "2026-07-17T00:00:00+00:00",
                "started_at": "2026-07-17T19:59:00+00:00",
            },
            {
                "run_id": "paper-2",
                "completed_at": "2026-07-18T20:00:00+00:00",
                "scanner_timestamp": "2026-07-18T00:00:00+00:00",
                "started_at": "2026-07-18T19:59:00+00:00",
            },
        ]

    def fetch_snapshots_for_run(self, run_id):
        if run_id == "paper-1":
            return [{"captured_at": "2026-07-17T20:00:00+00:00", "portfolio_value": 10000.0, "cash": 9600.0, "buying_power": 9600.0, "positions": {"AAA": {"quantity": 4.0, "avg_price": 100.0}}}]
        return [{"captured_at": "2026-07-18T20:00:00+00:00", "portfolio_value": 10150.0, "cash": 9700.0, "buying_power": 9700.0, "positions": {"AAA": {"quantity": 4.5, "avg_price": 100.0}}}]

    def fetch_orders_for_run(self, run_id):
        if run_id == "paper-1":
            return [{"submission_status": "filled", "side": "BUY", "filled_quantity": 4.0, "average_fill_price": 100.0, "notional": 400.0, "order_payload": {"hold_days": 1}}]
        return [{"submission_status": "filled", "side": "SELL", "filled_quantity": 1.0, "average_fill_price": 110.0, "notional": 110.0, "order_payload": {"hold_days": 2}}]

    def save_run(self, payload):
        self.saved_payload = payload
        return {"run_id": payload.run.get("run_id"), "metric_rows": len(payload.metrics)}

    def close(self):
        return None


def _benchmark_df() -> pd.DataFrame:
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=20, freq="D")
    return pd.DataFrame({"Close": [100.0 + i for i in range(len(idx))]}, index=idx)


def test_run_performance_intelligence_deterministic(monkeypatch):
    monkeypatch.setattr("performance_engine.PerformanceRepository", _RepoStub)
    monkeypatch.setattr("performance_engine.download_price_data", lambda *args, **kwargs: _benchmark_df())

    result = run_performance_intelligence(database_url="sqlite:///unused.db")

    assert result["status"] == "completed"
    assert result["source_run_count"] == 2
    assert result["metrics"]["portfolio_value"] == 10150.0
    assert "sharpe_ratio" in result["metrics"]
