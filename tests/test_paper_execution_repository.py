from datetime import datetime, timedelta, timezone

from monitoring_db import MonitoringDatabase
from paper_execution_repository import MonitoringPaperExecutionRepository, PaperValidationRunPayload


def test_paper_execution_repository_approval_and_run(tmp_path):
    db_path = tmp_path / "paper_validation.db"
    database_url = f"sqlite:///{db_path}"
    db = MonitoringDatabase(database_url=database_url)
    db.ensure_schema()
    repo = MonitoringPaperExecutionRepository(database_url=database_url)

    now = datetime.now(timezone.utc)
    approval = {
        "approval_id": "ap-1",
        "strategy_id": "baseline_scanner",
        "strategy_version": "v1",
        "strategy_fingerprint": "fp",
        "portfolio_configuration": {"top_n": 5},
        "risk_configuration": {"max_position_size": 0.25},
        "benchmark": "SPY",
        "horizon": 20,
        "approved_by": "tester",
        "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "enabled": True,
        "notes": "ok",
        "configuration_fingerprint": "cfg",
    }
    repo.create_approval(approval)
    fetched = repo.fetch_approval("ap-1")
    assert fetched is not None
    assert fetched["approval_id"] == "ap-1"

    run_payload = PaperValidationRunPayload(
        run={
            "run_id": "run-1",
            "run_fingerprint": "rfp-1",
            "execution_fingerprint": "efp-1",
            "approval_id": "ap-1",
            "strategy_id": "baseline_scanner",
            "strategy_version": "v1",
            "strategy_fingerprint": "fp",
            "research_run_id": "r1",
            "scanner_timestamp": now.isoformat(),
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "mode": "SIMULATION",
            "status": "completed",
            "dry_run": True,
            "proposed_order_count": 1,
            "approved_order_count": 1,
            "rejected_order_count": 0,
            "submitted_order_count": 0,
            "filled_order_count": 0,
            "failed_order_count": 0,
            "configuration": {},
            "risk_snapshot": {},
            "performance": {},
            "warnings": [],
            "error_message": None,
        },
        orders=[
            {
                "paper_order_id": "o-1",
                "symbol": "AAA",
                "side": "BUY",
                "quantity": 1,
                "notional": 100,
                "target_weight": 0.1,
                "current_weight": 0.0,
                "weight_delta": 0.1,
                "reference_price": 100,
                "proposed_at": now.isoformat(),
                "risk_status": "approved",
                "risk_reason": "approved",
                "submission_status": "not_submitted_dry_run",
                "order_payload": {},
            }
        ],
        position_snapshots=[
            {
                "snapshot_id": "s-1",
                "captured_at": now.isoformat(),
                "positions": {"AAA": {"quantity": 1}},
                "cash": 900,
                "buying_power": 900,
                "portfolio_value": 1000,
                "gross_exposure": 0.1,
                "net_exposure": 0.1,
                "concentration": {},
                "reconciliation_status": "matched",
                "warnings": [],
            }
        ],
    )
    repo.save_validation_run(run_payload)
    latest = repo.fetch_latest_run()
    assert latest is not None
    assert latest["run_id"] == "run-1"
    duplicate = repo.fetch_latest_submitting_run_by_execution_fingerprint("efp-1")
    assert duplicate is None

    run_payload_execute = PaperValidationRunPayload(
        run={
            "run_id": "run-2",
            "run_fingerprint": "rfp-2",
            "execution_fingerprint": "efp-1",
            "approval_id": "ap-1",
            "strategy_id": "baseline_scanner",
            "strategy_version": "v1",
            "strategy_fingerprint": "fp",
            "research_run_id": "r1",
            "scanner_timestamp": now.isoformat(),
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "mode": "PAPER",
            "status": "completed",
            "dry_run": False,
            "proposed_order_count": 1,
            "approved_order_count": 1,
            "rejected_order_count": 0,
            "submitted_order_count": 1,
            "filled_order_count": 1,
            "failed_order_count": 0,
            "configuration": {},
            "risk_snapshot": {},
            "performance": {},
            "warnings": [],
            "error_message": None,
        },
        orders=[
            {
                "paper_order_id": "o-2",
                "symbol": "BBB",
                "side": "BUY",
                "quantity": 1,
                "notional": 100,
                "target_weight": 0.1,
                "current_weight": 0.0,
                "weight_delta": 0.1,
                "reference_price": 100,
                "proposed_at": now.isoformat(),
                "risk_status": "approved",
                "risk_reason": "approved",
                "submission_status": "filled",
                "filled_quantity": 1,
                "average_fill_price": 100,
                "order_payload": {},
            }
        ],
        position_snapshots=[
            {
                "snapshot_id": "s-2",
                "captured_at": now.isoformat(),
                "positions": {"BBB": {"quantity": 1}},
                "cash": 800,
                "buying_power": 800,
                "portfolio_value": 1000,
                "gross_exposure": 0.1,
                "net_exposure": 0.1,
                "concentration": {},
                "reconciliation_status": "matched",
                "warnings": [],
            }
        ],
    )
    repo.save_validation_run(run_payload_execute)
    duplicate = repo.fetch_latest_submitting_run_by_execution_fingerprint("efp-1")
    assert duplicate is not None
    assert duplicate["run_id"] == "run-2"
    assert len(repo.fetch_orders_for_run("run-1")) == 1
    repo.close()
