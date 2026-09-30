from trade_review import build_trade_review, index_submission_records


def _event(symbol, pnl, entry, exit_, strategy="unknown", hour="14"):
    return {
        "symbol": symbol,
        "entry_strategy_id": strategy,
        "exit_timestamp": f"2026-09-02T{hour}:00:00+00:00",
        "average_exit_price": exit_,
        "quantity_closed": 1,
        "realized_pnl": pnl,
        "confidence": "exact",
        "matched_lots": [{
            "order_key": f"entry-{symbol}",
            "entry_timestamp": f"2026-09-01T{hour}:00:00+00:00",
            "entry_price": entry,
            "quantity": 1,
            "direction": "long",
            "strategy_id": strategy,
        }],
    }


def test_zero_trades_does_not_invent_performance_or_loss_cause():
    report = build_trade_review({"confidence": "exact", "realized_events": []})

    assert report["metrics"]["number_of_trades"] == 0
    assert report["metrics"]["win_rate"] is None
    assert report["metrics"]["expectancy_per_trade"] is None
    assert report["conclusions"]["loss_cause"] == "no_completed_losses_to_diagnose"
    assert report["conclusions"]["best_strategy"] is None


def test_metrics_breakdowns_streaks_and_insufficient_sample_are_explicit():
    events = [
        _event("AAA", 10, 100, 110, "trend", "14"),
        _event("BBB", -4, 50, 46, "mean", "15"),
        _event("CCC", -6, 50, 44, "mean", "15"),
    ]
    records = [
        {"order_id": "entry-AAA", "strategy_id": "trend", "market_regime": "bull", "expected_reward_risk": 2,
         "stop_price": 95, "target_price": 110, "confirmation_categories": ["trend", "volume"],
         "entry_reason": "trend confirmed", "rule_checks": {"regime_route": True, "quality_threshold": True,
         "risk_limit": True, "protective_exit": True}},
        {"order_id": "entry-BBB", "strategy_id": "mean", "market_regime": "sideways", "expected_reward_risk": 1.5},
        {"order_id": "entry-CCC", "strategy_id": "mean", "market_regime": "sideways", "expected_reward_risk": 1.5},
    ]
    report = build_trade_review({"confidence": "exact", "realized_events": events}, submission_records=records)

    metrics = report["metrics"]
    assert metrics["total_pnl"] == 0
    assert metrics["win_rate"] == 0.333333
    assert metrics["average_winner"] == 10
    assert metrics["average_loser"] == -5
    assert metrics["profit_factor"] == 1
    assert metrics["expectancy_per_trade"] == 0
    assert metrics["maximum_drawdown"] == 10
    assert metrics["maximum_consecutive_losses"] == 2
    assert report["conclusions"]["sample_sufficient"] is False
    assert report["conclusions"]["best_strategy"] is None
    by_strategy = {row["strategy"]: row for row in report["breakdowns"]["by_strategy"]}
    assert by_strategy["trend"]["pnl"] == 10
    assert by_strategy["mean"]["pnl"] == -10


def test_excursions_and_rule_compliance_use_recorded_evidence_only():
    event = _event("AAA", 5, 100, 105, "trend")
    records = [{
        "client_order_id": "entry-AAA",
        "strategy_id": "trend",
        "market_regime": "bull",
        "rule_checks": {"regime_route": True, "quality_threshold": True, "risk_limit": True, "protective_exit": True},
    }]
    prices = {"AAA": [
        {"timestamp": "2026-09-01T15:00:00+00:00", "high": 108, "low": 97},
        {"timestamp": "2026-09-02T14:00:00+00:00", "high": 106, "low": 99},
    ]}
    report = build_trade_review({"realized_events": [event]}, submission_records=records, price_paths=prices)
    trade = report["trades"][0]

    assert trade["maximum_favorable_excursion"] == 8
    assert trade["maximum_adverse_excursion"] == 3
    assert trade["followed_strategy_rules"] is True


def test_submission_index_supports_broker_and_client_ids():
    row = {"order_id": "one", "client_order_id": "two", "strategy_id": "trend"}
    index = index_submission_records([row])
    assert index["one"] == row
    assert index["two"] == row
