import pandas as pd

import replay_engine
import trade_journal
from trade_journal import read_journal, save_trade_decision


class DummyDataFrame(pd.DataFrame):
    @property
    def _constructor(self):
        return DummyDataFrame


def test_replay_is_deterministic(monkeypatch, tmp_path):
    prices = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=pd.date_range("2020-01-01", periods=5, freq="D"))

    monkeypatch.setattr(replay_engine, "download_price_data", lambda ticker, start_date, end_date: prices)
    monkeypatch.setattr(replay_engine, "generate_signal", lambda prices, short_window, long_window: "hold")
    monkeypatch.setattr(replay_engine, "create_simulated_trade_decision", lambda signal, portfolio_value, trade_value: "HOLD")
    monkeypatch.setattr(replay_engine, "save_trade_decision", lambda **kwargs: kwargs)

    engine = replay_engine.ReplayEngine(ticker="SPY", start_date="2020-01-01", end_date="2020-01-05")
    first = engine.replay()
    second = engine.replay()

    assert first == second


def test_compare_with_journal_counts_matches(monkeypatch, tmp_path):
    decisions = [{"timestamp": "2020-01-01", "signal": "hold", "decision": "HOLD"}]

    monkeypatch.setattr(replay_engine, "read_journal", lambda path=None: [{"timestamp": "2020-01-01", "signal": "hold", "decision": "HOLD"}])

    engine = replay_engine.ReplayEngine()
    comparison = engine.compare_with_journal(decisions)

    assert comparison["matched"] == 1
    assert comparison["total"] == 1


def test_replay_uses_only_prior_history(monkeypatch):
    prices = pd.DataFrame({"close": [100.0, 101.0]}, index=pd.date_range("2020-01-01", periods=2, freq="D"))
    observed = []

    monkeypatch.setattr(replay_engine, "download_price_data", lambda ticker, start_date, end_date: prices)
    monkeypatch.setattr(replay_engine, "generate_signal", lambda history, short_window, long_window: observed.append(len(history)) or "hold")
    monkeypatch.setattr(replay_engine, "create_simulated_trade_decision", lambda signal, portfolio_value, trade_value: "HOLD")
    monkeypatch.setattr(replay_engine, "save_trade_decision", lambda **kwargs: kwargs)

    engine = replay_engine.ReplayEngine(ticker="SPY", start_date="2020-01-01", end_date="2020-01-02")
    engine.replay(short_window=0, long_window=0)

    assert observed == [1]


def test_save_trade_decision_does_not_duplicate_existing_record(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_journal, "TRADING_MODE", "SIMULATION")
    path = tmp_path / "journal.csv"
    save_trade_decision("SPY", "buy", "BUY", 100.0, "reason", True, 10000.0, timestamp="2020-01-01", path=path)
    save_trade_decision("SPY", "buy", "BUY", 100.0, "reason", True, 10000.0, timestamp="2020-01-01", path=path)

    rows = read_journal(path)
    assert len(rows) == 1
