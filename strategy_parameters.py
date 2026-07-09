class StrategyParameters:
    """Simple container for strategy settings used by the backtester."""

    def __init__(self, short_window=20, long_window=50, rsi_window=14):
        self.short_window = short_window
        self.long_window = long_window
        self.rsi_window = rsi_window

    def to_dict(self):
        return {
            "short_window": self.short_window,
            "long_window": self.long_window,
            "rsi_window": self.rsi_window,
        }
