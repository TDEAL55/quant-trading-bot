import os

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
except ImportError:  # pragma: no cover - covered indirectly through runtime checks
    TradingClient = None
    AssetClass = None
    AssetStatus = None
    GetAssetsRequest = None


class AlpacaClient:
    """Paper-only Alpaca client that allows read-only account access."""

    REQUIRED_ENV_VARS = ("ALPACA_API_KEY", "ALPACA_API_SECRET")

    def __init__(self, mode=None, trading_client=None):
        self.mode = (mode or os.getenv("TRADING_MODE", "SIMULATION")).upper()
        if self.mode == "LIVE":
            raise RuntimeError("LIVE mode is blocked for alpaca_client; use PAPER or SIMULATION only.")

        self.credentials = self._load_credentials_from_env()
        self._validate_credentials(self.credentials)
        self._trading_client = trading_client or self._create_trading_client()

    def _load_credentials_from_env(self):
        return {
            "api_key": os.getenv("ALPACA_API_KEY", ""),
            "api_secret": os.getenv("ALPACA_API_SECRET", ""),
        }

    def _validate_credentials(self, credentials):
        missing = []
        if not credentials.get("api_key"):
            missing.append("ALPACA_API_KEY")
        if not credentials.get("api_secret"):
            missing.append("ALPACA_API_SECRET")

        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required Alpaca credentials: {joined}")

    def _create_trading_client(self):
        if TradingClient is None:
            raise RuntimeError("alpaca-py is required. Install alpaca-py before using alpaca_client.")
        return TradingClient(
            api_key=self.credentials["api_key"],
            secret_key=self.credentials["api_secret"],
            paper=True,
        )

    def _require_safe_action(self, action_name):
        if self.mode == "LIVE":
            raise RuntimeError(f"{action_name} is disabled because live trading is not allowed")

    def get_account_status(self):
        """Return account status from Alpaca paper trading."""
        self._require_safe_action("get_account_status")
        account = self._trading_client.get_account()
        return str(getattr(account, "status", "unknown"))

    def get_buying_power(self):
        """Return buying power from Alpaca paper trading."""
        self._require_safe_action("get_buying_power")
        account = self._trading_client.get_account()
        buying_power = getattr(account, "buying_power", 0.0)
        try:
            return float(buying_power)
        except (TypeError, ValueError):
            return buying_power

    def get_current_positions(self):
        """Return a simplified list of current positions from Alpaca paper trading."""
        self._require_safe_action("get_current_positions")
        positions = self._trading_client.get_all_positions()
        return [
            {
                "symbol": str(getattr(position, "symbol", "")),
                "qty": str(getattr(position, "qty", "0")),
                "avg_entry_price": str(getattr(position, "avg_entry_price", "0")),
                "market_value": str(getattr(position, "market_value", "0")),
            }
            for position in positions
        ]

    def get_positions(self):
        """Backward-compatible alias for current positions."""
        return self.get_current_positions()

    def get_assets(self):
        """Return assets visible to the connected paper account."""
        self._require_safe_action("get_assets")
        getter = getattr(self._trading_client, "get_all_assets", None)
        if getter is None:
            raise RuntimeError("Trading client does not support assets API")

        # Prefer server-side filtering when available.
        if GetAssetsRequest is not None and AssetClass is not None and AssetStatus is not None:
            try:
                req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
                rows = getter(filter=req)
                if rows is not None:
                    return list(rows)
            except TypeError:
                pass
            except Exception:
                pass

        # Fallback for mocked clients or older SDK behavior.
        try:
            rows = getter()
        except TypeError:
            rows = getter(None)
        return list(rows or [])

    def submit_order(self, *args, **kwargs):
        """Order submission remains blocked for safety."""
        raise NotImplementedError("Order submission is disabled in alpaca_client.")


def create_alpaca_client(mode=None, trading_client=None):
    """Create a paper-only Alpaca client."""
    return AlpacaClient(mode=mode, trading_client=trading_client)