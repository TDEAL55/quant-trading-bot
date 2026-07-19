from __future__ import annotations

from daily_research_runner import run_daily_research_cycle


def _execution_payload(status: str, reconciliation_status: str = "matched", mismatch_count: int = 0, dashboard_updated: bool = True) -> dict:
    payload = {
        "status": status,
        "market": {"market_timestamp": "2026-07-18T20:00:00+00:00", "session_type": "latest_completed_session", "fresh": True},
        "universe_size": 20,
        "qualified_securities": 2,
        "selected_symbol": "JPM",
        "overall_score": 83.02,
        "confidence": 68.82,
        "approval": {"granted": True},
        "risk_result": {"approved": True},
        "paper_order": {"order_id": "order-1", "shares": 1.5, "fill_price": 100.0},
        "cash_after": 9500.0,
        "reconciliation": {"reconciliation_status": reconciliation_status, "position_mismatch_count": mismatch_count},
        "dashboard_updated": dashboard_updated,
        "dashboard_payload": {"latest_run": {"run_id": "pv-1"}},
    }
    if status == "no_trade":
        payload.pop("selected_symbol", None)
        payload["qualified_securities"] = 0
    if status == "approval_rejected":
        payload["approval"] = {"granted": False}
    if status == "risk_rejected":
        payload["risk_result"] = {"approved": False}
        payload["paper_order"] = {}
    return payload


def _performance_payload() -> dict:
    return {
        "status": "completed",
        "run_id": "perf-1",
        "metrics": {
            "portfolio_value": 10000.0,
            "daily_return": 0.01,
            "total_return": 0.02,
            "current_drawdown": -0.01,
            "sharpe_ratio": 1.2,
        },
    }


def test_successful_run_status_completed():
    result = run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=lambda **kwargs: _execution_payload("completed"),
        performance_runner=lambda **kwargs: _performance_payload(),
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )
    assert result["execution_status"] == "completed"
    assert result["report"]["Selected Symbols"] == ["JPM"]


def test_no_candidates_status():
    result = run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=lambda **kwargs: _execution_payload("no_trade"),
        performance_runner=lambda **kwargs: _performance_payload(),
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )
    assert result["execution_status"] == "no_candidates"


def test_approval_denied_status():
    result = run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=lambda **kwargs: _execution_payload("approval_rejected"),
        performance_runner=lambda **kwargs: _performance_payload(),
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )
    assert result["execution_status"] == "approval_denied"


def test_risk_rejected_status():
    result = run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=lambda **kwargs: _execution_payload("risk_rejected"),
        performance_runner=lambda **kwargs: _performance_payload(),
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )
    assert result["execution_status"] == "risk_rejected"


def test_reconciliation_failure_status():
    result = run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=lambda **kwargs: _execution_payload("completed", reconciliation_status="mismatch", mismatch_count=1),
        performance_runner=lambda **kwargs: _performance_payload(),
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )
    assert result["execution_status"] == "reconciliation_failed"


def test_dashboard_update_status():
    result = run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=lambda **kwargs: _execution_payload("completed", dashboard_updated=True),
        performance_runner=lambda **kwargs: _performance_payload(),
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )
    assert result["dashboard_updated"] is True


def test_daily_workflow_calls_both_stages():
    calls = []

    def _execution_runner(**kwargs):
        calls.append("execution")
        return _execution_payload("completed")

    def _performance_runner(**kwargs):
        calls.append("performance")
        return _performance_payload()

    run_daily_research_cycle(
        database_url="sqlite:///unused.db",
        persist=False,
        execution_runner=_execution_runner,
        performance_runner=_performance_runner,
        performance_payload_loader=lambda _db: {"latest_run": {"run_id": "perf-1"}},
    )

    assert calls == ["execution", "performance"]
