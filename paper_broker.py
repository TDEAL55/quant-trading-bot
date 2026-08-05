from __future__ import annotations

import copy
import os
import time
from typing import Any

from alpaca_paper_broker import AlpacaPaperBroker
from config import PAPER_BROKER_BACKEND, is_safe_mode


class SimulatedPaperBroker:
    """Local deterministic broker used for tests and offline simulation."""

    def __init__(self, mode: str | None = None, buying_power: float = 10000.0, positions: dict[str, dict[str, float]] | None = None):
        selected_mode = str(mode or os.getenv("TRADING_MODE", "SIMULATION")).upper()
        if selected_mode == "LIVE":
            raise RuntimeError("LIVE mode is blocked for simulated paper broker")
        self.mode = selected_mode
        self.backend = "SIMULATED"
        self._buying_power = float(buying_power)
        default_positions = positions
        if default_positions is None:
            default_positions = {
                "SPY": {"quantity": 0.0, "avg_price": 0.0},
                "AAPL": {"quantity": 0.0, "avg_price": 0.0},
            }
        self._positions = {
            str(symbol).upper(): {
                "quantity": float((payload or {}).get("quantity") or 0.0),
                "avg_price": float((payload or {}).get("avg_price") or 0.0),
            }
            for symbol, payload in dict(default_positions or {}).items()
        }
        self._orders: dict[str, dict[str, Any]] = {}

    def is_safe(self) -> bool:
        return is_safe_mode(self.mode)

    def _require_safe(self, action_name: str) -> None:
        if not self.is_safe():
            raise RuntimeError(f"{action_name} is disabled because live trading is not allowed")

    def get_account(self) -> dict[str, Any]:
        self._require_safe("get_account")
        equity = self.get_portfolio_value()
        return {
            "mode": "paper",
            "status": "ACTIVE",
            "buying_power": float(self._buying_power),
            "cash": float(self._buying_power),
            "equity": float(equity),
            "portfolio_value": float(equity),
            "broker_backend": "SIMULATED",
        }

    def get_account_status(self) -> str:
        return "ACTIVE"

    def get_positions(self) -> dict[str, dict[str, float]]:
        self._require_safe("get_positions")
        return copy.deepcopy(self._positions)

    def get_buying_power(self) -> float:
        self._require_safe("get_buying_power")
        return float(self._buying_power)

    def get_cash(self) -> float:
        return self.get_buying_power()

    def get_equity(self) -> float:
        return self.get_portfolio_value()

    def get_portfolio_value(self) -> float:
        equity = float(self._buying_power)
        for payload in self._positions.values():
            equity += float(payload.get("quantity") or 0.0) * float(payload.get("avg_price") or 0.0)
        return float(equity)

    def get_open_orders(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._orders.values() if str(item.get("status") or "") not in {"filled", "canceled", "rejected", "expired"}]

    def get_order_by_id(self, order_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self._orders.get(str(order_id) or ""))

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        cid = str(client_order_id or "")
        for row in self._orders.values():
            if str(row.get("client_order_id") or "") == cid:
                return copy.deepcopy(row)
        return None

    def wait_for_order(self, *, order_id: str | None = None, client_order_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if order_id:
            item = self.get_order_by_id(order_id)
            if item:
                return item
        if client_order_id:
            item = self.get_order_by_client_order_id(client_order_id)
            if item:
                return item
        raise RuntimeError("simulated order not found")

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        item = self._orders.get(str(order_id) or "")
        if not item:
            raise RuntimeError("simulated order not found")
        item = dict(item)
        item["status"] = "canceled"
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._orders[str(order_id)] = item
        return copy.deepcopy(item)

    def submit_order(self, side: str, ticker: str, quantity: float, **kwargs: Any) -> dict[str, Any]:
        self._require_safe("submit_order")
        symbol = str(ticker or "").upper()
        normalized_side = str(side or "").strip().lower()
        qty = max(float(quantity or 0.0), 0.0)
        fill_price = float(kwargs.get("reference_price") or 100.0)
        client_order_id = str(kwargs.get("client_order_id") or "")
        if client_order_id:
            existing = self.get_order_by_client_order_id(client_order_id)
            if existing:
                existing["recovered_existing"] = True
                return existing
        if not symbol or qty <= 0 or fill_price <= 0:
            raise RuntimeError("invalid simulated order")

        position = self._positions.setdefault(symbol, {"quantity": 0.0, "avg_price": fill_price})
        if normalized_side == "buy":
            position["quantity"] = float(position.get("quantity") or 0.0) + qty
            position["avg_price"] = fill_price
            self._buying_power -= qty * fill_price
        elif normalized_side == "sell":
            position["quantity"] = max(float(position.get("quantity") or 0.0) - qty, 0.0)
            self._buying_power += qty * fill_price
            if position["quantity"] <= 0:
                self._positions.pop(symbol, None)
        else:
            raise RuntimeError("invalid side")

        order_id = f"sim-{symbol}-{int(time.time() * 1000)}"
        payload = {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": normalized_side,
            "requested_quantity": qty,
            "filled_quantity": qty,
            "order_type": "market",
            "time_in_force": str(kwargs.get("time_in_force") or "day").lower(),
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "filled",
            "average_fill_price": fill_price,
            "rejection_reason": "",
            "broker_backend": "SIMULATED",
        }
        self._orders[order_id] = dict(payload)
        return copy.deepcopy(payload)


PaperBroker = SimulatedPaperBroker


def create_paper_broker(
    mode: str | None = None,
    credentials: dict[str, Any] | None = None,
    backend: str | None = None,
    trading_client: Any | None = None,
    buying_power: float = 10000.0,
    positions: dict[str, dict[str, float]] | None = None,
):
    """Create a PAPER broker backend using ALPACA or SIMULATED configuration."""
    del credentials
    selected_mode = str(mode or os.getenv("TRADING_MODE", "SIMULATION")).strip().upper()
    if selected_mode == "LIVE":
        raise RuntimeError("LIVE mode is blocked")

    if backend is None and selected_mode == "SIMULATION":
        selected_backend = "SIMULATED"
    else:
        selected_backend = str(backend or os.getenv("PAPER_BROKER_BACKEND", PAPER_BROKER_BACKEND)).strip().upper()
    if selected_backend not in {"ALPACA", "SIMULATED"}:
        raise RuntimeError("PAPER_BROKER_BACKEND must be ALPACA or SIMULATED")

    if selected_backend == "ALPACA":
        return AlpacaPaperBroker(mode=selected_mode, trading_client=trading_client)
    return SimulatedPaperBroker(mode=selected_mode, buying_power=buying_power, positions=positions)
