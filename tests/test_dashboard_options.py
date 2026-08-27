from dashboard_data import _fetch_options_dashboard_snapshot


def test_options_dashboard_combines_status_with_live_broker_rows(tmp_path, monkeypatch):
    status_path = tmp_path / "options-status.json"
    status_path.write_text(
        '{"cycle_status":"no_trade","underlying_count":10,"scanned_count":10,"signals":[{"symbol":"SPY","signal":"CALL"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPTIONS_TRADING_ENABLED", "true")
    account = {
        "positions": [
            {"symbol": "SPY260918C00600000", "asset_class": "us_option", "market_value": 600, "unrealized_pl": 25},
            {"symbol": "AAPL", "asset_class": "us_equity", "market_value": 1000, "unrealized_pl": 10},
        ]
    }
    orders = [
        {"symbol": "SPY260918C00600000", "asset_class": "us_option", "client_order_id": "qtb-option-1"},
        {"symbol": "AAPL", "asset_class": "us_equity", "client_order_id": "qtb-stock-1"},
    ]

    snapshot = _fetch_options_dashboard_snapshot(
        account,
        orders,
        status_path=status_path,
        closed_trade_summary={"closed_trades": 2, "net_pnl": -12.5},
    )

    assert snapshot["enabled"] is True
    assert snapshot["underlying_count"] == 10
    assert snapshot["open_position_count"] == 1
    assert snapshot["options_exposure"] == 600
    assert snapshot["unrealized_pl"] == 25
    assert snapshot["realized_pl"] == -12.5
    assert snapshot["closed_trade_count"] == 2
    assert len(snapshot["recent_orders"]) == 1
