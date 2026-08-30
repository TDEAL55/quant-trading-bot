from __future__ import annotations

import dashboard_app
from dashboard_data import (
    _apply_exact_stock_pnl_reconstruction,
    _fetch_paper_account_snapshot,
    _fetch_stock_order_strategy_metadata,
)


class _BrokerWithExactStockHistory:
    def __init__(self, mode):
        assert mode == "PAPER"

    def get_account(self):
        return {
            "status": "ACTIVE",
            "portfolio_value": 10010.0,
            "equity": 10010.0,
            "last_equity": 10000.0,
            "cash": 10010.0,
            "buying_power": 10010.0,
        }

    def get_positions(self):
        return {}

    def get_open_orders(self):
        return []

    def get_market_clock(self):
        return {"is_open": False, "source": "alpaca"}

    def get_order_history(self, limit=50):
        assert limit == 500
        return [
            {
                "order_id": "sell-1",
                "client_order_id": "qtb-exit-sell-1",
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "side": "sell",
                "status": "filled",
                "filled_quantity": 2,
                "average_fill_price": 105,
                "updated_at": "2026-08-02T14:00:00+00:00",
            },
            {
                "order_id": "buy-1",
                "client_order_id": "qtb-buy-1",
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "side": "buy",
                "status": "filled",
                "filled_quantity": 2,
                "average_fill_price": 100,
                "updated_at": "2026-08-01T14:00:00+00:00",
            },
        ]


class _BrokerWithIncompleteStockHistory(_BrokerWithExactStockHistory):
    def get_order_history(self, limit=50):
        assert limit == 500
        return [
            {
                "order_id": "sell-1",
                "client_order_id": "qtb-exit-sell-1",
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "side": "sell",
                "status": "filled",
                "filled_quantity": 2,
                "average_fill_price": 105,
                "updated_at": "2026-08-02T14:00:00+00:00",
            }
        ]


class _BrokerWithManualInterleaving(_BrokerWithExactStockHistory):
    def get_order_history(self, limit=50):
        rows = super().get_order_history(limit=limit)
        rows.append(
            {
                "order_id": "manual-buy",
                "client_order_id": "manual-buy",
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "side": "buy",
                "status": "filled",
                "filled_quantity": 1,
                "average_fill_price": 90,
                "updated_at": "2026-08-01T13:00:00+00:00",
            }
        )
        return rows


def test_broker_snapshot_exposes_exact_read_only_stock_reconstruction_and_order_pnl():
    snapshot = _fetch_paper_account_snapshot(
        _BrokerWithExactStockHistory,
        strategy_by_order_id={"buy-1": {"strategy_id": "trend_momentum_v2"}},
    )

    diagnostic = snapshot["stock_pnl_reconstruction"]
    assert diagnostic["is_exact"] is True
    assert diagnostic["realized_stock_pnl"] == 10.0
    assert diagnostic["database_mutated"] is False
    assert diagnostic["per_strategy"][0]["strategy_id"] == "trend_momentum_v2"
    sell_event = next(row for row in snapshot["recent_orders"] if row["broker_order_id"] == "sell-1")
    assert sell_event["realized_profit_loss"] == 10.0
    assert sell_event["realized_pl_exact"] is True
    assert sell_event["realized_pl_source"] == "alpaca_actual_filled_stock_orders"


def test_incomplete_broker_reconstruction_is_exposed_but_not_rendered_as_exact_order_pnl():
    snapshot = _fetch_paper_account_snapshot(_BrokerWithIncompleteStockHistory)

    diagnostic = snapshot["stock_pnl_reconstruction"]
    assert diagnostic["is_exact"] is False
    assert diagnostic["confidence"] == "insufficient"
    assert diagnostic["unmatched_close_count"] == 1
    sell_event = snapshot["recent_orders"][0]
    assert sell_event["realized_profit_loss"] is None
    assert sell_event["closed_trade"] is False


def test_partial_subtotal_never_replaces_dashboard_headline_pnl():
    snapshot = _fetch_paper_account_snapshot(_BrokerWithManualInterleaving)

    assert snapshot["stock_pnl_reconstruction"]["realized_stock_pnl"] == 10.0
    assert snapshot["stock_pnl_reconstruction"]["is_exact"] is False
    assert snapshot["realized_paper_pl"] is None
    assert snapshot["closed_trade_count"] == 0
    assert snapshot["closed_trade_source"] == "broker_stock_reconstruction_incomplete_use_durable_ledger"
    sell_event = next(row for row in snapshot["recent_orders"] if row["broker_order_id"] == "sell-1")
    assert sell_event["realized_profit_loss"] is None


def test_exact_nonempty_reconstruction_drives_in_memory_stock_headline_without_mutating_ledger():
    snapshot = _fetch_paper_account_snapshot(_BrokerWithExactStockHistory)
    durable_tuning = {
        "closed_trades_by_asset": {
            "stocks": {"closed_trades": 99, "net_pnl": -999.0},
            "crypto": {"closed_trades": 3, "net_pnl": -10.0},
            "options": {"closed_trades": 2, "net_pnl": -20.0},
        }
    }

    tuning = _apply_exact_stock_pnl_reconstruction(
        durable_tuning,
        snapshot["stock_pnl_reconstruction"],
    )
    payload = {
        "db_connected": True,
        "latest_run": {"bot_status": "healthy", "trading_mode": "PAPER"},
        "latest_success": {},
        "latest_signal": {"market_open": 0, "generated_signal": "HOLD"},
        "latest_account": snapshot,
        "paper_tuning": tuning,
        "recent_runs": [],
        "recent_orders": snapshot["recent_orders"],
        "portfolio_history": [],
        "signal_history": [],
        "order_count_by_day": [],
    }
    view = dashboard_app.build_dashboard_view_model(payload)

    assert tuning["stock_pnl_reconstruction_used"] is True
    assert tuning["closed_trades_by_asset"]["stocks"] == {
        "closed_trades": 1,
        "net_pnl": 10.0,
        "source": "alpaca_actual_filled_stock_orders",
        "confidence": "exact",
        "read_only_reconstruction": True,
    }
    assert view["bot_net_pl"] == 10.0
    assert view["stock_realized_pl"] == 10.0
    assert view["stock_closed_trade_count"] == 1
    assert durable_tuning["closed_trades_by_asset"]["stocks"]["net_pnl"] == -999.0


def test_incomplete_or_empty_reconstruction_keeps_durable_stock_ledger():
    durable_tuning = {
        "closed_trades_by_asset": {
            "stocks": {"closed_trades": 4, "net_pnl": 44.0},
        }
    }
    incomplete = _fetch_paper_account_snapshot(_BrokerWithIncompleteStockHistory)["stock_pnl_reconstruction"]
    result = _apply_exact_stock_pnl_reconstruction(durable_tuning, incomplete)

    assert result["stock_pnl_reconstruction_used"] is False
    assert result["closed_trades_by_asset"]["stocks"] == {"closed_trades": 4, "net_pnl": 44.0}


class _StrategyMetadataDb:
    def query_all(self, query, params=()):
        assert "JOIN paper_validation_runs" in query
        return [
            {
                "broker_order_id": "broker-1",
                "client_order_id": "qtb-client-1",
                "strategy_id": "mean_reversion_v2",
                "strategy_version": "v2",
            }
        ]


def test_strategy_metadata_is_read_only_and_indexed_by_broker_and_client_ids():
    metadata = _fetch_stock_order_strategy_metadata(_StrategyMetadataDb())

    assert metadata["broker-1"]["strategy_id"] == "mean_reversion_v2"
    assert metadata["qtb-client-1"]["strategy_version"] == "v2"
