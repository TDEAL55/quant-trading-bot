from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import controlled_live_runner as module
from controlled_live_runner import LiveStateStore, run_controlled_live_cycle
from live_risk_policy import LiveRiskSettings


class Broker:
    def __init__(self, positions=None, orders=None):
        self.positions = positions or {}
        self.orders = orders or []
        self.submitted = []
    def get_account(self):
        return {"status":"ACTIVE", "equity":300, "last_equity":300, "day_pl":0, "cash":300,
            "multiplier":1, "trading_blocked":False, "account_blocked":False}
    def get_positions(self): return self.positions
    def get_open_orders(self): return self.orders
    def get_order_history(self, limit=500): return []
    def get_market_clock(self): return {"is_open": True}
    def submit_bracket_entry(self, **kwargs):
        self.submitted.append(kwargs)
        return {"order_id":"oid", "client_order_id":kwargs["client_order_id"], "symbol":kwargs["symbol"],
            "requested_quantity":kwargs["quantity"], "reference_price":kwargs["reference_price"],
            "stop_price":kwargs["stop_price"], "target_price":kwargs["target_price"], "status":"accepted"}
    def get_tradable_stock_assets(self, *, include_etfs=False):
        assert include_etfs is False
        return [
            {"symbol": "AAL", "company_name": "American Airlines"},
            {"symbol": "F", "company_name": "Ford Motor"},
        ]


def armed():
    return LiveRiskSettings(enabled=True, order_submission_enabled=True, kill_switch=False,
        confirmation="ENABLE_LIVE_MICRO_TRADING", private_dashboard_confirmed=True,
        minimum_strategy_score=75, minimum_confidence=70, allowed_symbols=("F",))


def recorded_submission(
    *,
    symbol="NVDA",
    strategy_id="stock_trend_pullback_v3",
    quantity=1,
    reference_price=100,
    stop_price=95,
    target_price=115,
    order_id="parent-order",
    client_order_id="qtb-live-parent",
):
    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "recorded_at": "2026-09-29T14:00:00+00:00",
        "order_id": order_id,
        "client_order_id": client_order_id,
        "quantity": quantity,
        "reference_price": reference_price,
        "stop_price": stop_price,
        "target_price": target_price,
    }


def broker_bracket_legs(
    *,
    symbol="NVDA",
    quantity=1,
    stop_price=95,
    target_price=115,
    side="sell",
    parent_order_id="parent-order",
    parent_client_order_id="qtb-live-parent",
):
    common = {
        "symbol": symbol,
        "side": side,
        "requested_quantity": quantity,
        "filled_quantity": 0,
        "parent_order_id": parent_order_id,
        "parent_client_order_id": parent_client_order_id,
    }
    return [
        {
            **common,
            "order_id": f"{parent_order_id}-target",
            "status": "new",
            "order_type": "limit",
            "limit_price": target_price,
        },
        {
            **common,
            "order_id": f"{parent_order_id}-stop",
            "status": "held",
            "order_type": "stop",
            "stop_price": stop_price,
        },
    ]


def filled_parent_order(
    *,
    symbol="NVDA",
    quantity=1,
    average_fill_price=100,
    side="buy",
    filled_at="2026-09-29T14:01:00+00:00",
    order_id="parent-order",
    client_order_id="qtb-live-parent",
):
    return {
        "order_id": order_id,
        "client_order_id": client_order_id,
        "symbol": symbol,
        "side": side,
        "status": "filled",
        "requested_quantity": quantity,
        "filled_quantity": quantity,
        "average_fill_price": average_fill_price,
        "filled_at": filled_at,
    }


def scanner(_records):
    return {"ranked_candidates": [{"symbol":"F", "latest_price":12}]}


def signal(_candidate):
    return [{"strategy_id":"stock_trend_pullback_v3", "strategy_version":"3", "signal":"BUY",
        "strategy_score":80, "confidence":80, "data_quality_status":"ok", "market_regime":"bull",
        "expected_reward_risk": 2.0, "entry_reason": "trend and volume agree",
        "target_or_exit_rule": "trail strong winners", "supporting_factors": {
            "confirmations": {"trend": True, "volume": True},
            "components": {"trend": 88, "volume": 76, "relative_strength": 81, "volatility": 60},
        }}]


def mean_reversion_signal(_candidate):
    return [{"strategy_id":"stock_mean_reversion_v2", "strategy_version":"2", "signal":"BUY",
        "strategy_score":82, "confidence":76, "data_quality_status":"ok", "market_regime":"sideways",
        "supporting_factors": {"confirmations": {"momentum": True, "support": True}}}]


