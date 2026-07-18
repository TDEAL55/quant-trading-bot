from paper_validation_data import fetch_paper_validation_dashboard_payload


class _FakeDb:
    enabled = True

    def ensure_schema(self):
        return None


class _FakeRepo:
    def __init__(self, database_url=None):
        self.db = _FakeDb()

    def list_approvals(self, enabled_only=False):
        return [{"approval_id": "a1"}]

    def fetch_latest_run(self):
        return {"run_id": "r1"}

    def fetch_orders_for_run(self, run_id):
        return [{"paper_order_id": "o1"}] if run_id == "r1" else []

    def fetch_position_snapshots_for_run(self, run_id):
        return [{"snapshot_id": "s1"}] if run_id == "r1" else []

    def list_runs(self, limit=50):
        return [{"run_id": "r1"}]

    def close(self):
        return None


def test_fetch_paper_validation_dashboard_payload(monkeypatch):
    monkeypatch.setattr("paper_validation_data.MonitoringPaperExecutionRepository", _FakeRepo)
    payload = fetch_paper_validation_dashboard_payload(database_url="sqlite:///x.db")
    assert payload["db_connected"] is True
    assert payload["latest_run"]["run_id"] == "r1"
    assert len(payload["latest_orders"]) == 1
