import json

from dashboard_app import PAGE_OPTIONS, PRIMARY_PAGE_OPTIONS
from dashboard_data import _fetch_crypto_dashboard_snapshot


def test_crypto_is_an_essential_dashboard_page():
    assert "Crypto" in PRIMARY_PAGE_OPTIONS
    assert "Crypto" in PAGE_OPTIONS


def test_crypto_dashboard_combines_cycle_status_with_live_broker_positions(tmp_path, monkeypatch):
    status_path = tmp_path / "crypto-status.json"
    status_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "cycle_status": "no_trade",
                "updated_at": "2026-08-24T22:00:00+00:00",
                "universe_count": 12,
                "scanned_count": 12,
                "signals": [{"symbol": "BTC/USD", "signal": "HOLD", "score": 55}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CRYPTO_TRADING_ENABLED", "true")
    account = {
        "positions": [
            {
                "symbol": "BTCUSD",
                "asset_class": "crypto",
                "quantity": 0.1,
                "market_value": 6000,
                "unrealized_pl": 125,
            },
            {"symbol": "AAPL", "asset_class": "us_equity", "quantity": 2, "market_value": 400},
        ]
    }
    orders = [
        {"symbol": "BTC/USD", "client_order_id": "qtb-crypto-abc", "status": "filled"},
        {"symbol": "AAPL", "client_order_id": "qtb-stock-abc", "status": "filled"},
    ]

    result = _fetch_crypto_dashboard_snapshot(account, orders, status_path=status_path)

    assert result["enabled"] is True
    assert result["universe_count"] == 12
    assert result["open_position_count"] == 1
    assert result["crypto_exposure"] == 6000
    assert result["unrealized_pl"] == 125
    assert [row["symbol"] for row in result["recent_orders"]] == ["BTC/USD"]