def test_disabled_cycle_never_touches_broker():
    result = run_controlled_live_cycle(environ={"TRADING_MODE":"PAPER"})
    assert result["status"] == "blocked"
    assert not result["submitted"]


def test_healthy_cycle_submits_once_then_daily_limit_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    broker = Broker()
    store = LiveStateStore(tmp_path / "live.json")
    now = lambda: datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    env = {"TRADING_MODE":"LIVE"}
    first = run_controlled_live_cycle(environ=env, settings=armed(), broker=broker, scanner=scanner,
        state_store=store, now_provider=now)
    second = run_controlled_live_cycle(environ=env, settings=armed(), broker=broker, scanner=scanner,
        state_store=store, now_provider=now)
    assert first["status"] == "submitted"
    assert broker.submitted[0]["quantity"] == 2
    saved = store.load()["submissions"][0]
    assert saved["market_regime"] == "bull"
    assert saved["confirmation_categories"] == ["trend", "volume"]
    assert saved["entry_reason"] == "trend and volume agree"
    assert saved["position_risk_dollars"] > 0
    assert saved["rule_checks"]["regime_route"] is True
    assert second["status"] == "blocked"
    assert "daily_new_order_limit_reached" in second["reasons"]


def test_unprotected_position_blocks_new_entry(tmp_path):
    broker = Broker(positions={"JPM":{"market_value":25}})
    result = run_controlled_live_cycle(environ={"TRADING_MODE":"LIVE"}, settings=armed(), broker=broker,
        scanner=scanner, state_store=LiveStateStore(tmp_path / "live.json"))
    assert result["status"] == "blocked"
    assert "unprotected_live_positions_require_manual_review" in result["reasons"]


def test_high_price_symbol_cannot_create_fractional_order(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    result = run_controlled_live_cycle(environ={"TRADING_MODE":"LIVE"}, settings=armed(), broker=Broker(),
        scanner=lambda _: {"ranked_candidates":[{"symbol":"F", "latest_price":31}]},
        state_store=LiveStateStore(tmp_path / "live.json"))
    assert result["status"] == "no_trade"
    assert result["candidate_funnel"]["rejection_counts"] == {"position_size_below_one_share": 1}
    assert result["candidate_funnel"]["near_misses"][0]["symbol"] == "F"


def test_candidate_requires_two_independent_confirmations(tmp_path, monkeypatch):
    weak = signal({})
    weak[0]["supporting_factors"]["confirmations"] = {"trend": True, "volume": False}
    monkeypatch.setattr(module, "evaluate_all_strategies", lambda _candidate: weak)
    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(), broker=Broker(), scanner=scanner,
        state_store=LiveStateStore(tmp_path / "live.json"),
    )
    assert result["status"] == "no_trade"
    assert result["candidate_funnel"]["rejection_counts"] == {
        "insufficient_independent_confirmations": 1
    }


def test_cycle_result_persists_funnel_and_shadow_review(tmp_path):
    store = LiveStateStore(tmp_path / "live.json")
    store.record_cycle_result({
        "status": "no_trade",
        "candidate_funnel": {"rejection_counts": {"too_small": 1}},
        "live_exit_shadow": [{"symbol": "F", "mode": "SHADOW_ONLY"}],
    })
    saved = store.load()["last_cycle"]
    assert saved["candidate_funnel"]["rejection_counts"] == {"too_small": 1}
    assert saved["live_exit_shadow"][0]["mode"] == "SHADOW_ONLY"


def test_live_exit_shadow_reports_stop_ratchet_without_changing_orders():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission()],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(),
        order_history=[filled_parent_order()],
    )

    assert len(reviews) == 1
    review = reviews[0]
    assert review["trailing_shadow_enabled"] is True
    assert review["current_protective_stop_price"] == 95
    assert review["proposed_protective_stop_price"] == 107
    assert review["would_raise_protective_stop"] is True
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "RATCHET_STOP_UP"
    assert review["actionable"] is False
    assert review["orders_changed"] is False


def test_live_exit_shadow_disabled_setting_suppresses_ratchet_recommendation():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission()],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=replace(armed(), trailing_stop_ratchet_shadow_enabled=False),
        open_orders=broker_bracket_legs(),
        order_history=[filled_parent_order()],
    )

    review = reviews[0]
    assert review["trailing_shadow_enabled"] is False
    assert review["current_protective_stop_price"] == 95
    assert review["proposed_protective_stop_price"] is None
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "DISABLED"


