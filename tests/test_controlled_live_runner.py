from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import controlled_live_runner as module
from controlled_live_runner import LiveStateStore, run_controlled_live_cycle
from live_risk_policy import LiveRiskSettings
from shadow_exit_trial_ledger import ShadowExitTrialLedger


class Broker:
    def __init__(self, positions=None, orders=None, order_history=None):
        self.positions = positions or {}
        self.orders = orders or []
        self.order_history = order_history or []
        self.submitted = []
        self.ratchets = []
    def get_account(self):
        return {"status":"ACTIVE", "equity":300, "last_equity":300, "day_pl":0, "cash":300,
            "multiplier":1, "trading_blocked":False, "account_blocked":False}
    def get_positions(self): return self.positions
    def get_open_orders(self): return self.orders
    def get_order_history(self, limit=500): return self.order_history[:limit]
    def get_market_clock(self): return {"is_open": True}
    def submit_bracket_entry(self, **kwargs):
        self.submitted.append(kwargs)
        return {"order_id":"oid", "client_order_id":kwargs["client_order_id"], "symbol":kwargs["symbol"],
            "requested_quantity":kwargs["quantity"], "reference_price":kwargs["reference_price"],
            "stop_price":kwargs["stop_price"], "target_price":kwargs["target_price"], "status":"accepted"}
    def tighten_protective_stop(self, **kwargs):
        self.ratchets.append(kwargs)
        return {
            "order_id": "replacement-stop",
            "status": "new",
            "stop_price": kwargs["proposed_stop_price"],
        }
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


def verified_shadow_review(
    *,
    current_price=108,
    proposed_stop=107,
    peak_return_percent=10,
    would_trigger=False,
):
    return {
        "symbol": "NVDA",
        "mode": "SHADOW_ONLY",
        "actionable": False,
        "orders_changed": False,
        "parent_order_id": "parent-order",
        "parent_client_order_id": "qtb-live-parent",
        "filled_parent_verified": True,
        "bot_position_attributed": True,
        "broker_bracket_verified": True,
        "entry_timestamp_source": "BROKER_FILLED_PARENT",
        "position_side": "LONG",
        "entry_price": 100,
        "entry_quantity": 1,
        "current_price": current_price,
        "strategy_id": "stock_trend_pullback_v3",
        "profile_id": "trend_pullback_atr_v3",
        "return_percent": current_price - 100,
        "peak_return_percent": peak_return_percent,
        "trailing_activation_percent": 6,
        "trailing_distance_percent": 3,
        "trailing_activation_reached": True,
        "proposed_protective_stop_price": proposed_stop,
        "would_tighten_protective_stop": True,
        "would_raise_protective_stop": True,
        "would_trigger_exit_now": would_trigger,
        "trailing_action": "EXIT_THRESHOLD_CROSSED" if would_trigger else "RATCHET_STOP_UP",
    }


def verified_shadow_cycle_dependencies(tmp_path):
    store = LiveStateStore(tmp_path / "live.json")
    store._save({
        "orders_by_date": {},
        "submissions": [recorded_submission(quantity=0.5)],
        "price_paths": {
            "NVDA": [
                {"timestamp": "2026-09-30T15:00:00+00:00", "high": 110, "low": 104}
            ]
        },
        "equity_history": [],
    })
    broker = Broker(
        positions={
            "NVDA": {
                "qty": "0.5",
                "avg_entry_price": "100",
                "current_price": "108",
                "market_value": "54",
            }
        },
        orders=broker_bracket_legs(quantity=0.5),
        order_history=[filled_parent_order(quantity=0.5)],
    )
    now = lambda: datetime(2026, 10, 1, 15, tzinfo=timezone.utc)
    return store, broker, now


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
        "shadow_exit_trial_summary": {
            "status": "available",
            "trial_count": 1,
            "actionable": False,
            "orders_changed": False,
        },
    })
    saved = store.load()["last_cycle"]
    assert saved["candidate_funnel"]["rejection_counts"] == {"too_small": 1}
    assert saved["live_exit_shadow"][0]["mode"] == "SHADOW_ONLY"
    assert saved["shadow_exit_trial_summary"]["trial_count"] == 1


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


