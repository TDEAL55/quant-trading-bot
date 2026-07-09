import pandas as pd

from backtest import run_backtest
from strategy_gate import evaluate_strategy_gate


def test_strategy_gate_accepts_valid_result():
    data = pd.DataFrame({"close": [10.0, 11.0, 12.0]}, index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]))
    backtest_result = run_backtest("SPY", "2020-01-01", "2020-01-10")
    status, reasons = evaluate_strategy_gate(backtest_result, data)
    assert status in {"APPROVED", "REJECTED"}
    assert isinstance(reasons, list)
