from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping

from alpaca_live_broker import AlpacaLiveBroker
from correlation_engine import CorrelationPolicy, assess_symbol_correlation
from live_risk_policy import (
    LiveRiskSettings,
    evaluate_live_performance_controls,
    evaluate_live_readiness,
    live_entry_notional,
    settings_from_environment,
    stop_risk_position_size,
)
from scanner_runner import _symbol_records_from_list, run_scan
from shadow_exit_trial_ledger import ShadowExitTrialLedger
from stock_pnl_reconstruction import reconstruct_stock_realized_pnl
from stock_exit_policy import evaluate_stock_exit
from strategies.paper_strategy_plugins import evaluate_all_strategies


FINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "done_for_day",
    "replaced",
}
ACCEPTED_ORDER_STATUSES = {"accepted", "new", "pending", "pending_new", "partially_filled", "filled"}
REGIME_STRATEGY_ROUTES = {
    "bull": ("stock_trend_pullback_v3", "stock_trend_ensemble_v2"),
    "weak_bull": ("stock_trend_pullback_v3", "stock_trend_ensemble_v2", "stock_mean_reversion_v2"),
    "sideways": ("stock_mean_reversion_v2",),
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_stock_universe(
    broker: Any,
    policy: LiveRiskSettings,
    env: Mapping[str, str],
) -> tuple[LiveRiskSettings, list[dict[str, Any]]]:
    if not _is_true(env.get("LIVE_FULL_STOCK_UNIVERSE", "true")):
        return policy, _symbol_records_from_list(list(policy.allowed_symbols))
    records = list(
        broker.get_tradable_stock_assets(
            include_etfs=_is_true(env.get("LIVE_INCLUDE_ETFS", "false"))
        )
        or []
    )
    maximum = max(int(env.get("LIVE_MAX_UNIVERSE_SIZE", "0") or 0), 0)
    if maximum:
        records = records[:maximum]
    symbols = tuple(str(item.get("symbol") or "").strip().upper() for item in records if item.get("symbol"))
    if not symbols:
        raise RuntimeError("alpaca_full_stock_universe_empty")
    return replace(policy, allowed_symbols=symbols), records


class LiveStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"orders_by_date": {}, "submissions": []}
        return dict(payload or {})

    def orders_submitted_on(self, date_key: str) -> int:
        state = self.load()
        return int(dict(state.get("orders_by_date") or {}).get(str(date_key), 0) or 0)

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(dict(state), stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def record_position_observations(
        self,
        positions: Mapping[str, Mapping[str, Any]],
        *,
        observed_at: datetime,
    ) -> None:
        """Persist price observations used for evidence-based MFE/MAE review."""
        state = self.load()
        paths = dict(state.get("price_paths") or {})
        timestamp = observed_at.astimezone(timezone.utc).isoformat()
        for raw_symbol, raw_position in dict(positions or {}).items():
            symbol = str(raw_symbol or "").strip().upper()
            position = dict(raw_position or {})
            current = _as_float(
                position.get("current_price") or position.get("market_price") or position.get("price"),
                0.0,
            )
            if not symbol or current <= 0:
                continue
            rows = list(paths.get(symbol) or [])
            rows.append({"timestamp": timestamp, "high": current, "low": current, "source": "live_cycle_observation"})
            paths[symbol] = rows[-10000:]
        state["price_paths"] = paths
        self._save(state)

    def record_account_observation(
        self,
        account: Mapping[str, Any],
        *,
        observed_at: datetime,
    ) -> None:
        state = self.load()
        rows = list(state.get("equity_history") or [])
        rows.append(
            {
                "timestamp": observed_at.astimezone(timezone.utc).isoformat(),
                "equity": _as_float(account.get("equity"), 0.0),
                "cash": _as_float(account.get("cash"), 0.0),
                "last_equity": _as_float(account.get("last_equity"), 0.0),
                "account_balance_change": _as_float(
                    account.get("account_balance_change"), 0.0
                ),
                "intraday_adjustments": _as_float(
                    account.get("intraday_adjustments"), 0.0
                ),
                "adjusted_day_pl": _as_float(account.get("day_pl"), 0.0),
            }
        )
        state["equity_history"] = rows[-10000:]
        self._save(state)

    def record_cycle_result(self, result: Mapping[str, Any], *, recorded_at: str | None = None) -> None:
        """Persist the latest runner decision so the dashboard can explain pauses."""
        state = self.load()
        payload = dict(result or {})
        state["last_cycle"] = {
            "recorded_at": str(recorded_at or _utc_iso()),
            "status": str(payload.get("status") or "unknown"),
            "reasons": [str(item) for item in list(payload.get("reasons") or [])],
            "submitted": bool(payload.get("submitted")),
            "readiness": dict(payload.get("readiness") or {}),
            "candidate_symbol": str(dict(payload.get("candidate") or {}).get("symbol") or ""),
            "scan_summary": dict(payload.get("scan_summary") or {}),
            "candidate_funnel": dict(payload.get("candidate_funnel") or {}),
            "live_exit_shadow": list(payload.get("live_exit_shadow") or []),
            "live_exit_actions": list(payload.get("live_exit_actions") or []),
            "shadow_exit_trial_summary": dict(payload.get("shadow_exit_trial_summary") or {}),
        }
        self._save(state)

    def record_market_bars(
        self,
        price_history_by_symbol: Mapping[str, Any],
        *,
        symbols: set[str],
    ) -> None:
        """Merge scanner OHLC bars for held symbols into the durable review path."""
        if not symbols:
            return
        state = self.load()
        paths = dict(state.get("price_paths") or {})
        for symbol in sorted(symbols):
            incoming = list(dict(price_history_by_symbol or {}).get(symbol) or [])
            existing = list(paths.get(symbol) or [])
            merged: dict[str, dict[str, Any]] = {
                str(row.get("timestamp") or row.get("date") or ""): dict(row)
                for row in existing
                if isinstance(row, dict) and (row.get("timestamp") or row.get("date"))
            }
            for raw in incoming:
                row = dict(raw or {}) if isinstance(raw, dict) else {}
                timestamp = str(row.get("timestamp") or row.get("date") or row.get("t") or "")
                close = _as_float(row.get("close") or row.get("price") or row.get("c"), 0.0)
                high = _as_float(row.get("high") or row.get("h"), close)
                low = _as_float(row.get("low") or row.get("l"), close)
                if timestamp and high > 0 and low > 0:
                    merged[timestamp] = {
                        "timestamp": timestamp,
                        "high": high,
                        "low": low,
                        "source": "scanner_market_bar",
                    }
            paths[symbol] = [merged[key] for key in sorted(merged)][-10000:]
        state["price_paths"] = paths
        self._save(state)

    def record_submission(self, order: Mapping[str, Any], *, date_key: str, strategy: Mapping[str, Any]) -> None:
        state = self.load()
        orders_by_date = dict(state.get("orders_by_date") or {})
        orders_by_date[str(date_key)] = int(orders_by_date.get(str(date_key), 0) or 0) + 1
        submissions = list(state.get("submissions") or [])
        supporting = dict(strategy.get("supporting_factors") or {})
        confirmations = dict(supporting.get("confirmations") or {})
        components = dict(supporting.get("components") or {})
        factor_values = dict(supporting.get("factor_values") or {})
        confirmation_categories = sorted(str(name) for name, passed in confirmations.items() if passed)
        reference_price = _as_float(order.get("reference_price"), 0.0)
        stop_price = _as_float(order.get("stop_price"), 0.0)
        quantity = _as_float(order.get("requested_quantity"), 0.0)
        submissions.append(
            {
                "recorded_at": _utc_iso(),
                "date": str(date_key),
                "order_id": str(order.get("order_id") or ""),
                "client_order_id": str(order.get("client_order_id") or ""),
                "symbol": str(order.get("symbol") or "").upper(),
                "status": str(order.get("status") or "unknown").lower(),
                "quantity": quantity,
                "reference_price": reference_price,
                "stop_price": stop_price,
                "target_price": _as_float(order.get("target_price"), 0.0),
                "strategy_id": str(strategy.get("strategy_id") or ""),
                "strategy_version": str(strategy.get("strategy_version") or ""),
                "market_regime": str(strategy.get("market_regime") or strategy.get("ensemble_route") or "unknown"),
                "strategy_score": _as_float(strategy.get("strategy_score"), 0.0),
                "confidence": _as_float(strategy.get("confidence"), 0.0),
                "expected_reward_risk": _as_float(strategy.get("expected_reward_risk"), 0.0),
                "entry_reason": str(strategy.get("entry_reason") or ""),
                "target_or_exit_rule": str(strategy.get("target_or_exit_rule") or ""),
                "eligible_strategy_ids": list(strategy.get("eligible_strategy_ids") or []),
                "confirmation_categories": confirmation_categories,
                "confirmations": confirmations,
                "trend_strength": components.get("trend") or factor_values.get("trend_strength"),
                "volatility": components.get("volatility") or factor_values.get("volatility"),
                "volume": components.get("volume") or factor_values.get("volume"),
                "relative_strength": components.get("relative_strength") or factor_values.get("relative_strength"),
                "position_risk_dollars": round(max(reference_price - stop_price, 0.0) * quantity, 6),
                "rule_checks": dict(strategy.get("rule_checks") or {}),
            }
        )
        state.update({"orders_by_date": orders_by_date, "submissions": submissions[-5000:]})
        self._save(state)


def _protective_order_symbols(
    positions: Mapping[str, Mapping[str, Any]],
    open_orders: list[Mapping[str, Any]],
) -> tuple[set[str], list[dict[str, Any]], list[str]]:
    held = {str(symbol).upper() for symbol in dict(positions or {})}
    protective_counts: dict[str, int] = {symbol: 0 for symbol in held}
    unreconciled: list[dict[str, Any]] = []
    for raw_order in open_orders or []:
        order = dict(raw_order or {})
        symbol = str(order.get("symbol") or "").upper()
        side = str(order.get("side") or "").lower()
        status = str(order.get("status") or "").lower()
        if status in FINAL_ORDER_STATUSES:
            continue
        if symbol in held and side == "sell":
            protective_counts[symbol] = protective_counts.get(symbol, 0) + 1
        else:
            unreconciled.append(order)
    protected = {symbol for symbol, count in protective_counts.items() if count >= 2}
    unprotected = sorted(held.difference(protected))
    return protected, unreconciled, unprotected


def build_live_exit_shadow_reviews(
    positions: Mapping[str, Mapping[str, Any]],
    state: Mapping[str, Any],
    *,
    observed_at: datetime,
    settings: LiveRiskSettings,
    open_orders: list[Mapping[str, Any]] | None = None,
    order_history: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate richer exits for visibility only; never submit or alter orders."""
    submissions = list(state.get("submissions") or [])
    price_paths = dict(state.get("price_paths") or {})
    reviews: list[dict[str, Any]] = []
    for raw_symbol, raw_position in dict(positions or {}).items():
        symbol = str(raw_symbol or "").strip().upper()
        position = dict(raw_position or {})
        quantity = _as_float(position.get("quantity") or position.get("qty"), 0.0)
        side = "SHORT" if quantity < 0 else "LONG"
        entry_price = _as_float(
            position.get("avg_entry_price")
            or position.get("average_entry_price")
            or position.get("avg_price"),
            0.0,
        )
        current_price = _as_float(position.get("current_price") or position.get("market_price"), 0.0)
        if not symbol or entry_price <= 0 or current_price <= 0:
            continue
        submission = next(
            (dict(row or {}) for row in reversed(submissions) if str(dict(row or {}).get("symbol") or "").upper() == symbol),
            {},
        )
        recorded_quantity = abs(_as_float(submission.get("quantity"), 0.0))
        recorded_reference_price = _as_float(submission.get("reference_price"), 0.0)
        recorded_stop_price = _as_float(submission.get("stop_price"), 0.0)
        submission_order_id = str(submission.get("order_id") or "").strip()
        submission_client_order_id = str(submission.get("client_order_id") or "").strip()
        submission_timestamp = _as_datetime(submission.get("recorded_at"))
        entry_timestamp: datetime | None = None
        entry_timestamp_source = "UNVERIFIED"
        absolute_quantity = abs(quantity)
        quantity_tolerance = max(1e-6, absolute_quantity * 1e-6)

        position_attributed = True
        attribution_validation_reason = "confirmed_bot_position_and_bracket"
        if not submission:
            position_attributed = False
            attribution_validation_reason = "bot_submission_missing"
        elif not submission_order_id or not submission_client_order_id:
            position_attributed = False
            attribution_validation_reason = "bot_submission_identity_missing"
        elif submission_timestamp is None or submission_timestamp > observed_at:
            position_attributed = False
            attribution_validation_reason = "bot_submission_timestamp_invalid"
        elif recorded_quantity <= 0 or abs(recorded_quantity - absolute_quantity) > quantity_tolerance:
            position_attributed = False
            attribution_validation_reason = "position_quantity_mismatch"
        elif recorded_reference_price <= 0:
            position_attributed = False
            attribution_validation_reason = "bot_submission_reference_missing"

        expected_entry_side = "sell" if side == "SHORT" else "buy"
        if position_attributed:
            filled_parents = []
            for raw_order in list(order_history or []):
                order = dict(raw_order or {})
                if str(order.get("order_id") or "").strip() != submission_order_id:
                    continue
                if str(order.get("client_order_id") or "").strip() != submission_client_order_id:
                    continue
                if str(order.get("symbol") or "").strip().upper() != symbol:
                    continue
                if str(order.get("status") or "").strip().lower() != "filled":
                    continue
                if str(order.get("side") or "").strip().lower() != expected_entry_side:
                    continue
                filled_parents.append(order)

            if not filled_parents:
                position_attributed = False
                attribution_validation_reason = "filled_parent_missing"
            elif len(filled_parents) != 1:
                position_attributed = False
                attribution_validation_reason = "filled_parent_ambiguous"
            else:
                parent = filled_parents[0]
                parent_quantity = _as_float(parent.get("filled_quantity"), 0.0)
                parent_fill_price = _as_float(parent.get("average_fill_price"), 0.0)
                parent_fill_timestamp = _as_datetime(parent.get("filled_at"))
                fill_price_tolerance = max(0.01, parent_fill_price * 0.0001)
                if (
                    parent_quantity <= 0
                    or abs(parent_quantity - absolute_quantity) > quantity_tolerance
                    or abs(parent_quantity - recorded_quantity) > quantity_tolerance
                ):
                    position_attributed = False
                    attribution_validation_reason = "filled_parent_quantity_mismatch"
                elif (
                    parent_fill_price <= 0
                    or abs(parent_fill_price - entry_price) > fill_price_tolerance
                ):
                    position_attributed = False
                    attribution_validation_reason = "position_entry_price_mismatch"
                elif parent_fill_timestamp is None or parent_fill_timestamp > observed_at:
                    position_attributed = False
                    attribution_validation_reason = "filled_parent_timestamp_invalid"
                else:
                    entry_timestamp = parent_fill_timestamp
                    entry_timestamp_source = "BROKER_FILLED_PARENT"

        current_protective_stop = 0.0
        protective_stop_source = "UNVERIFIED"
        protective_stop_order_id = ""
        protective_stop_submitted_at = ""
        bracket_verified = False
        if position_attributed:
            expected_side = "buy" if side == "SHORT" else "sell"
            expected_intent = "buy_to_close" if side == "SHORT" else "sell_to_close"
            stable_statuses = {"accepted", "new", "held", "pending_new", "partially_filled"}
            exact_legs = []
            for raw_order in list(open_orders or []):
                order = dict(raw_order or {})
                if str(order.get("status") or "").strip().lower() not in stable_statuses:
                    continue
                if str(order.get("symbol") or "").strip().upper() != symbol:
                    continue
                if str(order.get("parent_order_id") or "").strip() != submission_order_id:
                    continue
                if (
                    str(order.get("parent_client_order_id") or "").strip()
                    != submission_client_order_id
                ):
                    continue
                if str(order.get("side") or "").strip().lower() != expected_side:
                    continue
                position_intent = str(order.get("position_intent") or "").strip().lower()
                if position_intent and position_intent != expected_intent:
                    continue
                exact_legs.append(order)

            stop_legs = [
                order
                for order in exact_legs
                if str(order.get("order_type") or "").strip().lower() == "stop"
                and _as_float(order.get("stop_price"), 0.0) > 0
                and _as_float(order.get("filled_quantity"), 0.0) <= quantity_tolerance
            ]
            target_legs = [
                order
                for order in exact_legs
                if str(order.get("order_type") or "").strip().lower() == "limit"
                and _as_float(order.get("limit_price"), 0.0) > 0
            ]

            def remaining_quantity(order: Mapping[str, Any]) -> float:
                return max(
                    _as_float(order.get("requested_quantity"), 0.0)
                    - _as_float(order.get("filled_quantity"), 0.0),
                    0.0,
                )

            if not exact_legs or not stop_legs or not target_legs:
                position_attributed = False
                attribution_validation_reason = "broker_protection_missing"
            elif len(stop_legs) != 1 or len(target_legs) != 1:
                position_attributed = False
                attribution_validation_reason = "broker_protection_ambiguous"
            elif (
                abs(remaining_quantity(stop_legs[0]) - absolute_quantity) > quantity_tolerance
                or abs(remaining_quantity(target_legs[0]) - absolute_quantity) > quantity_tolerance
            ):
                position_attributed = False
                attribution_validation_reason = "broker_protection_quantity_mismatch"
            else:
                bracket_verified = True
                current_protective_stop = _as_float(stop_legs[0].get("stop_price"), 0.0)
                protective_stop_source = "BROKER_OPEN_STOP_LEG"
                protective_stop_order_id = str(stop_legs[0].get("order_id") or "")
                protective_stop_submitted_at = str(
                    stop_legs[0].get("submitted_at")
                    or stop_legs[0].get("updated_at")
                    or ""
                )

        path_rows = []
        for raw_row in list(price_paths.get(symbol) or []):
            if not isinstance(raw_row, dict):
                continue
            row = dict(raw_row)
            observation_timestamp = _as_datetime(row.get("timestamp") or row.get("date"))
            if entry_timestamp is None:
                continue
            if observation_timestamp is None or observation_timestamp < entry_timestamp:
                continue
            path_rows.append(row)
        if side == "SHORT":
            path_prices = [_as_float(row.get("low"), 0.0) for row in path_rows]
            return_percent = ((entry_price - current_price) / entry_price) * 100.0
            positive_prices = [price for price in path_prices if price > 0]
            peak_return = max(
                [return_percent]
                + [((entry_price - price) / entry_price) * 100.0 for price in positive_prices]
            )
        else:
            path_prices = [_as_float(row.get("high"), 0.0) for row in path_rows]
            return_percent = ((current_price - entry_price) / entry_price) * 100.0
            peak_return = max(
                [return_percent]
                + [((price - entry_price) / entry_price) * 100.0 for price in path_prices if price > 0]
            )
        context = {
            "bot_entry_confirmed": position_attributed,
            "bot_entry_attributed": position_attributed,
            "attribution_checked": True,
            "entry_timestamp": entry_timestamp.isoformat() if entry_timestamp is not None else None,
            "stop_price": submission.get("stop_price"),
            "target_price": submission.get("target_price"),
            "strategy": submission,
        }
        decision = evaluate_stock_exit(
            strategy_id=str(submission.get("strategy_id") or ""),
            position_side=side,
            return_percent=return_percent,
            entry_price=entry_price,
            current_price=current_price,
            entry_context=context,
            current_timestamp=observed_at,
            peak_return_percent=peak_return,
            legacy_stop_loss_percent=settings.stop_loss_percent,
            legacy_take_profit_percent=settings.take_profit_percent,
            # PAPER selects the strategy-specific model; this result is then
            # explicitly quarantined as shadow-only below.
            trading_mode="PAPER",
        )
        trailing_shadow_enabled = bool(settings.trailing_stop_ratchet_shadow_enabled)
        proposed_protective_stop = _as_float(decision.get("trailing_stop_price"), 0.0)
        trailing_activation_reached = bool(decision.get("trailing_activation_reached"))
        would_trigger_exit_now = bool(decision.get("trailing_stop_triggered"))
        would_tighten_protective_stop = False
        trailing_action = "INELIGIBLE"
        if not trailing_shadow_enabled:
            trailing_action = "DISABLED"
        elif not position_attributed:
            trailing_action = (
                "BROKER_PROTECTION_UNVERIFIED"
                if attribution_validation_reason.startswith("broker_protection_")
                else "POSITION_ATTRIBUTION_UNVERIFIED"
            )
        elif not bool(decision.get("trailing_eligible")):
            trailing_action = "INELIGIBLE"
        elif not trailing_activation_reached:
            trailing_action = "NOT_ACTIVATED"
        elif would_trigger_exit_now:
            trailing_action = "EXIT_THRESHOLD_CROSSED"
        elif current_protective_stop <= 0:
            trailing_action = "MISSING_PROTECTIVE_STOP_REFERENCE"
        elif side == "SHORT":
            would_tighten_protective_stop = bool(
                0 < current_price < proposed_protective_stop < current_protective_stop
            )
            trailing_action = "RATCHET_STOP_DOWN" if would_tighten_protective_stop else "NO_STOP_IMPROVEMENT"
        else:
            would_tighten_protective_stop = bool(
                current_protective_stop < proposed_protective_stop < current_price
            )
            trailing_action = "RATCHET_STOP_UP" if would_tighten_protective_stop else "NO_STOP_IMPROVEMENT"
        reviews.append(
            {
                "symbol": symbol,
                "mode": "SHADOW_ONLY",
                "actionable": False,
                "orders_changed": False,
                "parent_order_id": submission_order_id or None,
                "parent_client_order_id": submission_client_order_id or None,
                "filled_parent_verified": bool(
                    position_attributed and entry_timestamp_source == "BROKER_FILLED_PARENT"
                ),
                "position_side": side,
                "entry_price": round(entry_price, 8),
                "entry_quantity": round(absolute_quantity, 10),
                "current_price": round(current_price, 8),
                "return_percent": round(return_percent, 6),
                "peak_return_percent": decision.get("peak_return_percent"),
                "post_entry_price_observation_count": len(path_rows),
                "recommendation": decision.get("recommendation"),
                "exit_reason": decision.get("exit_reason"),
                "profile_id": decision.get("profile_id"),
                "strategy_id": decision.get("strategy_id"),
                "attribution_reason": decision.get("attribution_reason"),
                "attribution_validation_reason": attribution_validation_reason,
                "bot_position_attributed": position_attributed,
                "broker_bracket_verified": bracket_verified,
                "entry_fill_timestamp": (
                    entry_timestamp.isoformat() if entry_timestamp is not None else None
                ),
                "entry_timestamp_source": entry_timestamp_source,
                "trailing_shadow_enabled": trailing_shadow_enabled,
                "trailing_activation_percent": decision.get("trailing_activation_percent"),
                "trailing_distance_percent": decision.get("trailing_distance_percent"),
                "trailing_activation_reached": trailing_activation_reached,
                "locked_profit_percent": decision.get("trailing_stop_return_percent"),
                "recorded_entry_stop_price": (
                    round(recorded_stop_price, 8) if recorded_stop_price > 0 else None
                ),
                "protective_stop_source": protective_stop_source,
                "protective_stop_order_id": protective_stop_order_id or None,
                "protective_stop_submitted_at": protective_stop_submitted_at or None,
                "current_protective_stop_price": (
                    round(current_protective_stop, 8) if current_protective_stop > 0 else None
                ),
                "proposed_protective_stop_price": (
                    round(proposed_protective_stop, 8)
                    if trailing_shadow_enabled and proposed_protective_stop > 0
                    else None
                ),
                "would_tighten_protective_stop": (
                    trailing_shadow_enabled and would_tighten_protective_stop
                ),
                "would_raise_protective_stop": (
                    trailing_shadow_enabled and side == "LONG" and would_tighten_protective_stop
                ),
                "would_trigger_exit_now": (
                    trailing_shadow_enabled and would_trigger_exit_now
                ),
                "trailing_action": trailing_action,
                "validation_required_before_enforcement": True,
            }
        )
    return reviews


def apply_verified_live_stop_ratchets(
    broker: Any,
    reviews: list[Mapping[str, Any]],
    *,
    settings: LiveRiskSettings,
    market_is_open: bool,
    observed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Tighten verified broker stops; never loosen, cancel, or directly close."""
    if not settings.trailing_stop_ratchet_enabled:
        return []
    actions: list[dict[str, Any]] = []
    if not market_is_open:
        return [
            {
                "status": "deferred",
                "reason": "regular_market_closed",
                "orders_changed": False,
            }
        ]

    for raw_review in list(reviews or []):
        review = dict(raw_review or {})
        if not (
            review.get("bot_position_attributed") is True
            and review.get("filled_parent_verified") is True
            and review.get("broker_bracket_verified") is True
            and str(review.get("protective_stop_source") or "").strip().upper()
            == "BROKER_OPEN_STOP_LEG"
            and str(review.get("position_side") or "").strip().upper() == "LONG"
            and review.get("would_tighten_protective_stop") is True
        ):
            continue
        symbol = str(review.get("symbol") or "").strip().upper()
        stop_order_id = str(review.get("protective_stop_order_id") or "").strip()
        current_stop = _as_float(review.get("current_protective_stop_price"), 0.0)
        proposed_stop = _as_float(review.get("proposed_protective_stop_price"), 0.0)
        current_price = _as_float(review.get("current_price"), 0.0)
        if not symbol or not stop_order_id or not 0 < current_stop < proposed_stop < current_price:
            actions.append(
                {
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "verified_stop_ratchet_inputs_invalid",
                    "orders_changed": False,
                }
            )
            continue
        improvement = proposed_stop - current_stop
        minimum_improvement = max(
            float(settings.trailing_stop_ratchet_minimum_improvement_dollars),
            current_stop
            * float(settings.trailing_stop_ratchet_minimum_improvement_percent)
            / 100.0,
        )
        if improvement + 1e-9 < minimum_improvement:
            actions.append(
                {
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "stop_improvement_below_minimum",
                    "previous_stop_price": round(current_stop, 8),
                    "proposed_stop_price": round(proposed_stop, 8),
                    "minimum_improvement": round(minimum_improvement, 8),
                    "orders_changed": False,
                }
            )
            continue
        stop_submitted_at = _as_datetime(review.get("protective_stop_submitted_at"))
        effective_observed_at = observed_at or datetime.now(timezone.utc)
        if effective_observed_at.tzinfo is None:
            effective_observed_at = effective_observed_at.replace(tzinfo=timezone.utc)
        cooldown_seconds = float(settings.trailing_stop_ratchet_cooldown_minutes) * 60.0
        urgent_improvement = improvement + 1e-9 >= current_stop * 0.01
        if (
            stop_submitted_at is not None
            and stop_submitted_at <= effective_observed_at
            and (effective_observed_at - stop_submitted_at).total_seconds() < cooldown_seconds
            and not urgent_improvement
        ):
            actions.append(
                {
                    "symbol": symbol,
                    "status": "deferred",
                    "reason": "stop_ratchet_cooldown_active",
                    "previous_stop_price": round(current_stop, 8),
                    "proposed_stop_price": round(proposed_stop, 8),
                    "cooldown_minutes": settings.trailing_stop_ratchet_cooldown_minutes,
                    "orders_changed": False,
                }
            )
            continue
        try:
            broker_result = dict(
                broker.tighten_protective_stop(
                    symbol=symbol,
                    stop_order_id=stop_order_id,
                    current_stop_price=current_stop,
                    proposed_stop_price=proposed_stop,
                    current_market_price=current_price,
                )
                or {}
            )
            status = str(broker_result.get("status") or "unknown").strip().lower()
            changed = status not in {
                "",
                "unknown",
                "rejected",
                "canceled",
                "cancelled",
                "expired",
                "replacement_blocked_by_config",
            }
            actions.append(
                {
                    "symbol": symbol,
                    "status": "ratcheted" if changed else "not_changed",
                    "reason": "strategy_profit_trailing_stop",
                    "previous_stop_price": round(current_stop, 8),
                    "new_stop_price": round(proposed_stop, 8),
                    "orders_changed": changed,
                    "broker_order": broker_result,
                }
            )
        except Exception as exc:
            # The original broker stop remains in force if replacement fails.
            actions.append(
                {
                    "symbol": symbol,
                    "status": "failed_safe",
                    "reason": "protective_stop_replacement_failed",
                    "error_type": type(exc).__name__,
                    "orders_changed": False,
                }
            )
    return actions


def select_live_candidate(
    scan_payload: Mapping[str, Any],
    *,
    settings: LiveRiskSettings,
    positions: Mapping[str, Mapping[str, Any]],
    maximum_notional: float,
    account_equity: float,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    funnel = diagnostics if diagnostics is not None else {}
    funnel.update(
        {
            "ranked_candidate_count": len(list(scan_payload.get("ranked_candidates") or [])),
            "evaluated_count": 0,
            "rejection_counts": {},
            "near_misses": [],
            "selected_symbol": "",
        }
    )

    def reject(symbol: str, reason: str, **details: Any) -> None:
        counts = dict(funnel.get("rejection_counts") or {})
        counts[reason] = int(counts.get(reason, 0)) + 1
        funnel["rejection_counts"] = counts
        near_misses = list(funnel.get("near_misses") or [])
        if len(near_misses) < 20:
            near_misses.append({"symbol": symbol, "reason": reason, **details})
        funnel["near_misses"] = near_misses

    held = {str(symbol).upper() for symbol in dict(positions or {})}
    allowed = set(settings.allowed_symbols)
    for raw_candidate in list(scan_payload.get("ranked_candidates") or []):
        candidate = dict(raw_candidate or {})
        symbol = str(candidate.get("symbol") or "").upper()
        price = _as_float(candidate.get("latest_price"), 0.0)
        funnel["evaluated_count"] = int(funnel.get("evaluated_count") or 0) + 1
        if not symbol:
            reject("", "missing_symbol")
            continue
        if symbol not in allowed:
            reject(symbol, "symbol_not_in_live_universe")
            continue
        if symbol in held:
            reject(symbol, "position_already_held")
            continue
        if price <= 0:
            reject(symbol, "invalid_latest_price", latest_price=price)
            continue
        signals = [dict(item or {}) for item in evaluate_all_strategies(candidate)]
        regime = str(
            ((candidate.get("quantum_score") or {}).get("market_regime"))
            or next((item.get("market_regime") for item in signals if item.get("market_regime")), "unknown")
        ).strip().lower()
        routed_ids = set(REGIME_STRATEGY_ROUTES.get(regime, ()))
        eligible_signals = [
            item
            for item in signals
            if str(item.get("strategy_id") or "") in routed_ids
            and str(item.get("signal") or "").upper() == "BUY"
            and _as_float(item.get("strategy_score"), 0.0) >= settings.minimum_strategy_score
            and _as_float(item.get("confidence"), 0.0) >= settings.minimum_confidence
            and str(item.get("data_quality_status") or "").lower() in {"ok", "good"}
        ]
        if not eligible_signals:
            signal_reasons: set[str] = set()
            if not signals:
                signal_reasons.add("no_strategy_signals")
            if not routed_ids:
                signal_reasons.add("unsupported_market_regime")
            for item in signals:
                if str(item.get("strategy_id") or "") not in routed_ids:
                    signal_reasons.add("strategy_not_routed_for_regime")
                if str(item.get("signal") or "").upper() != "BUY":
                    signal_reasons.add("strategy_signal_not_buy")
                if _as_float(item.get("strategy_score"), 0.0) < settings.minimum_strategy_score:
                    signal_reasons.add("strategy_score_below_minimum")
                if _as_float(item.get("confidence"), 0.0) < settings.minimum_confidence:
                    signal_reasons.add("strategy_confidence_below_minimum")
                if str(item.get("data_quality_status") or "").lower() not in {"ok", "good"}:
                    signal_reasons.add("strategy_data_quality_not_acceptable")
            reject(
                symbol,
                "no_eligible_strategy_signal",
                regime=regime,
                signal_reasons=sorted(signal_reasons),
                best_strategy_score=max((_as_float(item.get("strategy_score"), 0.0) for item in signals), default=0.0),
                best_confidence=max((_as_float(item.get("confidence"), 0.0) for item in signals), default=0.0),
            )
            continue
        signal = max(
            eligible_signals,
            key=lambda item: (
                _as_float(item.get("strategy_score"), 0.0),
                _as_float(item.get("confidence"), 0.0),
            ),
        )
        signal["ensemble_route"] = regime
        signal["eligible_strategy_ids"] = sorted(
            str(item.get("strategy_id") or "") for item in eligible_signals
        )
        supporting = dict(signal.get("supporting_factors") or {})
        confirmations = dict(supporting.get("confirmations") or {})
        distinct_confirmation_count = sum(1 for passed in confirmations.values() if passed)
        if distinct_confirmation_count < 2:
            reject(
                symbol,
                "insufficient_independent_confirmations",
                confirmation_count=distinct_confirmation_count,
                required_confirmation_count=2,
            )
            continue
        stop = _as_float(signal.get("stop") or signal.get("stop_price"), 0.0)
        if not 0 < stop < price:
            stop = round(price * (1.0 - settings.stop_loss_percent / 100.0), 2)
        target = _as_float(signal.get("target") or signal.get("target_price"), 0.0)
        if target <= price:
            target = round(price * (1.0 + settings.take_profit_percent / 100.0), 2)

        correlation = assess_symbol_correlation(
            symbol,
            sorted(held),
            dict(scan_payload.get("price_history_by_symbol") or {}),
            CorrelationPolicy(
                lookback_days=settings.correlation_lookback_days,
                min_overlap_days=settings.correlation_min_overlap_days,
                max_correlation=settings.maximum_pair_correlation,
                allocation_reduction_factor=1.0,
            ),
        )
        if held and int(correlation.get("insufficient_pair_count") or 0) > 0:
            reject(
                symbol,
                "correlation_history_incomplete",
                insufficient_pair_count=int(correlation.get("insufficient_pair_count") or 0),
            )
            continue
        high_peers = {
            str(row.get("peer_symbol") or "").upper()
            for row in list(correlation.get("high_correlation_pairs") or [])
        }
        correlated_exposure = sum(
            abs(_as_float(dict(positions.get(peer) or {}).get("market_value"), 0.0))
            for peer in high_peers
        )
        correlated_room = max(
            account_equity * settings.maximum_correlated_exposure_percent / 100.0 - correlated_exposure,
            0.0,
        )
        correlation_notional_cap = min(maximum_notional, correlated_room) if high_peers else maximum_notional
        sizing = stop_risk_position_size(
            equity=account_equity,
            entry_price=price,
            stop_price=stop,
            maximum_notional=correlation_notional_cap,
            settings=settings,
        )
        quantity = int(sizing.get("quantity") or 0)
        if quantity < 1:
            reject(
                symbol,
                "position_size_below_one_share",
                latest_price=price,
                stop_price=stop,
                maximum_notional=round(correlation_notional_cap, 6),
                allowed_risk_dollars=_as_float(sizing.get("allowed_risk_dollars"), 0.0),
            )
            continue
        signal["rule_checks"] = {
            "regime_route": str(signal.get("strategy_id") or "") in routed_ids,
            "quality_threshold": (
                _as_float(signal.get("strategy_score"), 0.0) >= settings.minimum_strategy_score
                and _as_float(signal.get("confidence"), 0.0) >= settings.minimum_confidence
            ),
            "independent_confirmations": distinct_confirmation_count >= 2,
            "risk_limit": _as_float(sizing.get("planned_risk_dollars"), 0.0) <= _as_float(sizing.get("allowed_risk_dollars"), 0.0),
            "correlation_limit": not held or int(correlation.get("insufficient_pair_count") or 0) == 0,
            "protective_exit": settings.stop_loss_percent > 0 and settings.take_profit_percent > 0,
        }
        signal["risk_sizing"] = sizing
        signal["correlation_assessment"] = correlation
        signal["correlated_exposure_before"] = round(correlated_exposure, 6)
        funnel["selected_symbol"] = symbol
        return {
            "symbol": symbol,
            "quantity": quantity,
            "reference_price": price,
            "notional": round(quantity * price, 2),
            "stop_price": stop,
            "target_price": target,
            "strategy": signal,
        }
    return None


def run_controlled_live_cycle(
    *,
    environ: Mapping[str, str] | None = None,
    settings: LiveRiskSettings | None = None,
    broker: Any | None = None,
    scanner: Callable[..., dict[str, Any]] = run_scan,
    state_store: LiveStateStore | None = None,
    shadow_trial_ledger: Any | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    policy = settings or settings_from_environment(env)
    now = (now_provider or (lambda: datetime.now(timezone.utc)))()
    date_key = now.astimezone(timezone.utc).date().isoformat()
    store = state_store or LiveStateStore(
        env.get("LIVE_STATE_PATH", "/var/lib/quant-bot/live-micro-state.json")
    )
    shadow_summary_not_evaluated = {
        "status": "not_evaluated",
        "mode": "SHADOW_ONLY",
        "actionable": False,
        "orders_changed": False,
    }

    preflight_reasons: list[str] = []
    if str(env.get("TRADING_MODE", "")).strip().upper() != "LIVE":
        preflight_reasons.append("TRADING_MODE_must_be_LIVE")
    if not policy.enabled:
        preflight_reasons.append("live_trading_disabled")
    if not policy.order_submission_enabled:
        preflight_reasons.append("live_order_submission_disabled")
    if policy.kill_switch:
        preflight_reasons.append("live_kill_switch_active")
    if preflight_reasons:
        return {
            "status": "blocked",
            "reasons": preflight_reasons,
            "shadow_exit_trial_summary": dict(shadow_summary_not_evaluated),
            "submitted": False,
        }

    live_broker = broker or AlpacaLiveBroker(mode="LIVE", environ=env)
    account = dict(live_broker.get_account() or {})
    store.record_account_observation(account, observed_at=now)
    positions = dict(live_broker.get_positions() or {})
    store.record_position_observations(positions, observed_at=now)
    open_orders = [dict(item or {}) for item in list(live_broker.get_open_orders() or [])]
    clock = dict(live_broker.get_market_clock() or {})
    _, unreconciled_orders, unprotected_positions = _protective_order_symbols(positions, open_orders)
    orders_submitted_today = store.orders_submitted_on(date_key)
    state = store.load()
    history_limit = 500
    order_history: list[dict[str, Any]] = []
    history_error: Exception | None = None
    try:
        order_history = [
            dict(item or {})
            for item in list(live_broker.get_order_history(limit=history_limit) or [])
        ]
    except Exception as exc:
        history_error = exc
    try:
        live_exit_shadow = build_live_exit_shadow_reviews(
            positions,
            state,
            observed_at=now,
            settings=policy,
            open_orders=open_orders,
            order_history=order_history,
        )
    except Exception as exc:
        # Shadow diagnostics must never interrupt broker protection or the
        # primary live decision loop.
        live_exit_shadow = [
            {
                "mode": "SHADOW_ONLY",
                "actionable": False,
                "orders_changed": False,
                "status": "unavailable",
                "error_type": type(exc).__name__,
            }
        ]
    live_exit_actions = apply_verified_live_stop_ratchets(
        live_broker,
        live_exit_shadow,
        settings=policy,
        market_is_open=bool(clock.get("is_open")),
        observed_at=now,
    )
    try:
        ledger = shadow_trial_ledger or ShadowExitTrialLedger.from_environment(env)
        shadow_exit_trial_summary = dict(
            ledger.update(
                live_exit_shadow,
                observed_at=now,
                positions=positions,
                order_history=order_history,
            )
            or {}
        )
        shadow_exit_trial_summary.setdefault("status", "available")
        shadow_exit_trial_summary["mode"] = "SHADOW_ONLY"
        shadow_exit_trial_summary["actionable"] = False
        shadow_exit_trial_summary["orders_changed"] = False
    except Exception as exc:
        # The evidence ledger is deliberately non-authoritative. Persistence or
        # reconciliation failures must never block protection, scanning, or an
        # otherwise valid live order decision.
        shadow_exit_trial_summary = {
            "status": "unavailable",
            "mode": "SHADOW_ONLY",
            "actionable": False,
            "orders_changed": False,
            "error_type": type(exc).__name__,
        }
    try:
        if history_error is not None:
            raise history_error
        strategy_metadata: dict[str, Any] = {}
        for row in list(state.get("submissions") or []):
            for key in ("order_id", "client_order_id"):
                identifier = str(dict(row or {}).get(key) or "").strip()
                if identifier:
                    strategy_metadata[identifier] = dict(row or {})
        reconstruction = reconstruct_stock_realized_pnl(
            order_history,
            bot_orders_only=True,
            history_limit=history_limit,
            strategy_by_order_id=strategy_metadata,
        )
        history_complete = not bool(reconstruction.get("history_limit_reached")) and not bool(
            reconstruction.get("missing_timestamp_count") or reconstruction.get("unmatched_close_count")
        )
        performance_controls = evaluate_live_performance_controls(
            account,
            list(reconstruction.get("realized_events") or []),
            settings=policy,
            now=now,
            equity_history=list(state.get("equity_history") or []),
            history_available=True,
            history_complete=history_complete,
        )
    except Exception as exc:
        performance_controls = evaluate_live_performance_controls(
            account,
            [],
            settings=policy,
            now=now,
            equity_history=list(store.load().get("equity_history") or []),
            history_available=False,
            history_complete=False,
        )
        performance_controls["history_error_type"] = type(exc).__name__

    # Candidate discovery must never gate management of positions that already
    # exist.  Resolve the stock universe only after account reconciliation and
    # verified stop ratchets have run.  If the discovery endpoint is unhealthy,
    # block new entries while preserving the exit-management evidence/actions.
    try:
        policy, records = _resolve_stock_universe(live_broker, policy, env)
    except Exception as exc:
        return {
            "status": "blocked",
            "reasons": [f"stock_universe_unavailable_{type(exc).__name__}"],
            "live_exit_shadow": live_exit_shadow,
            "live_exit_actions": live_exit_actions,
            "shadow_exit_trial_summary": shadow_exit_trial_summary,
            "performance_controls": performance_controls,
            "submitted": False,
        }
    readiness = evaluate_live_readiness(
        account,
        positions,
        unreconciled_orders,
        settings=policy,
        market_is_open=bool(clock.get("is_open")),
        orders_submitted_today=orders_submitted_today,
    )
    if not performance_controls.get("approved"):
        readiness["approved"] = False
        readiness.setdefault("reasons", []).extend(list(performance_controls.get("reasons") or []))
    readiness["performance_controls"] = performance_controls
    if unprotected_positions:
        readiness["approved"] = False
        readiness.setdefault("reasons", []).append("unprotected_live_positions_require_manual_review")
        readiness["unprotected_positions"] = unprotected_positions
    if not readiness.get("approved"):
        return {
            "status": "blocked",
            "reasons": list(readiness.get("reasons") or []),
            "readiness": readiness,
            "live_exit_shadow": live_exit_shadow,
            "live_exit_actions": live_exit_actions,
            "shadow_exit_trial_summary": shadow_exit_trial_summary,
            "submitted": False,
        }

    maximum_notional = live_entry_notional(account, positions, policy)
    if maximum_notional < 1.0:
        return {
            "status": "no_trade",
            "reasons": ["available_live_notional_below_one_dollar"],
            "readiness": readiness,
            "live_exit_shadow": live_exit_shadow,
            "live_exit_actions": live_exit_actions,
            "shadow_exit_trial_summary": shadow_exit_trial_summary,
            "submitted": False,
        }

    scan_payload = scanner(records)
    store.record_market_bars(
        dict(scan_payload.get("price_history_by_symbol") or {}),
        symbols={str(symbol).upper() for symbol in positions},
    )
    candidate_funnel: dict[str, Any] = {}
    candidate = select_live_candidate(
        scan_payload,
        settings=policy,
        positions=positions,
        maximum_notional=maximum_notional,
        account_equity=_as_float(account.get("equity"), 0.0),
        diagnostics=candidate_funnel,
    )
    if candidate is None:
        return {
            "status": "no_trade",
            "reasons": ["no_eligible_risk_checked_candidate"],
            "readiness": readiness,
            "scan_summary": dict(scan_payload.get("summary") or {}),
            "candidate_funnel": candidate_funnel,
            "live_exit_shadow": live_exit_shadow,
            "live_exit_actions": live_exit_actions,
            "shadow_exit_trial_summary": shadow_exit_trial_summary,
            "submitted": False,
        }

    client_order_id = f"qtb-live-micro-{date_key.replace('-', '')}-{candidate['symbol'].lower()}"
    order = dict(
        live_broker.submit_bracket_entry(
            symbol=candidate["symbol"],
            quantity=int(candidate["quantity"]),
            reference_price=float(candidate["reference_price"]),
            stop_price=float(candidate["stop_price"]),
            target_price=float(candidate["target_price"]),
            client_order_id=client_order_id,
        )
        or {}
    )
    order_status = str(order.get("status") or "unknown").lower()
    submitted = order_status in ACCEPTED_ORDER_STATUSES
    if submitted:
        store.record_submission(order, date_key=date_key, strategy=dict(candidate.get("strategy") or {}))
    return {
        "status": "submitted" if submitted else "rejected",
        "reasons": [] if submitted else [f"broker_order_status_{order_status}"],
        "readiness": readiness,
        "candidate": candidate,
        "scan_summary": dict(scan_payload.get("summary") or {}),
        "candidate_funnel": candidate_funnel,
        "live_exit_shadow": live_exit_shadow,
        "live_exit_actions": live_exit_actions,
        "shadow_exit_trial_summary": shadow_exit_trial_summary,
        "order": order,
        "submitted": submitted,
    }


def run_forever(*, interval_seconds: int = 60) -> None:
    while True:
        try:
            result = run_controlled_live_cycle()
            LiveStateStore(
                os.getenv("LIVE_STATE_PATH", "/var/lib/quant-bot/live-micro-state.json")
            ).record_cycle_result(result)
            print(json.dumps({"event": "controlled_live_cycle", "timestamp": _utc_iso(), **result}, default=str))
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "controlled_live_cycle_error",
                        "timestamp": _utc_iso(),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            )
        time.sleep(max(int(interval_seconds), 60))


def check_live_account(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read live account state without enabling or submitting orders."""
    env = dict(os.environ if environ is None else environ)
    broker = AlpacaLiveBroker(mode="LIVE", environ=env, read_only=True)
    account = dict(broker.get_account() or {})
    account.pop("account_number", None)
    positions = dict(broker.get_positions() or {})
    open_orders = list(broker.get_open_orders() or [])
    clock = dict(broker.get_market_clock() or {})
    return {
        "status": "checked",
        "submission_enabled": False,
        "account": account,
        "position_symbols": sorted(str(symbol).upper() for symbol in positions),
        "open_order_count": len(open_orders),
        "market_is_open": bool(clock.get("is_open")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Separately gated $300 micro-live stock runner")
    parser.add_argument("--once", action="store_true", help="Run one controlled live cycle")
    parser.add_argument("--show-policy", action="store_true", help="Print non-secret live risk policy")
    parser.add_argument("--check-account", action="store_true", help="Read live account state without order permission")
    args = parser.parse_args()
    if args.show_policy:
        print(json.dumps(asdict(settings_from_environment()), indent=2, sort_keys=True))
        return
    if args.check_account:
        print(json.dumps(check_live_account(), indent=2, sort_keys=True, default=str))
        return
    if args.once:
        print(json.dumps(run_controlled_live_cycle(), indent=2, sort_keys=True, default=str))
        return
    run_forever(interval_seconds=int(os.getenv("LIVE_SCAN_INTERVAL_SECONDS", "60")))


if __name__ == "__main__":
    main()
