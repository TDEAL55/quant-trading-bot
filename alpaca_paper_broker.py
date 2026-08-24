from __future__ import annotations

import os
import time
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
except Exception:  # pragma: no cover - import failures are handled at runtime
    TradingClient = None
    OrderSide = None
    QueryOrderStatus = None
    TimeInForce = None
    GetOrdersRequest = None
    MarketOrderRequest = None


ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
FINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected", "done_for_day"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_account_status(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().upper()
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str) and enum_value.strip():
        return enum_value.strip().upper()
    text = str(value).strip()
    if "." in text:
        text = text.split(".")[-1]
    return (text or "UNKNOWN").upper()


def _normalize_enum_text(value: Any, default: str = "") -> str:
    """Return Alpaca enum values without leaking strings such as OrderSide.BUY."""
    if value is None:
        return str(default or "").strip().lower()
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        value = enum_value
    else:
        enum_name = getattr(value, "name", None)
        if enum_name is not None:
            value = enum_name
    text = str(value or default or "").strip()
    if "." in text:
        text = text.split(".")[-1]
    return text.lower()


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _time_in_force(value: str) -> Any:
    normalized = str(value or "day").strip().lower()
    if TimeInForce is None:
        return normalized
    if normalized == "gtc":
        return TimeInForce.GTC
    return TimeInForce.DAY


def _order_side(value: str) -> Any:
    normalized = str(value or "").strip().lower()
    if normalized not in {"buy", "sell"}:
        raise RuntimeError("order side must be BUY or SELL")
    if OrderSide is None:
        return normalized
    return OrderSide.BUY if normalized == "buy" else OrderSide.SELL


def _default_client_order_id(symbol: str, side: str, quantity: float, order_type: str, time_in_force: str) -> str:
    payload = {
        "symbol": str(symbol or "").upper(),
        "side": str(side or "").lower(),
        "quantity": round(_to_float(quantity, 0.0), 8),
        "order_type": str(order_type or "market").lower(),
        "time_in_force": str(time_in_force or "day").lower(),
        "trade_date": datetime.now(timezone.utc).date().isoformat(),
    }
    digest = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()[:24]
    return f"qtb-{digest}"


def normalize_alpaca_order(order: Any) -> dict[str, Any]:
    if not order:
        return {}
    return {
        "order_id": str(getattr(order, "id", "") or ""),
        "client_order_id": str(getattr(order, "client_order_id", "") or ""),
        "symbol": str(getattr(order, "symbol", "") or "").upper(),
        "side": _normalize_enum_text(getattr(order, "side", "")),
        "requested_quantity": _to_float(getattr(order, "qty", 0.0), 0.0),
        "filled_quantity": _to_float(getattr(order, "filled_qty", 0.0), 0.0),
        "order_type": _normalize_enum_text(getattr(order, "order_type", "market"), "market"),
        "time_in_force": _normalize_enum_text(getattr(order, "time_in_force", "day"), "day"),
        "submitted_at": str(getattr(order, "submitted_at", "") or ""),
        "updated_at": str(getattr(order, "updated_at", "") or ""),
        "status": _normalize_enum_text(getattr(order, "status", "unknown"), "unknown"),
        "average_fill_price": _to_float(getattr(order, "filled_avg_price", 0.0), 0.0),
        "rejection_reason": str(getattr(order, "failed_at", "") or "") if _normalize_enum_text(getattr(order, "status", "")) == "rejected" else "",
        "broker_backend": "ALPACA",
    }


