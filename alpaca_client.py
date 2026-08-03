import os
import inspect
import time
from typing import Any


ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"

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
        base_url = self._resolved_paper_base_url()
        kwargs: dict[str, Any] = {
            "api_key": self.credentials["api_key"],
            "secret_key": self.credentials["api_secret"],
            "paper": True,
        }
        try:
            params = inspect.signature(TradingClient).parameters
            if "url_override" in params:
                kwargs["url_override"] = base_url
            elif "base_url" in params:
                kwargs["base_url"] = base_url
        except Exception:
            pass
        return TradingClient(**kwargs)

    def _resolved_paper_base_url(self) -> str:
        configured = str(os.getenv("ALPACA_PAPER_BASE_URL") or os.getenv("APCA_API_BASE_URL") or ALPACA_PAPER_ENDPOINT).strip()
        normalized = configured.rstrip("/").lower()
        expected = ALPACA_PAPER_ENDPOINT.rstrip("/").lower()
        if normalized != expected:
            raise RuntimeError("ALPACA_PAPER_BASE_URL must be https://paper-api.alpaca.markets")
        return configured

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

    @staticmethod
    def _enum_or_string(value: Any) -> str:
        if value is None:
            return ""
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str) and enum_value.strip():
            return enum_value.strip()
        enum_name = getattr(value, "name", None)
        if isinstance(enum_name, str) and enum_name.strip():
            return enum_name.strip()
        text = str(value).strip()
        if "." in text:
            text = text.split(".")[-1]
        return text

    @classmethod
    def normalize_asset_class(cls, value: Any) -> str:
        return cls._enum_or_string(value).upper().replace("-", "_")

    @classmethod
    def normalize_status(cls, value: Any) -> str:
        return cls._enum_or_string(value).upper().replace("-", "_")

    @classmethod
    def normalize_exchange(cls, value: Any) -> str:
        return cls._enum_or_string(value).upper().replace("-", "_")

    @classmethod
    def normalize_tradable(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _asset_value(cls, asset: Any, key: str, default: Any = None) -> Any:
        if isinstance(asset, dict):
            return asset.get(key, default)
        return getattr(asset, key, default)

    @classmethod
    def _is_active_tradable_us_equity(cls, asset: Any) -> bool:
        asset_class = cls.normalize_asset_class(cls._asset_value(asset, "asset_class", ""))
        status = cls.normalize_status(cls._asset_value(asset, "status", ""))
        tradable = cls.normalize_tradable(cls._asset_value(asset, "tradable", False))
        return asset_class == "US_EQUITY" and status == "ACTIVE" and tradable

    def _safe_get_all_assets(self, getter, *, filtered_request: Any | None = None) -> list[Any]:
        if filtered_request is not None:
            try:
                rows = getter(filter=filtered_request)
                return list(rows or [])
            except TypeError:
                rows = getter(filtered_request)
                return list(rows or [])
        try:
            rows = getter()
            return list(rows or [])
        except TypeError:
            rows = getter(None)
            return list(rows or [])

    def get_assets_diagnostics(self) -> dict[str, Any]:
        self._require_safe_action("get_assets")
        getter = getattr(self._trading_client, "get_all_assets", None)
        if getter is None:
            raise RuntimeError("Trading client does not support assets API")

        started = time.perf_counter()
        unfiltered_rows = self._safe_get_all_assets(getter)

        filtered_rows: list[Any] = []
        filtered_exception_type = ""
        filter_requests: list[Any] = []
        if GetAssetsRequest is not None:
            if AssetClass is not None and AssetStatus is not None:
                try:
                    filter_requests.append(GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
                except Exception:
                    pass
            try:
                filter_requests.append(GetAssetsRequest(asset_class="us_equity", status="active"))
            except Exception:
                pass

        for req in filter_requests:
            try:
                filtered_rows = self._safe_get_all_assets(getter, filtered_request=req)
                filtered_exception_type = ""
                break
            except Exception as exc:
                filtered_exception_type = type(exc).__name__

        unfiltered_asset_count = len(unfiltered_rows)
        filtered_api_asset_count = len(filtered_rows)
        active_count = 0
        tradable_count = 0
        us_equity_count = 0
        rejected_by_asset_class = 0
        rejected_by_status = 0
        rejected_non_tradable = 0
        rejected_missing_symbol = 0
        client_filtered_rows: list[Any] = []

        for asset in unfiltered_rows:
            symbol = str(self._asset_value(asset, "symbol", "") or "").strip().upper()
            asset_class = self.normalize_asset_class(self._asset_value(asset, "asset_class", ""))
            status = self.normalize_status(self._asset_value(asset, "status", ""))
            tradable = self.normalize_tradable(self._asset_value(asset, "tradable", False))
            if asset_class == "US_EQUITY":
                us_equity_count += 1
            else:
                rejected_by_asset_class += 1
            if status == "ACTIVE":
                active_count += 1
            else:
                rejected_by_status += 1
            if tradable:
                tradable_count += 1
            else:
                rejected_non_tradable += 1
            if not symbol:
                rejected_missing_symbol += 1
            if asset_class == "US_EQUITY" and status == "ACTIVE" and tradable and symbol:
                client_filtered_rows.append(asset)

        fallback_used = filtered_api_asset_count == 0 and unfiltered_asset_count > 0
        selected_rows = filtered_rows if filtered_api_asset_count > 0 else client_filtered_rows
        elapsed_seconds = round(float(time.perf_counter() - started), 6)

        return {
            "selected_assets": list(selected_rows),
            "unfiltered_assets": list(unfiltered_rows),
            "filtered_assets": list(filtered_rows),
            "unfiltered_asset_count": int(unfiltered_asset_count),
            "filtered_api_asset_count": int(filtered_api_asset_count),
            "client_filtered_asset_count": int(len(client_filtered_rows)),
            "active_count": int(active_count),
            "tradable_count": int(tradable_count),
            "us_equity_count": int(us_equity_count),
            "rejected_by_asset_class": int(rejected_by_asset_class),
            "rejected_by_status": int(rejected_by_status),
            "rejected_non_tradable": int(rejected_non_tradable),
            "rejected_missing_symbol": int(rejected_missing_symbol),
            "fallback_used": bool(fallback_used),
            "api_exception_type": str(filtered_exception_type or ""),
            "api_request_elapsed_time": float(elapsed_seconds),
        }

    def get_assets(self):
        """Return assets visible to the connected paper account."""
        diagnostics = self.get_assets_diagnostics()
        return list(diagnostics.get("selected_assets") or [])

    def submit_order(self, *args, **kwargs):
        """Order submission remains blocked for safety."""
        raise NotImplementedError("Order submission is disabled in alpaca_client.")


def create_alpaca_client(mode=None, trading_client=None):
    """Create a paper-only Alpaca client."""
    return AlpacaClient(mode=mode, trading_client=trading_client)