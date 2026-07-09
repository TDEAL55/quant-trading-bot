import pandas as pd

import simulation_runner


class DummyBroker:
    def __init__(self):
        self.calls = []

    def get_account(self):
        self.calls.append("account")
        return {"mode": "paper", "status": "paper_trading"}

    def get_buying_power(self):
        self.calls.append("buying_power")
        return 1234.0

    def get_positions(self):
        self.calls.append("positions")
        return {"SPY": {"quantity": 0, "avg_price": 0.0}}

    def submit_order(self, *args, **kwargs):
        raise AssertionError("submit_order should not be called")


def test_run_simulation_uses_broker_for_read_only_checks(monkeypatch):
    dummy_broker = DummyBroker()

    monkeypatch.setattr(
        simulation_runner,
        "download_price_data",
        lambda ticker, start_date, end_date: pd.DataFrame({"close": [100.0, 101.0]}),
    )
    monkeypatch.setattr(simulation_runner, "generate_signal", lambda prices, short_window, long_window: "hold")
    monkeypatch.setattr(simulation_runner, "create_simulated_trade_decision", lambda signal, cash, trade_value: "HOLD")
    monkeypatch.setattr(simulation_runner, "create_paper_broker", lambda mode=None, credentials=None: dummy_broker)
    monkeypatch.setattr(simulation_runner, "save_trade_decision", lambda **kwargs: kwargs)

    summary = simulation_runner.run_simulation()

    assert summary["decision"] == "HOLD"
    assert summary["buying_power"] == 1234.0
    assert summary["positions"]["SPY"]["quantity"] == 0
    assert dummy_broker.calls == ["account", "buying_power", "positions"]
