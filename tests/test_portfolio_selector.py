from portfolio_selector import build_portfolio_shortlist, review_existing_positions


def test_shortlist_respects_max_positions_and_sector_caps():
    ranked = [
        {"rank": 1, "symbol": "AAA", "sector": "Tech", "overall_score": 85, "confidence": 70},
        {"rank": 2, "symbol": "BBB", "sector": "Tech", "overall_score": 84, "confidence": 69},
        {"rank": 3, "symbol": "CCC", "sector": "Tech", "overall_score": 83, "confidence": 68},
        {"rank": 4, "symbol": "DDD", "sector": "Energy", "overall_score": 82, "confidence": 67},
    ]
    payload = build_portfolio_shortlist(
        ranked,
        current_positions=[],
        current_cash=10_000,
        portfolio_value=10_000,
        max_positions=3,
        max_symbols_per_sector=2,
    )
    symbols = [item["symbol"] for item in payload["selected"]]
    assert "CCC" not in symbols
    assert len(payload["selected"]) <= 3


def test_shortlist_rejects_existing_pending_and_cooldown_symbols():
    ranked = [
        {"rank": 1, "symbol": "AAA", "sector": "Tech", "overall_score": 85, "confidence": 70},
        {"rank": 2, "symbol": "BBB", "sector": "Energy", "overall_score": 84, "confidence": 69},
        {"rank": 3, "symbol": "CCC", "sector": "Health", "overall_score": 83, "confidence": 68},
    ]
    payload = build_portfolio_shortlist(
        ranked,
        current_positions=[{"symbol": "AAA"}],
        pending_order_symbols=["BBB"],
        cooldown_symbols=["CCC"],
        current_cash=10_000,
        portfolio_value=10_000,
    )
    assert len(payload["selected"]) == 0
    assert len(payload["rejected"]) == 3


def test_shortlist_halts_when_risk_stop_active():
    ranked = [{"rank": 1, "symbol": "AAA", "sector": "Tech", "overall_score": 85, "confidence": 70}]
    payload = build_portfolio_shortlist(
        ranked,
        risk_state={"daily_loss_stop_active": True},
    )
    assert payload["selected"] == []
    assert payload["selection_summary"]["reason"] == "risk_stop_active"


def test_position_review_recommendations_cover_hold_watch_reduce_exit():
    positions = [
        {"symbol": "HOLD", "quantity": 1, "entry_price": 100, "market_price": 105, "holding_days": 10},
        {"symbol": "WATCH", "quantity": 1, "entry_price": 100, "market_price": 98, "holding_days": 10},
        {"symbol": "REDUCE", "quantity": 1, "entry_price": 100, "market_price": 95, "holding_days": 10},
        {"symbol": "EXIT", "quantity": 1, "entry_price": 100, "market_price": 90, "holding_days": 10},
    ]
    scores = {
        "HOLD": {"overall_score": 75, "confidence": 70, "signal": "BUY", "regime": "weak_bull", "component_scores": {"risk_quality": 60}, "data_quality": {"history_sufficient": True}},
        "WATCH": {"overall_score": 55, "confidence": 60, "signal": "HOLD", "regime": "weak_bull", "component_scores": {"risk_quality": 60}, "data_quality": {"history_sufficient": True}},
        "REDUCE": {"overall_score": 40, "confidence": 60, "signal": "REDUCE", "regime": "weak_bull", "component_scores": {"risk_quality": 60}, "data_quality": {"history_sufficient": True}},
        "EXIT": {"overall_score": 70, "confidence": 60, "signal": "BUY", "regime": "strong_bear", "component_scores": {"risk_quality": 60}, "data_quality": {"history_sufficient": True}},
    }
    payload = review_existing_positions(positions, scores)
    by_symbol = {item["symbol"]: item["recommendation"] for item in payload["reviews"]}
    assert by_symbol["HOLD"] == "HOLD"
    assert by_symbol["WATCH"] == "WATCH"
    assert by_symbol["REDUCE"] == "REDUCE"
    assert by_symbol["EXIT"] == "EXIT"