def test_verified_live_stop_ratchet_respects_emergency_disable_and_acts_by_default():
    broker = Broker()
    review = verified_shadow_review()
    review.update(
        {
            "protective_stop_source": "BROKER_OPEN_STOP_LEG",
            "protective_stop_order_id": "parent-order-stop",
            "current_protective_stop_price": 95,
        }
    )

    disabled = module.apply_verified_live_stop_ratchets(
        broker,
        [review],
        settings=replace(armed(), trailing_stop_ratchet_enabled=False),
        market_is_open=True,
    )
    assert disabled == []
    assert broker.ratchets == []

    actions = module.apply_verified_live_stop_ratchets(
        broker,
        [review],
        settings=armed(),
        market_is_open=True,
    )
    assert actions[0]["status"] == "ratcheted"
    assert actions[0]["orders_changed"] is True
    assert broker.ratchets == [
        {
            "symbol": "NVDA",
            "stop_order_id": "parent-order-stop",
            "current_stop_price": 95.0,
            "proposed_stop_price": 107.0,
            "current_market_price": 108.0,
        }
    ]


def test_live_stop_ratchet_runs_even_when_new_entries_are_blocked(tmp_path):
    store, broker, now = verified_shadow_cycle_dependencies(tmp_path)
    broker.get_account = lambda: {
        "status": "ACTIVE",
        "equity": 300,
        "last_equity": 303,
        "day_pl": -3,
        "cash": 246,
        "multiplier": 1,
        "trading_blocked": False,
        "account_blocked": False,
    }

    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=broker,
        scanner=scanner,
        state_store=store,
        shadow_trial_ledger=ShadowExitTrialLedger(tmp_path / "blocked-entry-shadow.json"),
        now_provider=now,
    )

    assert result["status"] == "blocked"
    assert "daily_loss_stop_active" in result["reasons"]
    assert result["submitted"] is False
    assert result["live_exit_actions"][0]["status"] == "ratcheted"
    assert broker.ratchets == [
        {
            "symbol": "NVDA",
            "stop_order_id": "parent-order-stop",
            "current_stop_price": 95.0,
            "proposed_stop_price": 107.0,
            "current_market_price": 108.0,
        }
    ]


def test_live_stop_ratchet_runs_before_stock_universe_failure(tmp_path):
    store, broker, now = verified_shadow_cycle_dependencies(tmp_path)

    def fail_universe(*, include_etfs=False):
        del include_etfs
        raise ValueError("sdk asset validation failed")

    broker.get_tradable_stock_assets = fail_universe

    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "true"},
        settings=armed(),
        broker=broker,
        scanner=scanner,
        state_store=store,
        shadow_trial_ledger=ShadowExitTrialLedger(tmp_path / "universe-failure-shadow.json"),
        now_provider=now,
    )

    assert result["status"] == "blocked"
    assert result["reasons"] == ["stock_universe_unavailable_ValueError"]
    assert result["submitted"] is False
    assert result["live_exit_actions"][0]["status"] == "ratcheted"
    assert broker.ratchets == [
        {
            "symbol": "NVDA",
            "stop_order_id": "parent-order-stop",
            "current_stop_price": 95.0,
            "proposed_stop_price": 107.0,
            "current_market_price": 108.0,
        }
    ]


def test_live_stop_ratchet_never_runs_for_unverified_position():
    broker = Broker()
    review = verified_shadow_review()
    review.update(
        {
            "bot_position_attributed": False,
            "protective_stop_source": "UNVERIFIED",
            "protective_stop_order_id": None,
            "current_protective_stop_price": 95,
        }
    )

    actions = module.apply_verified_live_stop_ratchets(
        broker,
        [review],
        settings=replace(armed(), trailing_stop_ratchet_enabled=True),
        market_is_open=True,
    )

    assert actions == []
    assert broker.ratchets == []


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


def test_verified_shadow_rows_update_injected_trial_ledger_and_expose_summary(tmp_path):
    store, broker, now = verified_shadow_cycle_dependencies(tmp_path)
    ledger = ShadowExitTrialLedger(tmp_path / "shadow-trials.json")

    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=broker,
        scanner=scanner,
        state_store=store,
        shadow_trial_ledger=ledger,
        now_provider=now,
    )

    summary = result["shadow_exit_trial_summary"]
    assert summary["trial_count"] == 1
    assert summary["open_trial_count"] == 1
    assert summary["activated_count"] == 1
    assert summary["triggered_count"] == 0
    assert summary["verified_observation_count"] == 1
    assert summary["unverified_observation_count"] == 0
    assert summary["coverage_percent"] == 100
    assert summary["by_strategy"]["stock_trend_pullback_v3"]["trial_count"] == 1
    assert summary["actionable"] is False
    assert summary["orders_changed"] is False

    review = result["live_exit_shadow"][0]
    assert review["actionable"] is False
    assert review["orders_changed"] is False
    trial = ledger.get_trial("parent-order", "qtb-live-parent")
    assert trial is not None
    assert trial["symbol"] == "NVDA"
    assert trial["shadow_stop_price"] == 107
    assert trial["reconciliation_status"] == "OPEN"


