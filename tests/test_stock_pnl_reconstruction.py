from __future__ import annotations

from stock_pnl_reconstruction import reconstruct_stock_realized_pnl, realized_events_by_exit_order_id


def _fill(
    order_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    minute: int,
    *,
    client_order_id: str | None = None,
    asset_class: str = "us_equity",
    position_intent: str = "",
    status: str = "filled",
    strategy_id: str = "",
):
    return {
        "order_id": order_id,
        "client_order_id": client_order_id or f"qtb-{order_id}",
        "symbol": symbol,
        "asset_class": asset_class,
        "side": side,
        "filled_quantity": quantity,
        "average_fill_price": price,
        "status": status,
        "position_intent": position_intent,
        "strategy_id": strategy_id,
        "updated_at": f"2026-08-01T14:{minute:02d}:00+00:00",
    }


def test_fifo_scale_ins_and_partial_exits_use_actual_fills_and_entry_strategy():
    orders = [
        _fill("buy-1", "AAPL", "buy", 10, 100, 1),
        _fill("buy-2", "AAPL", "buy", 5, 110, 2),
        _fill("sell-1", "AAPL", "sell", 12, 120, 3, client_order_id="qtb-exit-sell-1"),
        _fill("sell-2", "AAPL", "sell", 3, 105, 4, client_order_id="qtb-exit-sell-2"),
    ]
    strategy_metadata = {
        "buy-1": {"strategy_id": "trend_momentum_v2"},
        "buy-2": {"strategy_id": "mean_reversion_v2"},
    }

    result = reconstruct_stock_realized_pnl(orders, strategy_by_order_id=strategy_metadata)

    assert result["is_exact"] is True
    assert result["confidence"] == "exact"
    assert result["realized_stock_pnl"] == 205.0
    assert result["closed_trade_count"] == 2
    assert result["matched_lot_count"] == 3
    assert result["open_inventory"] == []
    assert result["strategy_attribution_complete"] is True
    assert result["estimated_execution_costs"] > 0.0
    assert result["estimated_live_net_pnl"] < result["realized_stock_pnl"]
    assert {row["strategy_id"] for row in result["strategy_scoreboard"]} == {
        "mean_reversion_v2",
        "trend_momentum_v2",
    }
    assert {row["strategy_id"]: row["realized_pnl"] for row in result["per_strategy"]} == {
        "mean_reversion_v2": 5.0,
        "trend_momentum_v2": 200.0,
    }
    first_exit = realized_events_by_exit_order_id(result)["sell-1"]
    assert first_exit["quantity_closed"] == 12.0
    assert first_exit["realized_pnl"] == 220.0
    assert first_exit["entry_strategy_id"] == "multiple"


def test_short_covers_and_reversal_are_matched_chronologically():
    orders = [
        _fill("short-1", "SBLK", "sell", 10, 50, 1, strategy_id="bearish_trend_v2"),
        _fill("cover-1", "SBLK", "buy", 4, 40, 2, client_order_id="qtb-exit-cover-1"),
        _fill("reverse", "SBLK", "buy", 8, 55, 3, strategy_id="trend_momentum_v2"),
        _fill("long-exit", "SBLK", "sell", 2, 60, 4, client_order_id="qtb-exit-long"),
    ]

    result = reconstruct_stock_realized_pnl(orders)

    assert result["is_exact"] is True
    assert result["realized_stock_pnl"] == 20.0
    assert result["short_realized_pnl"] == 10.0
    assert result["long_realized_pnl"] == 10.0
    assert result["closed_trade_count"] == 3
    assert result["open_inventory"] == []
    assert {row["strategy_id"]: row["realized_pnl"] for row in result["per_strategy"]} == {
        "bearish_trend_v2": 10.0,
        "trend_momentum_v2": 10.0,
    }


def test_only_qtb_stock_fills_are_included():
    orders = [
        _fill("stock-buy", "MSFT", "buy", 1, 100, 1),
        _fill("stock-sell", "MSFT", "sell", 1, 110, 2, client_order_id="qtb-exit-stock"),
        _fill("manual", "MSFT", "buy", 1, 1, 3, client_order_id="manual-order"),
        _fill("crypto-buy", "BTC/USD", "buy", 0.1, 100000, 4, client_order_id="qtb-crypto-buy", asset_class="crypto"),
        _fill("crypto-sell", "BTC/USD", "sell", 0.1, 90000, 5, client_order_id="qtb-crypto-sell", asset_class="crypto"),
        _fill("option-buy", "AAPL260925C00320000", "buy", 1, 10, 6, client_order_id="qtb-option-buy", asset_class="us_option"),
        _fill("option-sell", "AAPL260925C00320000", "sell", 1, 1, 7, client_order_id="qtb-option-sell", asset_class="us_option"),
    ]

    result = reconstruct_stock_realized_pnl(orders)

    assert result["realized_stock_pnl"] == 10.0
    assert result["valid_stock_fill_count"] == 2
    assert result["ignored_order_counts"]["non_bot"] == 1
    assert result["manual_stock_fill_count"] == 1
    assert result["interfering_manual_stock_fill_count"] == 1
    assert result["unrelated_manual_stock_fill_count"] == 0
    assert result["interfering_manual_stock_symbols"] == ["MSFT"]
    assert result["ignored_order_counts"]["crypto"] == 2
    assert result["ignored_order_counts"]["options"] == 2
    assert [row["symbol"] for row in result["per_symbol"]] == ["MSFT"]
    assert result["is_exact"] is False
    assert "manual_stock_fills_were_excluded_so_inventory_interactions_are_not_provable" in result["confidence_reasons"]


