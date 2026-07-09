import pandas as pd

from strategy import generate_signal


def test_generate_signal_buy():
    prices = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    assert generate_signal(prices, 3, 5) in {"buy", "hold"}


def test_generate_signal_hold():
    prices = pd.Series([10, 10, 10, 10, 10, 10, 10, 10, 10, 10])
    assert generate_signal(prices, 3, 5) == "hold"
