from __future__ import annotations

from datetime import datetime, timezone

from live_event_risk import (
    build_event_risk_reviews,
    build_premarket_gap_shadow_reviews,
    classify_news_event_risk,
)


def test_objective_election_headline_blocks_new_entry_and_flags_held_review():
    now = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)
    risks = classify_news_event_risk(
        [
            {
                "headline": "Brazil stocks plunge after election upset",
                "summary": "A sharp policy repricing followed the vote.",
                "symbols": ["PAGS"],
                "created_at": "2026-10-26T12:00:00Z",
            },
            {
                "headline": "Ordinary product update",
                "summary": "No binary catalyst here.",
                "symbols": ["F"],
                "created_at": "2026-10-26T12:00:00Z",
            },
        ],
        now=now,
        lookback_hours=96,
    )

    assert risks["PAGS"]["entry_blocked"] is True
    assert risks["PAGS"]["categories"] == ["election_or_policy"]
    assert "F" not in risks
    review = build_event_risk_reviews(risks, held_symbols=["PAGS"])[0]
    assert review["recommendation"] == "REVIEW_BEFORE_NEXT_CLOSE"
    assert review["actionable"] is False
    assert review["orders_changed"] is False


def test_premarket_gap_shadow_flags_price_below_broker_stop_without_trading():
    reviews = build_premarket_gap_shadow_reviews(
        {"PAGS": {"lastday_price": 11.20}},
        [{"symbol": "PAGS", "side": "sell", "order_type": "stop", "stop_price": 10.38}],
        {"PAGS": 7.50},
        observed_at=datetime(2026, 10, 26, 12, tzinfo=timezone.utc),
        alert_percent=5,
    )

    assert len(reviews) == 1
    assert reviews[0]["below_protective_stop"] is True
    assert reviews[0]["recommendation"] == "CONTROLLED_OPENING_EXIT_REVIEW"
    assert reviews[0]["mode"] == "SHADOW_ONLY"
    assert reviews[0]["orders_changed"] is False


def test_premarket_gap_shadow_is_quiet_outside_premarket():
    assert build_premarket_gap_shadow_reviews(
        {"PAGS": {"lastday_price": 11.20}},
        [],
        {"PAGS": 7.50},
        observed_at=datetime(2026, 10, 26, 16, tzinfo=timezone.utc),
        alert_percent=5,
    ) == []