def test_default_shadow_trial_ledger_path_is_used_by_live_cycle(tmp_path):
    store, broker, now = verified_shadow_cycle_dependencies(tmp_path)
    ledger_path = tmp_path / "configured-shadow-trials.json"

    result = run_controlled_live_cycle(
        environ={
            "TRADING_MODE": "LIVE",
            "LIVE_FULL_STOCK_UNIVERSE": "false",
            "SHADOW_EXIT_TRIAL_LEDGER_PATH": str(ledger_path),
        },
        settings=armed(),
        broker=broker,
        scanner=scanner,
        state_store=store,
        now_provider=now,
    )

    assert ledger_path.exists()
    assert result["shadow_exit_trial_summary"]["trial_count"] == 1
    assert ShadowExitTrialLedger(ledger_path).summary()["verified_observation_count"] == 1


def test_shadow_trial_ratchets_are_monotonic_and_first_trigger_is_frozen(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow-trials.json")
    order_history = [filled_parent_order()]

    def update(review, *, observed_at):
        return ledger.update(
            [review],
            observed_at=observed_at,
            positions={
                "NVDA": {
                    "qty": "1",
                    "avg_entry_price": "100",
                    "current_price": str(review["current_price"]),
                }
            },
            order_history=order_history,
        )

    update(
        verified_shadow_review(current_price=108, proposed_stop=107, peak_return_percent=10),
        observed_at="2026-10-01T15:00:00+00:00",
    )
    update(
        verified_shadow_review(current_price=111, proposed_stop=109, peak_return_percent=12),
        observed_at="2026-10-01T15:01:00+00:00",
    )
    update(
        verified_shadow_review(current_price=110.5, proposed_stop=108, peak_return_percent=11),
        observed_at="2026-10-01T15:02:00+00:00",
    )
    assert ledger.get_trial("parent-order", "qtb-live-parent")["shadow_stop_price"] == 109

    update(
        verified_shadow_review(
            current_price=108.5,
            proposed_stop=109,
            peak_return_percent=12,
            would_trigger=True,
        ),
        observed_at="2026-10-01T15:03:00+00:00",
    )
    first_trigger = dict(
        ledger.get_trial("parent-order", "qtb-live-parent")["first_hypothetical_trigger"]
    )
    assert first_trigger["triggered_at"] == "2026-10-01T15:03:00+00:00"
    assert first_trigger["shadow_stop_price"] == 109
    assert first_trigger["simulated_exit_price"] == 108.5
    assert (
        first_trigger["simulated_fill_model"]
        == "CONSERVATIVE_STOP_OR_OBSERVED_TRIGGER_PRICE"
    )

    summary = update(
        verified_shadow_review(
            current_price=113,
            proposed_stop=110,
            peak_return_percent=13,
            would_trigger=True,
        ),
        observed_at="2026-10-01T15:04:00+00:00",
    )
    frozen = ledger.get_trial("parent-order", "qtb-live-parent")["first_hypothetical_trigger"]
    assert frozen == first_trigger
    assert summary["trial_count"] == 1
    assert summary["activated_count"] == 1
    assert summary["triggered_count"] == 1
    assert summary["verified_observation_count"] == 5
    assert summary["coverage_percent"] == 100


def test_shadow_trial_ledger_failure_does_not_block_live_order_submission(tmp_path, monkeypatch):
    class FailingLedger:
        def update(self, reviews, *, observed_at, positions, order_history):
            raise RuntimeError("shadow ledger disk unavailable")

    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    broker = Broker()
    result = run_controlled_live_cycle(
        environ={"TRADING_MODE": "LIVE", "LIVE_FULL_STOCK_UNIVERSE": "false"},
        settings=armed(),
        broker=broker,
        scanner=scanner,
        state_store=LiveStateStore(tmp_path / "live.json"),
        shadow_trial_ledger=FailingLedger(),
        now_provider=lambda: datetime(2026, 10, 1, 15, tzinfo=timezone.utc),
    )

    assert result["status"] == "submitted"
    assert result["submitted"] is True
    assert len(broker.submitted) == 1
    assert result["shadow_exit_trial_summary"]["status"] == "unavailable"
    assert result["shadow_exit_trial_summary"]["error_type"] == "RuntimeError"
    assert result["shadow_exit_trial_summary"]["actionable"] is False
    assert result["shadow_exit_trial_summary"]["orders_changed"] is False


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