def test_unrelated_manual_stock_symbol_does_not_downgrade_bot_pnl():
    orders = [
        _fill("stock-buy", "AAPL", "buy", 2, 100, 1),
        _fill("stock-sell", "AAPL", "sell", 2, 105, 2, client_order_id="qtb-exit-stock"),
        _fill("manual", "MSFT", "buy", 5, 300, 3, client_order_id="manual-order"),
    ]

    result = reconstruct_stock_realized_pnl(orders)

    assert result["realized_stock_pnl"] == 10.0
    assert result["is_exact"] is True
    assert result["confidence"] == "exact"
    assert result["manual_stock_fill_count"] == 1
    assert result["interfering_manual_stock_fill_count"] == 0
    assert result["unrelated_manual_stock_fill_count"] == 1
    assert result["interfering_manual_stock_symbols"] == []


def test_explicit_close_without_known_entry_is_incomplete_not_a_fake_short():
    orders = [
        _fill("orphan-exit", "JPM", "sell", 5, 210, 1, client_order_id="qtb-exit-orphan"),
    ]

    result = reconstruct_stock_realized_pnl(orders)

    assert result["is_exact"] is False
    assert result["history_complete"] is False
    assert result["confidence"] == "insufficient"
    assert result["realized_stock_pnl"] == 0.0
    assert result["unmatched_close_count"] == 1
    assert result["unmatched_close_quantity"] == 5.0
    assert result["open_inventory"] == []
    assert "one_or_more_close_orders_have_no_known_entry_cost_basis" in result["confidence_reasons"]


def test_partially_matched_close_is_labeled_partial_and_not_exact():
    orders = [
        _fill("known-buy", "JPM", "buy", 2, 100, 1),
        _fill("oversized-exit", "JPM", "sell", 5, 110, 2, client_order_id="qtb-exit-oversized"),
    ]

    result = reconstruct_stock_realized_pnl(orders)

    assert result["realized_stock_pnl"] == 20.0
    assert result["confidence"] == "partial"
    assert result["is_exact"] is False
    assert result["unmatched_close_quantity"] == 3.0
    assert result["realized_events"][0]["is_exact"] is False
    assert result["realized_events"][0]["confidence"] == "partial"


def test_history_limit_and_inferred_asset_class_downgrade_confidence():
    buy = _fill("buy", "AAPL", "buy", 1, 100, 1, asset_class="")
    sell = _fill("sell", "AAPL", "sell", 1, 110, 2, asset_class="", client_order_id="qtb-exit-sell")

    result = reconstruct_stock_realized_pnl(
        [buy, sell],
        history_limit=2,
    )

    assert result["realized_stock_pnl"] == 10.0
    assert result["is_exact"] is False
    assert result["history_limit_reached"] is True
    assert result["inferred_stock_classification_count"] == 2
    assert "broker_order_limit_reached_older_fills_may_be_missing" in result["confidence_reasons"]
    assert "one_or_more_stock_asset_classes_were_inferred" in result["confidence_reasons"]


def test_repeated_partial_and_final_order_snapshots_are_not_double_counted():
    partial = _fill("buy", "AAPL", "buy", 2, 100, 1, status="partially_filled")
    final = _fill("buy", "AAPL", "buy", 5, 102, 2, status="filled")
    sell = _fill("sell", "AAPL", "sell", 5, 110, 3, client_order_id="qtb-exit-sell")

    result = reconstruct_stock_realized_pnl([partial, final, sell])

    assert result["duplicate_snapshot_count"] == 1
    assert result["valid_stock_fill_count"] == 2
    assert result["realized_stock_pnl"] == 40.0


def test_bad_fill_rows_are_reported_instead_of_silently_guessed():
    missing_time = _fill("missing-time", "AAPL", "buy", 1, 100, 1)
    missing_time["updated_at"] = ""
    invalid_price = _fill("bad-price", "AAPL", "buy", 1, 0, 2)

    result = reconstruct_stock_realized_pnl([missing_time, invalid_price])

    assert result["is_exact"] is False
    assert result["confidence"] == "insufficient"
    assert result["missing_timestamp_count"] == 1
    assert result["invalid_fill_count"] == 1
    assert result["realized_stock_pnl"] == 0.0
    assert result["database_mutated"] is False
    assert result["read_only"] is True
