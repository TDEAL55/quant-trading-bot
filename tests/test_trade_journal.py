import os

import trade_journal
from trade_journal import read_journal, save_trade_decision


def test_save_trade_decision_writes_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_journal, "TRADING_MODE", "SIMULATION")
    path = tmp_path / "journal.csv"
    record = save_trade_decision(
        symbol="SPY",
        signal="buy",
        decision="BUY",
        price=100.0,
        reason="signal crossover",
        risk_result=True,
        portfolio_value=10000.0,
        timestamp="2026-07-06T00:00:00",
        path=path,
    )

    assert record["decision"] == "BUY"
    assert (tmp_path / "journal.csv").exists()

    rows = read_journal(path)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SPY"


def test_read_journal_returns_empty_list_for_missing_file(tmp_path):
    missing_path = tmp_path / "missing.csv"
    assert read_journal(missing_path) == []
