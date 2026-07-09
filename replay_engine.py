from pathlib import Path

import pandas as pd

from market_data import download_price_data
from strategy import generate_signal
from trading_engine import create_simulated_trade_decision
from trade_journal import read_journal, save_trade_decision


class ReplayEngine:
    """Replay historical market data as if the strategy were running live."""

    def __init__(self, ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
        self.ticker = ticker
        self.start_date = start_date
        self.end_date = end_date

    def replay(self, short_window=20, long_window=50, portfolio_value=10000.0, trade_value=1000.0):
        """Replay historical data and record every simulated decision."""
        prices = download_price_data(self.ticker, self.start_date, self.end_date)
        prices = prices.dropna()
        decisions = []

        for index, row in prices.iterrows():
            history = prices["close"].loc[:index].iloc[:-1]
            if len(history) < max(short_window, long_window) + 1:
                signal = "hold"
            else:
                signal = generate_signal(history, short_window, long_window)

            decision = create_simulated_trade_decision(signal, portfolio_value, trade_value)
            record = {
                "timestamp": str(index),
                "symbol": self.ticker,
                "signal": signal,
                "decision": decision,
                "price": float(row["close"]),
                "reason": "historical replay",
                "risk_result": True,
                "portfolio_value": portfolio_value,
            }
            decisions.append(record)
            save_trade_decision(
                symbol=self.ticker,
                signal=signal,
                decision=decision,
                price=float(row["close"]),
                reason="historical replay",
                risk_result=True,
                portfolio_value=portfolio_value,
                timestamp=str(index),
            )

        return decisions

    def compare_with_journal(self, decisions, journal_path=None):
        """Compare replay decisions with the local trade journal entries."""
        journal_entries = read_journal(journal_path)
        if not journal_entries:
            return {"matched": 0, "total": len(decisions), "journal_entries": 0}

        replay_rows = [(d["timestamp"], d["decision"], d["signal"]) for d in decisions]
        journal_rows = [(entry["timestamp"], entry["decision"], entry["signal"]) for entry in journal_entries]
        matched = sum(1 for row in replay_rows if row in journal_rows)
        return {
            "matched": matched,
            "total": len(decisions),
            "journal_entries": len(journal_entries),
        }


def run_replay(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
    """Convenience wrapper to run a historical replay and return the comparison summary."""
    engine = ReplayEngine(ticker=ticker, start_date=start_date, end_date=end_date)
    decisions = engine.replay()
    return {"decisions": decisions, "comparison": engine.compare_with_journal(decisions)}
