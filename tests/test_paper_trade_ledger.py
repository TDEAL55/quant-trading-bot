from paper_trade_ledger import build_closed_trade_records, sync_closed_trade_ledger


def _filled(order_id, symbol, side, quantity, price, timestamp, asset_class="us_equity"):
    return {
        "order_id": order_id,
        "client_order_id": f"qtb-{order_id}",
        "symbol": symbol,
        "asset_class": asset_class,
        "side": side,
        "filled_quantity": quantity,
        "average_fill_price": price,
        "status": "filled",
        "updated_at": timestamp,
    }


def test_build_closed_trade_records_handles_long_short_crypto_and_options():
    orders = [
        _filled("stock-open", "AAPL", "buy", 2, 100, "2026-08-01T10:00:00Z"),
        _filled("stock-close", "AAPL", "sell", 2, 110, "2026-08-02T10:00:00Z"),
        _filled("short-open", "SBLK", "sell", 3, 20, "2026-08-03T10:00:00Z"),
        _filled("short-close", "SBLK", "buy", 3, 18, "2026-08-04T10:00:00Z"),
        _filled("crypto-open", "BTC/USD", "buy", 0.01, 100000, "2026-08-05T10:00:00Z", "crypto"),
        _filled("crypto-close", "BTC/USD", "sell", 0.01, 101000, "2026-08-06T10:00:00Z", "crypto"),
        _filled("option-open", "SPY260918C00600000", "buy", 1, 5, "2026-08-07T10:00:00Z", "us_option"),
        _filled("option-close", "SPY260918C00600000", "sell", 1, 6, "2026-08-08T10:00:00Z", "us_option"),
    ]

    records = build_closed_trade_records(list(reversed(orders)))

    assert len(records) == 4
    assert sum(row["net_pnl"] for row in records) == 136.0
    assert {row["symbol"] for row in records} == {"AAPL", "SBLK", "BTC/USD", "SPY260918C00600000"}
    assert all(row["trade_id"].startswith("alpaca-") for row in records)


def test_sync_closed_trade_ledger_is_idempotent():
    orders = [
        _filled("crypto-open", "BTC/USD", "buy", 0.01, 100000, "2026-08-05T10:00:00Z", "crypto"),
        _filled("crypto-close", "BTC/USD", "sell", 0.01, 101000, "2026-08-06T10:00:00Z", "crypto"),
    ]

    class Broker:
        def __init__(self, mode):
            assert mode == "PAPER"

        def get_order_history(self, limit):
            assert limit == 500
            return orders

    class Repository:
        saved = {}

        def __init__(self, database_url=None):
            self.db = type("DB", (), {"close": lambda self: None})()

        def list_closed_trades(self, limit=5000):
            return list(self.saved.values())

        def save_closed_trade(self, record):
            self.saved[record["trade_id"]] = dict(record)

    Repository.saved = {}
    first = sync_closed_trade_ledger(broker_factory=Broker, repository_factory=Repository)
    second = sync_closed_trade_ledger(broker_factory=Broker, repository_factory=Repository)

    assert first["new_records"] == 1
    assert second["new_records"] == 0
    assert len(Repository.saved) == 1


def test_sync_closed_trade_ledger_backfills_stock_exit_without_duplicating_direct_record():
    orders = [
        _filled("stock-open", "AAPL", "buy", 2, 100, "2026-08-01T10:00:00Z"),
        _filled("stock-close", "AAPL", "sell", 2, 110, "2026-08-02T10:00:00Z"),
        _filled("short-open", "SBLK", "sell", 3, 20, "2026-08-03T10:00:00Z"),
        _filled("short-close", "SBLK", "buy", 3, 18, "2026-08-04T10:00:00Z"),
    ]

    class Broker:
        def __init__(self, mode):
            assert mode == "PAPER"

        def get_order_history(self, limit):
            return orders

    class Repository:
        saved = {
            "direct-aapl": {
                "trade_id": "direct-aapl",
                "symbol": "AAPL",
                "quantity": 2.0,
                "exit_price": 110.0,
                "exit_timestamp": "2026-08-02T10:01:00Z",
            }
        }

        def __init__(self, database_url=None):
            self.db = type("DB", (), {"close": lambda self: None})()

        def list_closed_trades(self, limit=5000):
            return list(self.saved.values())

        def save_closed_trade(self, record):
            self.saved[record["trade_id"]] = dict(record)

    result = sync_closed_trade_ledger(broker_factory=Broker, repository_factory=Repository)

    assert result["new_records"] == 1
    assert {row["symbol"] for row in Repository.saved.values()} == {"AAPL", "SBLK"}
    assert sum(1 for row in Repository.saved.values() if row["symbol"] == "AAPL") == 1
