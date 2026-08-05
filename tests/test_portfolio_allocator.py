from portfolio_allocator import (
    AllocationPolicy,
    ExistingPosition,
    PortfolioCandidate,
    allocate_portfolio_recommendation,
)


def _candidate(symbol: str, rank: int = 1, sector: str = "Information Technology", score: float = 85.0, rr: float = 2.0):
    return PortfolioCandidate(
        symbol=symbol,
        rank=rank,
        quantum_score=score,
        strategy_score=80.0,
        strategy_id="trend_momentum_v1",
        strategy_version="1.0.0",
        confidence=75.0,
        volatility=10.0,
        reward_risk_ratio=rr,
        sector=sector,
        industry="Software",
        latest_price=100.0,
        liquidity_status="ok",
        data_quality_status="ok",
    )


def test_respects_max_position_size_and_max_positions():
    policy = AllocationPolicy(max_positions=2, max_position_percent=10.0, max_sector_percent=60.0)
    result = allocate_portfolio_recommendation(
        candidates=[_candidate("AAA", 1), _candidate("BBB", 2), _candidate("CCC", 3)],
        existing_positions=[],
        account_equity=10000.0,
        available_cash=10000.0,
        policy=policy,
    )
    assert len(result.proposed_allocations) == 2
    assert all(item.target_allocation_percent <= 10.0 + 1e-6 for item in result.proposed_allocations)


def test_preserves_cash_reserve_and_total_notional_bound():
    result = allocate_portfolio_recommendation(
        candidates=[_candidate("AAA", 1), _candidate("BBB", 2)],
        existing_positions=[],
        account_equity=10000.0,
        available_cash=10000.0,
        policy=AllocationPolicy(min_cash_reserve_percent=20.0),
    )
    assert result.cash_reserve >= 2000.0
    assert result.total_proposed_exposure <= 8000.0 + 1e-6


def test_existing_positions_count_toward_exposure_caps():
    existing = [ExistingPosition(symbol="OLD", quantity=20, latest_price=100.0, sector="Information Technology", strategy_id="trend_momentum_v1")]
    result = allocate_portfolio_recommendation(
        candidates=[_candidate("AAA", 1, sector="Information Technology")],
        existing_positions=existing,
        account_equity=10000.0,
        available_cash=10000.0,
        policy=AllocationPolicy(max_sector_percent=20.0),
    )
    assert result.proposed_allocations == []
    assert any("sector" in reason for reason in result.rejected_allocations[0].rejection_reasons)


def test_deterministic_ordering_and_rejections():
    candidates = [_candidate("BBB", 1), _candidate("AAA", 1)]
    first = allocate_portfolio_recommendation(candidates, [], 10000.0, 10000.0)
    second = allocate_portfolio_recommendation(list(reversed(candidates)), [], 10000.0, 10000.0)
    assert [row.symbol for row in first.proposed_allocations] == [row.symbol for row in second.proposed_allocations]


def test_rejects_low_quantum_score_bad_quality_and_low_rr():
    bad_score = _candidate("LOW", score=60.0)
    bad_score = PortfolioCandidate(**{**bad_score.__dict__, "data_quality_status": "bad"})
    bad_rr = _candidate("RR", rr=1.0)
    result = allocate_portfolio_recommendation(
        candidates=[bad_score, bad_rr],
        existing_positions=[],
        account_equity=10000.0,
        available_cash=10000.0,
        policy=AllocationPolicy(min_quantum_score=70.0, min_risk_reward=1.5),
    )
    assert len(result.proposed_allocations) == 0
    assert len(result.rejected_allocations) == 2


def test_zero_equity_fails_closed():
    result = allocate_portfolio_recommendation(
        candidates=[_candidate("AAA")],
        existing_positions=[],
        account_equity=0.0,
        available_cash=0.0,
    )
    assert result.proposed_allocations == []
    assert result.review_required is True
