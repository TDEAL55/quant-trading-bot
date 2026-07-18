from datetime import datetime, timedelta, timezone

from paper_approval import approval_configuration_fingerprint, build_approval_record, validate_approval


def test_approval_fingerprint_is_deterministic():
    fp1 = approval_configuration_fingerprint(
        strategy_id="baseline_scanner",
        strategy_version="v1",
        strategy_fingerprint="abc123",
        portfolio_configuration={"top_n": 5, "weighting_method": "equal_weight"},
        risk_configuration={"max_position_size": 0.25},
        benchmark="SPY",
        horizon=20,
    )
    fp2 = approval_configuration_fingerprint(
        strategy_id="baseline_scanner",
        strategy_version="v1",
        strategy_fingerprint="abc123",
        portfolio_configuration={"weighting_method": "equal_weight", "top_n": 5},
        risk_configuration={"max_position_size": 0.25},
        benchmark="SPY",
        horizon=20,
    )
    assert fp1 == fp2


def test_validate_approval_rejects_live_mode():
    now = datetime.now(timezone.utc)
    approval = build_approval_record(
        approval_id="a1",
        strategy_id="baseline_scanner",
        strategy_version="v1",
        strategy_fingerprint="abc",
        portfolio_configuration={"top_n": 5},
        risk_configuration={"max_position_size": 0.25},
        benchmark="SPY",
        horizon=20,
        approved_by="tester",
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        enabled=True,
        notes="ok",
    )
    result = validate_approval(
        approval=approval,
        expected_strategy_id="baseline_scanner",
        expected_strategy_version="v1",
        expected_strategy_fingerprint="abc",
        expected_portfolio_configuration={"top_n": 5},
        expected_risk_configuration={"max_position_size": 0.25},
        mode="LIVE",
        broker_type="paper",
    )
    assert result.valid is False
    assert any("LIVE mode is rejected" in item for item in result.reasons)


def test_validate_approval_valid_path():
    now = datetime.now(timezone.utc)
    portfolio = {"top_n": 5, "weighting_method": "equal_weight"}
    risk = {"max_position_size": 0.25, "max_daily_loss": 500}
    approval = build_approval_record(
        approval_id="a2",
        strategy_id="baseline_scanner",
        strategy_version="v1",
        strategy_fingerprint="fp-1",
        portfolio_configuration=portfolio,
        risk_configuration=risk,
        benchmark="SPY",
        horizon=20,
        approved_by="tester",
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        enabled=True,
        notes="ok",
    )
    result = validate_approval(
        approval=approval,
        expected_strategy_id="baseline_scanner",
        expected_strategy_version="v1",
        expected_strategy_fingerprint="fp-1",
        expected_portfolio_configuration=portfolio,
        expected_risk_configuration=risk,
        mode="SIMULATION",
        broker_type="simulation",
    )
    assert result.valid is True
    assert result.fingerprint_status == "matched"
