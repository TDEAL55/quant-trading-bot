import copy
import os

from config import is_safe_mode
from paper_api_client import create_paper_api_client


class PaperBroker:
    """Paper-only broker adapter that never places real trades."""

    def __init__(self, mode=None, credentials=None):
        self.mode = (mode or os.getenv("TRADING_MODE", "SIMULATION")).upper()
        self.account_status = "paper_trading"
        self.buying_power = 10000.0
        self.positions = {
            "SPY": {"quantity": 0, "avg_price": 0.0},
            "AAPL": {"quantity": 0, "avg_price": 0.0},
        }
        self.paper_client = None
        self.credentials = credentials or {
            "base_url": os.getenv("PAPER_API_BASE_URL", ""),
            "username": os.getenv("PAPER_API_USERNAME", ""),
            "password": os.getenv("PAPER_API_PASSWORD", ""),
            "token": os.getenv("PAPER_API_TOKEN", ""),
        }

        if self.mode == "PAPER":
            self.paper_client = create_paper_api_client(mode=self.mode, credentials=self.credentials)
            self.credentials = self.paper_client.credentials

    def is_safe(self):
        """Return True only when live trading is disabled."""
        return is_safe_mode(self.mode)

    def _require_safe_action(self, action_name):
        if not self.is_safe():
            raise RuntimeError(f"{action_name} is disabled because live trading is not allowed")

    def get_account(self):
        """Return a mock account summary for paper trading."""
        self._require_safe_action("get_account")
        if self.mode == "PAPER":
            raise RuntimeError("get_account is disabled in PAPER mode; use get_account_status, get_buying_power, or get_positions")
        return {"mode": "paper", "status": self.account_status}

    def get_account_status(self):
        """Backward-compatible alias for account status access."""
        if self.paper_client is not None:
            return self.paper_client.get_account_status()
        return self.account_status

    def get_positions(self):
        """Return mock current positions."""
        self._require_safe_action("get_positions")
        if self.paper_client is not None:
            return self.paper_client.get_positions()
        return copy.deepcopy(self.positions)

    def get_buying_power(self):
        """Return mock buying power for paper trading."""
        self._require_safe_action("get_buying_power")
        if self.paper_client is not None:
            return self.paper_client.get_buying_power()
        return self.buying_power

    def submit_order(self, side, ticker, quantity, **kwargs):
        """Disabled by default to ensure no real order is ever sent."""
        self._require_safe_action("submit_order")
        raise NotImplementedError("Paper broker adapter does not submit live or paper orders")


def create_paper_broker(mode=None, credentials=None):
    """Create a paper-broker instance for simulation-only use."""
    return PaperBroker(mode=mode, credentials=credentials)