class AlpacaPaperBroker:
    """Paper-only Alpaca broker with duplicate-safe client order recovery."""

    def __init__(self, mode: str | None = None, trading_client: Any | None = None):
        selected_mode = str(mode or os.getenv("TRADING_MODE", "PAPER")).strip().upper()
        if selected_mode == "LIVE":
            raise RuntimeError("LIVE mode is blocked for Alpaca paper broker")
        if selected_mode not in {"PAPER", "SIMULATION"}:
            raise RuntimeError("mode must be PAPER or SIMULATION")

        self.mode = selected_mode
        self.backend = "ALPACA"
        self.api_key = str(os.getenv("ALPACA_API_KEY", "")).strip()
        self.api_secret = str(os.getenv("ALPACA_API_SECRET", "")).strip()
        self.base_url = str(os.getenv("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)).strip() or ALPACA_PAPER_ENDPOINT
        self.order_submission_enabled = _is_true(os.getenv("ALPACA_ORDER_SUBMISSION_ENABLED", "false"))
        self.allow_short_selling = bool(
            self.mode == "PAPER" and _is_true(os.getenv("PAPER_ALLOW_SHORT_SELLING", "false"))
        )

        if _normalize_url(self.base_url) != _normalize_url(ALPACA_PAPER_ENDPOINT):
            raise RuntimeError("ALPACA_PAPER_BASE_URL must be https://paper-api.alpaca.markets")
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Missing required Alpaca credentials: ALPACA_API_KEY, ALPACA_API_SECRET")

        self._trading_client = trading_client or self._create_trading_client()
        self._validate_account_ready()

    def _create_trading_client(self) -> Any:
        if TradingClient is None:
            raise RuntimeError("alpaca-py is required. Install alpaca-py before using Alpaca paper broker.")
        return TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=True,
            url_override=self.base_url,
        )

    def _fetch_account(self) -> Any:
        return self._trading_client.get_account()

    def _validate_account_ready(self) -> None:
        account = self._fetch_account()
        status = _normalize_account_status(getattr(account, "status", ""))
        if status not in {"ACTIVE"}:
            raise RuntimeError(f"Alpaca account is not active: {status or 'UNKNOWN'}")
        if bool(getattr(account, "trading_blocked", False)):
            raise RuntimeError("Alpaca account is trading_blocked")
        if bool(getattr(account, "account_blocked", False)):
            raise RuntimeError("Alpaca account is account_blocked")

    def get_account(self) -> dict[str, Any]:
        account = self._fetch_account()
        equity = _to_float(getattr(account, "equity", 0.0), 0.0)
        last_equity = _to_float(getattr(account, "last_equity", equity), equity)
        return {
            "status": _normalize_account_status(getattr(account, "status", "unknown")),
            "account_number": str(getattr(account, "account_number", "") or ""),
            "currency": str(getattr(account, "currency", "USD") or "USD"),
            "buying_power": _to_float(getattr(account, "buying_power", 0.0), 0.0),
            "cash": _to_float(getattr(account, "cash", 0.0), 0.0),
            "equity": equity,
            "last_equity": last_equity,
            "day_pl": equity - last_equity,
            "portfolio_value": _to_float(getattr(account, "portfolio_value", getattr(account, "equity", 0.0)), 0.0),
            "trading_blocked": bool(getattr(account, "trading_blocked", False)),
            "account_blocked": bool(getattr(account, "account_blocked", False)),
            "paper_endpoint_confirmed": True,
            "broker_backend": "ALPACA",
        }

    def get_account_status(self) -> str:
        return str(self.get_account().get("status") or "unknown")

    def get_buying_power(self) -> float:
        return float(self.get_account().get("buying_power") or 0.0)

    def get_cash(self) -> float:
        return float(self.get_account().get("cash") or 0.0)

    def get_equity(self) -> float:
        return float(self.get_account().get("equity") or 0.0)

    def get_portfolio_value(self) -> float:
        return float(self.get_account().get("portfolio_value") or 0.0)

    def get_positions(self) -> dict[str, dict[str, float]]:
        rows = self._trading_client.get_all_positions()
        result: dict[str, dict[str, float]] = {}
        for position in rows or []:
            symbol = str(getattr(position, "symbol", "") or "").upper()
            if not symbol:
                continue
            result[symbol] = {
                "quantity": _to_float(getattr(position, "qty", 0.0), 0.0),
                "avg_price": _to_float(getattr(position, "avg_entry_price", 0.0), 0.0),
                "current_price": _to_float(getattr(position, "current_price", 0.0), 0.0),
                "market_value": _to_float(getattr(position, "market_value", 0.0), 0.0),
                "unrealized_pl": _to_float(getattr(position, "unrealized_pl", 0.0), 0.0),
                "unrealized_plpc": _to_float(getattr(position, "unrealized_plpc", 0.0), 0.0),
            }
        return result

    def get_open_orders(self) -> list[dict[str, Any]]:
        if GetOrdersRequest is not None and QueryOrderStatus is not None:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            rows = self._trading_client.get_orders(filter=request)
        else:
            rows = self._trading_client.get_orders()
        return [normalize_alpaca_order(row) for row in rows or []]

    def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 50), 500))
        if GetOrdersRequest is not None and QueryOrderStatus is not None:
            request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=safe_limit)
            rows = self._trading_client.get_orders(filter=request)
        else:
            rows = self._trading_client.get_orders()
        normalized = [normalize_alpaca_order(row) for row in rows or []]
        return sorted(
            normalized,
            key=lambda row: str(row.get("updated_at") or row.get("submitted_at") or ""),
            reverse=True,
        )[:safe_limit]

    def get_order_by_id(self, order_id: str) -> dict[str, Any] | None:
        order_id = str(order_id or "").strip()
        if not order_id:
            return None
        order = self._trading_client.get_order_by_id(order_id)
        return normalize_alpaca_order(order)

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        cid = str(client_order_id or "").strip()
        if not cid:
            return None
        try:
            order = self._trading_client.get_order_by_client_id(cid)
        except Exception:
            return None
        normalized = normalize_alpaca_order(order)
        return normalized or None

    def _validate_quantity(self, symbol: str, quantity: float, allow_fractional: bool) -> None:
        qty = _to_float(quantity, 0.0)
        if qty <= 0:
            raise RuntimeError("quantity must be greater than zero")
        if not allow_fractional and abs(qty - round(qty)) > 1e-9:
            raise RuntimeError("fractional quantity is not allowed for this order")
        try:
            asset = self._trading_client.get_asset(str(symbol).upper())
        except Exception:
            return
        if not bool(getattr(asset, "tradable", True)):
            raise RuntimeError(f"asset is not tradable: {symbol}")
        if abs(qty - round(qty)) > 1e-9 and not bool(getattr(asset, "fractionable", False)):
            raise RuntimeError(f"asset does not support fractional orders: {symbol}")

    def submit_order(
        self,
        side: str,
        ticker: str,
        quantity: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        client_order_id = str(kwargs.get("client_order_id") or "").strip()
        order_type = str(kwargs.get("order_type") or "market")
        time_in_force = str(kwargs.get("time_in_force") or "day")
        allow_fractional = bool(kwargs.get("allow_fractional", False))
        wait_for_fill = bool(kwargs.get("wait_for_fill", True))
        poll_seconds = float(kwargs.get("poll_seconds", 1.0))
        max_wait_seconds = float(kwargs.get("max_wait_seconds", 45.0))
        reference_price = _to_float(kwargs.get("reference_price"), 0.0)

        symbol = str(ticker or "").strip().upper()
        if not symbol:
            raise RuntimeError("symbol is required")
        normalized_side = _normalize_enum_text(side)
        if normalized_side not in {"buy", "sell"}:
            raise RuntimeError("order side must be BUY or SELL")
        self._validate_quantity(symbol=symbol, quantity=quantity, allow_fractional=allow_fractional)

        if not client_order_id:
            client_order_id = _default_client_order_id(symbol=symbol, side=side, quantity=quantity, order_type=order_type, time_in_force=time_in_force)

        existing = self.get_order_by_client_order_id(client_order_id)
        if existing:
            existing["recovered_existing"] = True
            if reference_price > 0:
                existing.setdefault("reference_price", reference_price)
            if wait_for_fill:
                return self.wait_for_order(client_order_id=client_order_id, poll_seconds=poll_seconds, max_wait_seconds=max_wait_seconds)
            return existing

        if not self.order_submission_enabled:
            return {
                "status": "submission_blocked_by_config",
                "symbol": symbol,
                "side": str(side or "").lower(),
                "requested_quantity": _to_float(quantity, 0.0),
                "client_order_id": str(client_order_id or ""),
                "reference_price": reference_price,
                "broker_backend": "ALPACA",
            }

        if normalized_side == "sell" and not self.allow_short_selling:
            current_position = self.get_positions().get(symbol) or {}
            held_quantity = _to_float(current_position.get("quantity"), 0.0)
            requested_quantity = _to_float(quantity, 0.0)
            if held_quantity <= 0:
                raise RuntimeError(f"naked short blocked: no long {symbol} position is available to sell")
            if requested_quantity > held_quantity + 1e-8:
                raise RuntimeError(
                    f"oversell blocked: requested {requested_quantity:g} {symbol}, only {held_quantity:g} held"
                )

        order_kind = str(order_type or "market").strip().lower()
        if order_kind != "market":
            raise RuntimeError("only market order type is supported for first PAPER deployment")

        side_value = _order_side(normalized_side)
        tif_value = _time_in_force(time_in_force)
        qty_decimal = _to_decimal(quantity)

        try:
            order_request = MarketOrderRequest(
                symbol=symbol,
                qty=qty_decimal,
                side=side_value,
                time_in_force=tif_value,
                client_order_id=str(client_order_id),
            )
            submitted = self._trading_client.submit_order(order_data=order_request)
            submitted_order = normalize_alpaca_order(submitted)
            if reference_price > 0:
                submitted_order["reference_price"] = reference_price
        except Exception:
            recovered = self.get_order_by_client_order_id(client_order_id)
            if recovered:
                recovered["recovered_after_submit_error"] = True
                if reference_price > 0:
                    recovered.setdefault("reference_price", reference_price)
                submitted_order = recovered
            else:
                raise

        if wait_for_fill:
            return self.wait_for_order(
                order_id=str(submitted_order.get("order_id") or ""),
                client_order_id=str(client_order_id),
                poll_seconds=poll_seconds,
                max_wait_seconds=max_wait_seconds,
            )
        return submitted_order

    def wait_for_order(
        self,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
        poll_seconds: float = 1.0,
        max_wait_seconds: float = 45.0,
    ) -> dict[str, Any]:
        started = time.monotonic()
        last_seen: dict[str, Any] | None = None
        while True:
            order = None
            if order_id:
                order = self.get_order_by_id(order_id)
            if not order and client_order_id:
                order = self.get_order_by_client_order_id(client_order_id)
            if order:
                last_seen = order
                status = str(order.get("status") or "unknown").lower()
                if status in FINAL_ORDER_STATUSES:
                    return order
            if (time.monotonic() - started) >= float(max_wait_seconds):
                if last_seen:
                    last_seen["timed_out_waiting_for_final_status"] = True
                    return last_seen
                raise RuntimeError("Timed out waiting for Alpaca order status")
            time.sleep(max(float(poll_seconds), 0.1))

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        oid = str(order_id or "").strip()
        if not oid:
            raise RuntimeError("order_id is required")
        self._trading_client.cancel_order_by_id(oid)
        canceled = self.get_order_by_id(oid) or {"order_id": oid, "status": "canceled"}
        return canceled

    def reconcile_symbol(self, symbol: str, expected_quantity: float, tolerance: float = 1e-6) -> dict[str, Any]:
        positions = self.get_positions()
        actual_qty = _to_float((positions.get(str(symbol).upper()) or {}).get("quantity"), 0.0)
        expected_qty = _to_float(expected_quantity, 0.0)
        mismatch = abs(actual_qty - expected_qty) > float(tolerance)
        return {
            "symbol": str(symbol).upper(),
            "expected_quantity": expected_qty,
            "actual_quantity": actual_qty,
            "matched": not mismatch,
            "difference": round(actual_qty - expected_qty, 6),
            "broker_backend": "ALPACA",
        }