def test_live_exit_shadow_uses_filled_parent_time_and_ignores_prefill_highs():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_price": "100",
                "current_price": "104",
            }
        },
        {
            "submissions": [
                recorded_submission()
            ],
            "price_paths": {
                "NVDA": [
                    {"timestamp": "2026-09-30T15:00:00+00:00", "high": 130},
                    {"timestamp": "2026-10-02T15:00:00+00:00", "high": 105},
                ]
            },
        },
        observed_at=datetime(2026, 10, 2, 16, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(),
        order_history=[
            filled_parent_order(filled_at="2026-10-01T14:00:00+00:00")
        ],
    )

    review = reviews[0]
    assert review["entry_fill_timestamp"] == "2026-10-01T14:00:00+00:00"
    assert review["entry_timestamp_source"] == "BROKER_FILLED_PARENT"
    assert review["post_entry_price_observation_count"] == 1
    assert review["peak_return_percent"] == 5
    assert review["trailing_activation_reached"] is False
    assert review["proposed_protective_stop_price"] is None
    assert review["trailing_action"] == "NOT_ACTIVATED"


def test_live_exit_shadow_uses_post_entry_lows_for_short_position_peak():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "-2",
                "avg_entry_price": "100",
                "current_price": "92",
            }
        },
        {
            "submissions": [
                recorded_submission(
                    strategy_id="stock_bearish_trend_v2",
                    quantity=2,
                    stop_price=104,
                    target_price=88,
                )
            ],
            "price_paths": {
                "NVDA": [
                    {
                        "timestamp": "2026-09-30T15:00:00+00:00",
                        "high": 120,
                        "low": 90,
                    }
                ]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(
            quantity=2,
            stop_price=104,
            target_price=88,
            side="buy",
        ),
        order_history=[filled_parent_order(quantity=2, side="sell")],
    )

    review = reviews[0]
    assert review["peak_return_percent"] == 10
    assert review["proposed_protective_stop_price"] == 93
    assert review["would_tighten_protective_stop"] is True
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "RATCHET_STOP_DOWN"


def test_live_exit_shadow_uses_verified_broker_stop_instead_of_recorded_submission_stop():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission(stop_price=95)],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(stop_price=96),
        order_history=[filled_parent_order()],
    )

    review = reviews[0]
    assert review["attribution_validation_reason"] == "confirmed_bot_position_and_bracket"
    assert review["protective_stop_source"] == "BROKER_OPEN_STOP_LEG"
    assert review["current_protective_stop_price"] == 96
    assert review["proposed_protective_stop_price"] == 107
    assert review["would_raise_protective_stop"] is True
    assert review["trailing_action"] == "RATCHET_STOP_UP"


def test_live_exit_shadow_fails_closed_when_filled_parent_is_missing():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission()],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(),
        order_history=[],
    )

    review = reviews[0]
    assert review["attribution_validation_reason"] == "filled_parent_missing"
    assert review["entry_fill_timestamp"] is None
    assert review["entry_timestamp_source"] == "UNVERIFIED"
    assert review["protective_stop_source"] == "UNVERIFIED"
    assert review["proposed_protective_stop_price"] is None
    assert review["would_tighten_protective_stop"] is False
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "POSITION_ATTRIBUTION_UNVERIFIED"


def test_live_exit_shadow_fails_closed_when_filled_parent_is_ambiguous():
    parent = filled_parent_order()
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission()],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(),
        order_history=[parent, dict(parent)],
    )

    review = reviews[0]
    assert review["attribution_validation_reason"] == "filled_parent_ambiguous"
    assert review["entry_timestamp_source"] == "UNVERIFIED"
    assert review["protective_stop_source"] == "UNVERIFIED"
    assert review["proposed_protective_stop_price"] is None
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "POSITION_ATTRIBUTION_UNVERIFIED"


def test_live_exit_shadow_fails_closed_when_position_quantity_does_not_match_submission():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "2",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission(quantity=1)],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs(quantity=2),
        order_history=[filled_parent_order()],
    )

    review = reviews[0]
    assert review["attribution_validation_reason"] == "position_quantity_mismatch"
    assert review["protective_stop_source"] == "UNVERIFIED"
    assert review["proposed_protective_stop_price"] is None
    assert review["would_tighten_protective_stop"] is False
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "POSITION_ATTRIBUTION_UNVERIFIED"


