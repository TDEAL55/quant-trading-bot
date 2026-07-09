import copy
import os


class PaperAPIClient:
    """Paper-only API client that exposes read-only account data."""

    REQUIRED_ENV_VARS = (
        "PAPER_API_BASE_URL",
        "PAPER_API_USERNAME",
        "PAPER_API_PASSWORD",
        "PAPER_API_TOKEN",
    )

    def __init__(self, mode=None, credentials=None, account_status="paper_trading", buying_power=10000.0, positions=None):
        self.mode = (mode or os.getenv("TRADING_MODE", "SIMULATION")).upper()
        if self.mode == "LIVE":
            raise RuntimeError("LIVE mode is blocked for paper_api_client; use PAPER or SIMULATION only.")

        self.credentials = credentials or self._load_credentials_from_env()
        self._validate_credentials(self.credentials)

        self._account_status = account_status
        self._buying_power = float(buying_power)
        self._positions = positions or {}

    def _load_credentials_from_env(self):
        return {
            "base_url": os.getenv("PAPER_API_BASE_URL", ""),
            "username": os.getenv("PAPER_API_USERNAME", ""),
            "password": os.getenv("PAPER_API_PASSWORD", ""),
            "token": os.getenv("PAPER_API_TOKEN", ""),
        }

    def _validate_credentials(self, credentials):
        missing = []
        for env_name, credential_name in (
            ("PAPER_API_BASE_URL", "base_url"),
            ("PAPER_API_USERNAME", "username"),
            ("PAPER_API_PASSWORD", "password"),
            ("PAPER_API_TOKEN", "token"),
        ):
            if not credentials.get(credential_name):
                missing.append(env_name)

        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required paper API credentials: {joined}")

    def get_account_status(self):
        """Return a paper-trading account status string."""
        return self._account_status

    def get_buying_power(self):
        """Return the configured paper buying power."""
        return self._buying_power

    def get_positions(self):
        """Return a copy of the configured paper positions."""
        return copy.deepcopy(self._positions)

    def submit_order(self, *args, **kwargs):
        """Order submission is disabled in paper_api_client."""
        raise NotImplementedError("Order submission is disabled in paper_api_client.")


def create_paper_api_client(mode=None, credentials=None, account_status="paper_trading", buying_power=10000.0, positions=None):
    """Create a paper-only API client for read-only use."""
    return PaperAPIClient(
        mode=mode,
        credentials=credentials,
        account_status=account_status,
        buying_power=buying_power,
        positions=positions,
    )