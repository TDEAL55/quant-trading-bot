from __future__ import annotations

from sprint_10_1_validation import PaperTestProfile, SimulatedFillBroker, run_sprint_10_1_validation


def _scan_payload_with_candidates() -> dict:
    return {
        "scan_results": [
            {
                "symbol": "AAA",
                "overall_score": 82.0,
                "confidence": 78.0,
                "signal": "BUY",
                "component_scores": {"trend": 80.0, "momentum": 76.0, "risk_quality": 72.0},
                "reasons": ["trend and momentum aligned"],
                "warnings": [],
                "status": "scored",
                "eligible": True,
                "rejection_reasons": [],
            }
        ],
        "ranked_candidates": [
            {
                "symbol": "AAA",
                "overall_score": 82.0,
                "confidence": 78.0,
                "signal": "BUY",
                "component_scores": {"trend": 80.0, "momentum": 76.0, "risk_quality": 72.0},
                "reasons": ["trend and momentum aligned"],
                "warnings": [],
                "rank": 1,
            }
        ],
        "summary": {"eligible_count": 1, "success_count": 1, "rejection_count": 0, "error_count": 0},
    }


def _scan_payload_without_candidates() -> dict:
    return {
        "scan_results": [
            {
                "symbol": "BBB",
                "overall_score": 58.0,
                "confidence": 49.0,
                "signal": "HOLD",
                "component_scores": {},
                "status": "rejected",
                "eligible": False,
                "rejection_reasons": ["overall score below minimum", "confidence below minimum"],
            }
        ],
        "ranked_candidates": [],
        "summary": {"eligible_count": 0, "success_count": 0, "rejection_count": 1, "error_count": 0},
    }


def test_live_profile_is_rejected() -> None:
    profile = PaperTestProfile(mode="LIVE")
    try:
        run_sprint_10_1_validation(database_url=None, profile=profile)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "LIVE mode is rejected" in str(exc)


def test_manual_approval_gate_blocks_run(monkeypatch) -> None:
    monkeypatch.setattr("sprint_10_1_validation.run_scan", lambda universe: _scan_payload_with_candidates())
    monkeypatch.setattr(
        "sprint_10_1_validation.run_shortlist_only",
        lambda scan_payload, positions, cash, portfolio_value: {"selected": [scan_payload["ranked_candidates"][0]], "rejected": []},
    )
    monkeypatch.setattr("sprint_10_1_validation.journal_scanner_run", lambda **kwargs: {"status": "stored"})

    result = run_sprint_10_1_validation(database_url=None, manual_approval="NO", execute=False)

    assert result["status"] == "approval_rejected"
    assert result["manual_approval"]["approved"] is False


def test_no_candidate_result_reports_reasons(monkeypatch) -> None:
    monkeypatch.setattr("sprint_10_1_validation.run_scan", lambda universe: _scan_payload_without_candidates())
    monkeypatch.setattr("sprint_10_1_validation.run_shortlist_only", lambda *args, **kwargs: {"selected": [], "rejected": []})
    monkeypatch.setattr("sprint_10_1_validation.journal_scanner_run", lambda **kwargs: {"status": "stored"})

    result = run_sprint_10_1_validation(database_url=None, manual_approval="YES", execute=False)

    assert result["status"] == "no_candidate"
    assert result["no_candidate"]["reason_counts"]
    assert result["no_candidate"]["reason_counts"][0]["count"] >= 1


def test_completed_flow_executes_single_order(monkeypatch) -> None:
    captured_create = {}

    def _fake_create_approval(**kwargs):
        captured_create.update(kwargs)
        return {"saved": {"approval_id": kwargs["approval_id"]}, "approval": {"approval_id": kwargs["approval_id"]}}

    def _fake_run_paper_validation(**kwargs):
        assert kwargs["execute"] is True
        assert kwargs["confirm"] is True
        return {
            "run_id": "pv-1",
            "metrics": {"submitted_orders": 1, "filled_orders": 1},
            "status": "completed",
        }

    monkeypatch.setattr("sprint_10_1_validation.run_scan", lambda universe: _scan_payload_with_candidates())
    monkeypatch.setattr(
        "sprint_10_1_validation.run_shortlist_only",
        lambda scan_payload, positions, cash, portfolio_value: {"selected": [scan_payload["ranked_candidates"][0]], "rejected": []},
    )
    monkeypatch.setattr("sprint_10_1_validation.journal_scanner_run", lambda **kwargs: {"status": "stored"})
    monkeypatch.setattr("sprint_10_1_validation.create_approval", _fake_create_approval)
    monkeypatch.setattr("sprint_10_1_validation.run_paper_validation", _fake_run_paper_validation)
    monkeypatch.setattr(
        "sprint_10_1_validation.fetch_paper_validation_dashboard_payload",
        lambda database_url: {"db_connected": True, "latest_run": {"run_id": "pv-1"}},
    )

    result = run_sprint_10_1_validation(
        database_url="sqlite:///test.db",
        manual_approval="YES",
        execute=True,
        broker=SimulatedFillBroker(mode="PAPER"),
    )

    assert result["status"] == "completed"
    assert result["paper_validation"]["metrics"]["submitted_orders"] == 1
    assert captured_create["portfolio_configuration"]["maximum_orders"] == 1
