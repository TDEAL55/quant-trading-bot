from datetime import datetime, timezone

from paper_account_reconciliation import run_paper_account_reconciliation


class _Broker:
    def __init__(self, mode):
        assert mode == "PAPER"

    def get_account(self):
        return {"status": "ACTIVE"}

    def get_positions(self):
        return {"BTC/USD": {"quantity": 0.1, "asset_class": "crypto"}}

    def get_open_orders(self):
        return []


def test_daily_reconciliation_matches_and_syncs_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    calls = []

    result = run_paper_account_reconciliation(
        broker_factory=_Broker,
        ledger_sync=lambda **kwargs: calls.append(kwargs) or {"closed_trade_count": 4, "new_records": 1},
        now=datetime(2026, 8, 25, tzinfo=timezone.utc),
        status_path=tmp_path / "reconciliation.json",
    )

    assert result["status"] == "matched"
    assert result["closed_trade_count"] == 4
    assert result["new_closed_trades_recorded"] == 1
    assert (tmp_path / "reconciliation.json").is_file()
    assert len(calls) == 1


def test_daily_reconciliation_reports_only_actionable_mismatches(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")

    class Broker(_Broker):
        def get_positions(self):
            return {"BTC/USD": {"quantity": -0.1, "asset_class": "crypto"}}

        def get_open_orders(self):
            return [
                {"client_order_id": "qtb-a", "symbol": "BTC/USD", "side": "buy", "status": "accepted", "submitted_at": "2026-08-23T00:00:00Z"},
                {"client_order_id": "qtb-b", "symbol": "BTC/USD", "side": "buy", "status": "accepted", "submitted_at": "2026-08-25T00:00:00Z"},
            ]

    result = run_paper_account_reconciliation(
        broker_factory=Broker,
        ledger_sync=lambda **kwargs: {},
        now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
        status_path=tmp_path / "reconciliation.json",
    )

    assert result["status"] == "mismatch"
    assert "duplicate_open_order:BTC/USD:buy" in result["warnings"]
    assert "stale_open_order:BTC/USD:buy" in result["warnings"]
    assert "unexpected_short_crypto:BTC/USD" in result["warnings"]