def test_live_exit_shadow_fails_closed_when_broker_stop_leg_is_missing():
    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission()],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=broker_bracket_legs()[:1],
        order_history=[filled_parent_order()],
    )

    review = reviews[0]
    assert review["attribution_validation_reason"] == "broker_protection_missing"
    assert review["protective_stop_source"] == "UNVERIFIED"
    assert review["proposed_protective_stop_price"] is None
    assert review["would_tighten_protective_stop"] is False
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "BROKER_PROTECTION_UNVERIFIED"


def test_live_exit_shadow_fails_closed_when_broker_stop_leg_is_ambiguous():
    open_orders = broker_bracket_legs()
    open_orders.append({**open_orders[1], "order_id": "duplicate-stop"})

    reviews = module.build_live_exit_shadow_reviews(
        {
            "NVDA": {
                "qty": "1",
                "avg_entry_price": "100",
                "current_price": "108",
            }
        },
        {
            "submissions": [recorded_submission()],
            "price_paths": {
                "NVDA": [{"timestamp": "2026-09-30T15:00:00+00:00", "high": 110}]
            },
        },
        observed_at=datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
        settings=armed(),
        open_orders=open_orders,
        order_history=[filled_parent_order()],
    )

    review = reviews[0]
    assert review["attribution_validation_reason"] == "broker_protection_ambiguous"
    assert review["protective_stop_source"] == "UNVERIFIED"
    assert review["proposed_protective_stop_price"] is None
    assert review["would_tighten_protective_stop"] is False
    assert review["would_raise_protective_stop"] is False
    assert review["would_trigger_exit_now"] is False
    assert review["trailing_action"] == "BROKER_PROTECTION_UNVERIFIED"


def test_full_universe_replaces_short_allowlist_for_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    observed = {}

    def broad_scanner(records):
        observed["symbols"] = [row["symbol"] for row in records]
        return {"ranked_candidates": [{"symbol": "F", "latest_price": 12}]}

    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "true"},
        settings=armed(),
        broker=Broker(),
        scanner=broad_scanner,
        state_store=LiveStateStore(tmp_path / "live.json"),
    )
    assert result["status"] == "submitted"
    assert observed["symbols"] == ["AAL", "F"]


def test_sideways_market_routes_only_to_mean_reversion(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", mean_reversion_signal)
    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=Broker(),
        scanner=scanner,
        state_store=LiveStateStore(tmp_path / "live.json"),
    )
    assert result["status"] == "submitted"
    assert result["candidate"]["strategy"]["strategy_id"] == "stock_mean_reversion_v2"
    assert result["candidate"]["strategy"]["ensemble_route"] == "sideways"


def test_bearish_market_rejects_long_strategy_even_if_it_says_buy(tmp_path, monkeypatch):
    def bearish_signal(_candidate):
        return [{"strategy_id":"stock_trend_ensemble_v2", "signal":"BUY", "strategy_score":90,
            "confidence":90, "data_quality_status":"ok", "market_regime":"bear"}]

    monkeypatch.setattr(module, "evaluate_all_strategies", bearish_signal)
    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=Broker(),
        scanner=scanner,
        state_store=LiveStateStore(tmp_path / "live.json"),
    )
    assert result["status"] == "no_trade"
    assert not result["submitted"]


def test_missing_broker_history_fails_closed(tmp_path):
    broker = Broker()
    broker.get_order_history = None
    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=broker,
        scanner=scanner,
        state_store=LiveStateStore(tmp_path / "live.json"),
    )
    assert result["status"] == "blocked"
    assert "live_order_history_unavailable" in result["reasons"]


def test_highly_correlated_position_uses_shared_exposure_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    history = [{"date": f"2026-07-{day:02d}", "close": 100 + day} for day in range(1, 29)]
    history += [{"date": f"2026-08-{day:02d}", "close": 128 + day} for day in range(1, 29)]
    broker = Broker(
        positions={"AAL": {"market_value": 55, "current_price": 15}},
        orders=[
            {"symbol": "AAL", "side": "sell", "status": "new"},
            {"symbol": "AAL", "side": "sell", "status": "new"},
        ],
    )
    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=broker,
        scanner=lambda _records: {
            "ranked_candidates": [{"symbol": "F", "latest_price": 12}],
            "price_history_by_symbol": {"F": history, "AAL": history},
        },
        state_store=LiveStateStore(tmp_path / "live.json"),
    )
    assert result["status"] == "no_trade"
    assert result["reasons"] == ["no_eligible_risk_checked_candidate"]
