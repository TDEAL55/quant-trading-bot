class RiskManager:
    """Simple paper-trading risk checks for trade approval."""

    def __init__(self, max_position_size=0.25, max_daily_loss=500, daily_loss_limit=500):
        # Maximum position size controls how much of the portfolio can be allocated to one trade.
        self.max_position_size = max_position_size

        # Maximum loss limit is the largest allowed single-trade loss in dollars.
        self.max_daily_loss = max_daily_loss

        # Daily loss limit stops further trading once the day's loss exceeds the threshold.
        self.daily_loss_limit = daily_loss_limit
        self.daily_loss = 0.0
        self.trading_allowed = True

    def approve_trade(self, portfolio_value, trade_value, current_loss=0.0):
        """Return True when a trade passes basic risk checks, otherwise False."""
        # Reject trades that try to exceed the maximum portfolio allocation.
        if trade_value > portfolio_value * self.max_position_size:
            return False

        # Reject trades that would exceed the maximum loss limit for a single trade.
        if current_loss > self.max_daily_loss:
            return False

        # Stop trading for the day if the running loss limit has been reached.
        if self.daily_loss >= self.daily_loss_limit:
            self.trading_allowed = False
            return False

        return True

    def record_loss(self, loss_amount):
        """Update running daily loss and disable trading if the loss limit is hit."""
        self.daily_loss += loss_amount
        if self.daily_loss >= self.daily_loss_limit:
            self.trading_allowed = False
