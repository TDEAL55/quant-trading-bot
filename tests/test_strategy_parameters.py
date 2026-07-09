from strategy_parameters import StrategyParameters


def test_strategy_parameters_defaults():
    params = StrategyParameters()
    assert params.short_window == 20
    assert params.long_window == 50
    assert params.rsi_window == 14


def test_strategy_parameters_to_dict():
    params = StrategyParameters(short_window=10, long_window=30, rsi_window=7)
    assert params.to_dict() == {"short_window": 10, "long_window": 30, "rsi_window": 7}
