from __future__ import annotations

from decimal import Decimal
import os
from typing import Any, Mapping

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest, StopLossRequest, TakeProfitRequest
except Exception:  # pragma: no cover - handled at runtime
    TradingClient = None
    OrderClass = None
    OrderSide = None
    QueryOrderStatus = None
    TimeInForce = None
    GetOrdersRequest = None
    MarketOrderRequest = None
    StopLossRequest = None
    TakeProfitRequest = None

from alpaca_paper_broker import (
    AlpacaPaperBroker,
    _default_client_order_id,
    _is_true,
    _normalize_account_status,
    _normalize_url,
    _to_float,
    normalize_alpaca_order,
)
from live_risk_policy import LIVE_CONFIRMATION_PHRASE, LIVE_ENDPOINT


class AlpacaLiveBroker(AlpacaPaperBroker):
    """Narrow live adapter for the separately gated micro-live runner."""

    def __init__(
        self,
        mode: str | None = None,
        trading_client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        read_only: bool = False,
    ):
        env = dict(os.environ if environ is None else environ)
        selected_mode = str(mode or env.get("TRADING_MODE", "")).strip().upper()
        if selected_mode != "LIVE":
            raise RuntimeError("Alpaca live broker requires TRADING_MODE=LIVE")
        if not read_only:
            if not _is_true(env.get("LIVE_TRADING_ENABLED", "false")):
                raise RuntimeError("LIVE_TRADING_ENABLED must be true")
            if not _is_true(env.get("ALPACA_LIVE_ORDER_SUBMISSION_ENABLED", "false")):
                raise RuntimeError("ALPACA_LIVE_ORDER_SUBMISSION_ENABLED must be true")
            if str(env.get("LIVE_TRADING_CONFIRMATION", "")).strip() != LIVE_CONFIRMATION_PHRASE:
                raise RuntimeError("LIVE_TRADING_CONFIRMATION is missing or invalid")

        self.mode = "LIVE"
        self.backend = "ALPACA_LIVE_MICRO"
        self.api_key = str(env.get("ALPACA_LIVE_API_KEY", "")).strip()
        self.api_secret = str(env.get("ALPACA_LIVE_API_SECRET", "")).strip()
        self.base_url = str(env.get("ALPACA_LIVE_BASE_URL", LIVE_ENDPOINT)).strip() or LIVE_ENDPOINT
        self.order_submission_enabled = not read_only
        self.allow_short_selling = False
        if _normalize_url(self.base_url) != _normalize_url(LIVE_ENDPOINT):
            raise RuntimeError("ALPACA_LIVE_BASE_URL must be https://api.alpaca.markets")
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Missing ALPACA_LIVE_API_KEY or ALPACA_LIVE_API_SECRET")

        self._trading_client = trading_client or self._create_live_trading_client()
        self._validate_account_ready()

    def _create_live_trading_client(self) -> Any:
        if TradingClient is None:
            raise RuntimeError("alpaca-py is required for Alpaca live trading")
        return TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=False,
            url_override=self.base_url,
        )

    def get_account(self) -> dict[str, Any]:
        account = self._fetch_account()
        equity = _to_float(getattr(account, "equity", 0.0), 0.0)
        last_equity = _to_float(getattr(account, "last_equity", equity), equity)
        day_pl = equity - last_equity if last_equity > 0 else 0.0
        return {
            "status": _normalize_account_status(getattr(account, "status", "unknown")),
            "account_number": str(getattr(account, "account_number", "") or ""),
            "currency": str(getattr(account, "currency", "USD") or "USD"),
            "buying_power": _to_float(getattr(account, "buying_power", 0.0), 0.0),
            "non_marginable_buying_power": _to_float(
                getattr(account, "non_marginable_buying_power", getattr(account, "cash", 0.0)),
                0.0,
            ),
            "cash": _to_float(getattr(account, "cash", 0.0), 0.0),
            "equity": equity,
            "last_equity": last_equity,
            "day_pl": day_pl,
            "portfolio_value": _to_float(getattr(account, "portfolio_value", equity), equity),
            "multiplier": _to_float(getattr(account, "multiplier", 1.0), 1.0),
            "trading_blocked": bool(getattr(account, "trading_blocked", False)),
            "account_blocked": bool(getattr(account, "account_blocked", False)),
            "live_endpoint_confirmed": True,
            "broker_backend": self.backend,
        }

    def get_tradable_crypto_assets(self) -> list[dict[str, Any]]:
        raise RuntimeError("crypto is disabled for micro-live trading")

    def get_option_contracts(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        raise RuntimeError("options are disabled for micro-live trading")

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return open parents and flattened bracket legs for protection checks."""
        if GetOrdersRequest is not None and QueryOrderStatus is not None:
            rows = self._trading_client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
            )
        else:
            rows = self._trading_client.get_orders()
        normalized: list[dict[str, Any]] = []
        for row in rows or []:
            parent = normalize_alpaca_order(row)
            if parent:
                normalized.append(parent)
            for leg in list(getattr(row, "legs", None) or []):
                child = normalize_alpaca_order(leg)
                if child:
                    normalized.append(child)
        return normalized

    def submit_bracket_entry(
        self,
        *,
        symbol: str,
        quantity: int,
        reference_price: float,
        stop_price: float,
        target_price: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").strip().upper()
        qty = int(quantity)
        reference = _to_float(reference_price, 0.0)
        stop = round(_to_float(stop_price, 0.0), 2)
        target = round(_to_float(target_price, 0.0), 2)
        if not normalized_symbol or qty <= 0:
            raise RuntimeError("whole-share symbol and quantity are required")
        if not 0 < stop < reference < target:
            raise RuntimeError("bracket prices must satisfy stop < reference < target")
        self._validate_quantity(normalized_symbol, qty, allow_fractional=False)

        cid = str(client_order_id or "").strip() or _default_client_order_id(
            symbol=normalized_symbol,
            side="buy",
            quantity=qty,
            order_type="bracket",
            time_in_force="gtc",
        )
        existing = self.get_order_by_client_order_id(cid)
        if existing:
            existing["recovered_existing"] = True
            return existing
        if not self.order_submission_enabled:
            return {
                "status": "submission_blocked_by_config",
                "symbol": normalized_symbol,
                "requested_quantity": qty,
                "client_order_id": cid,
                "broker_backend": self.backend,
            }
        if any(item is None for item in (MarketOrderRequest, StopLossRequest, TakeProfitRequest, OrderClass)):
            raise RuntimeError("installed alpaca-py does not support bracket orders")

        request = MarketOrderRequest(
            symbol=normalized_symbol,
            qty=Decimal(str(qty)),
            side=OrderSide.BUY if OrderSide is not None else "buy",
            time_in_force=TimeInForce.GTC if TimeInForce is not None else "gtc",
            client_order_id=cid,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=target),
            stop_loss=StopLossRequest(stop_price=stop),
        )
        try:
            order = self._trading_client.submit_order(order_data=request)
            result = normalize_alpaca_order(order)
        except Exception:
            recovered = self.get_order_by_client_order_id(cid)
            if not recovered:
                raise
            result = recovered
            result["recovered_after_submit_error"] = True
        result.update(
            {
                "reference_price": reference,
                "stop_price": stop,
                "target_price": target,
                "protective_order_class": "bracket",
                "broker_backend": self.backend,
            }
        )
        return result
