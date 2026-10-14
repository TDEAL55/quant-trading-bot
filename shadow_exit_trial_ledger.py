"""Durable, broker-read-only evidence for shadow exit trials.

This module deliberately has no broker imports and exposes no order mutation
methods.  Callers provide already-normalized observations and verified order
history; the ledger only writes its own JSON evidence file.  Unless a caller
supplies an explicit hypothetical fill, a triggered long stop fills at the
lower of the stop and observed trigger price, while a short fills at the
higher.  That conservative model accounts for gaps through a stop.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
DEFAULT_LEDGER_PATH = "/var/lib/quant-bot/live-shadow-exit-trials.json"
DEFAULT_LOCK_TIMEOUT_SECONDS = 2.0
DEFAULT_LOCK_POLL_SECONDS = 0.025


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _same_path_lock(path: Path) -> threading.RLock:
    """Return the process-wide lock shared by every instance for ``path``."""
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _acquire_file_lock_nonblocking(handle: Any) -> bool:
    """Try to lock one ledger lock-file handle without waiting."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _release_file_lock(handle: Any) -> None:
    """Release a lock previously acquired by ``_acquire_file_lock_nonblocking``."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ShadowExitTrialLedgerError(RuntimeError):
    """Raised when durable shadow-trial evidence cannot be read or written."""


class ShadowExitTrialValidationError(ValueError):
    """Raised when an observation is not safe to associate with a trial."""


def make_trial_key(parent_order_id: str, parent_client_order_id: str) -> str:
    """Return a stable key tied to both verified parent-order identifiers."""
    order_id = _required_text(parent_order_id, "parent_order_id")
    client_order_id = _required_text(parent_client_order_id, "parent_client_order_id")
    identity = f"{order_id}\0{client_order_id}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ShadowExitTrialValidationError(f"{field} is required")
    return text


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ShadowExitTrialValidationError(f"{field} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ShadowExitTrialValidationError(
            f"{field} must be a positive finite number"
        ) from exc
    if not math.isfinite(number) or number <= 0:
        raise ShadowExitTrialValidationError(f"{field} must be a positive finite number")
    return number


def _optional_positive_number(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    return _positive_number(value, field)


def _normalized_timestamp(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ShadowExitTrialValidationError("timestamp must not be blank")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ShadowExitTrialValidationError(f"invalid ISO timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _rounded(value: float) -> float:
    return round(float(value), 8)


def _return_and_pnl(side: str, entry_price: float, exit_price: float, quantity: float) -> tuple[float, float]:
    direction = 1.0 if side == "LONG" else -1.0
    price_change = (exit_price - entry_price) * direction
    return _rounded((price_change / entry_price) * 100.0), _rounded(price_change * quantity)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_positive(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            return number
    return None


class ShadowExitTrialLedger:
    """Persist and summarize hypothetical stop-ratchet outcomes.

    A trial can only be created from a caller-verified, filled parent order.
    Once recorded, activation is sticky, stops may only tighten, and the first
    hypothetical trigger and first verified actual close are immutable.  A
    verified broker stop seeds the counterfactual floor/ceiling, but cannot
    trigger the trial before the shadow trailing strategy activates.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        lock_poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    ):
        self.path = Path(path)
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = _same_path_lock(self.path)
        self._lock_timeout_seconds = max(0.0, float(lock_timeout_seconds))
        self._lock_poll_seconds = max(0.001, float(lock_poll_seconds))

    @contextmanager
    def _exclusive_access(self):
        """Bounded same-path and inter-process exclusion for a whole transaction."""
        deadline = time.monotonic() + self._lock_timeout_seconds
        same_process_acquired = self._lock.acquire(timeout=self._lock_timeout_seconds)
        if not same_process_acquired:
            raise ShadowExitTrialLedgerError(
                f"timed out acquiring in-process shadow trial ledger lock: {self.path}"
            )
        try:
            try:
                self._lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self._lock_path.open("a+b")
                if os.name == "nt":
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
            except OSError as exc:
                raise ShadowExitTrialLedgerError(
                    f"cannot prepare shadow trial ledger lock: {self._lock_path}"
                ) from exc

            acquired = False
            try:
                while True:
                    try:
                        acquired = _acquire_file_lock_nonblocking(handle)
                    except OSError as exc:
                        raise ShadowExitTrialLedgerError(
                            f"cannot acquire shadow trial ledger lock: {self._lock_path}"
                        ) from exc
                    if acquired:
                        break
                    if time.monotonic() >= deadline:
                        raise ShadowExitTrialLedgerError(
                            f"timed out acquiring shadow trial ledger lock: {self._lock_path}"
                        )
                    time.sleep(
                        min(self._lock_poll_seconds, max(0.0, deadline - time.monotonic()))
                    )
                yield
            finally:
                if acquired:
                    try:
                        _release_file_lock(handle)
                    except OSError:
                        # The protected work already completed. Closing the
                        # descriptor still releases an OS-owned advisory lock.
                        pass
                handle.close()
        finally:
            self._lock.release()

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ShadowExitTrialLedger":
        env = os.environ if environ is None else environ
        return cls(env.get("SHADOW_EXIT_TRIAL_LEDGER_PATH", DEFAULT_LEDGER_PATH))

    def record_observation(
        self,
        *,
        parent_order_id: str,
        parent_client_order_id: str,
        filled_parent_verified: bool,
        symbol: str,
        position_side: str,
        entry_price: float,
        entry_quantity: float,
        observed_price: float,
        observed_at: datetime | str | None,
        strategy_id: str = "",
        activation_reached: bool = False,
        proposed_shadow_stop: float | None = None,
        current_protective_stop_price: float | None = None,
        protective_stop_verified: bool = False,
        would_tighten_protective_stop: bool | None = None,
        observed_low: float | None = None,
        observed_high: float | None = None,
        hypothetical_triggered: bool | None = None,
        hypothetical_fill_price: float | None = None,
        actual_close: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one verified observation and return a detached trial copy."""
        with self._exclusive_access():
            state = self._load()
            trial, changed, counted = self._record_observation_in_state(
                state,
                parent_order_id=parent_order_id,
                parent_client_order_id=parent_client_order_id,
                filled_parent_verified=filled_parent_verified,
                symbol=symbol,
                position_side=position_side,
                entry_price=entry_price,
                entry_quantity=entry_quantity,
                observed_price=observed_price,
                observed_at=observed_at,
                strategy_id=strategy_id,
                activation_reached=activation_reached,
                proposed_shadow_stop=proposed_shadow_stop,
                current_protective_stop_price=current_protective_stop_price,
                protective_stop_verified=protective_stop_verified,
                would_tighten_protective_stop=would_tighten_protective_stop,
                observed_low=observed_low,
                observed_high=observed_high,
                hypothetical_triggered=hypothetical_triggered,
                hypothetical_fill_price=hypothetical_fill_price,
            )
            if counted:
                state["observation_counts"]["verified"] += 1
                changed = True
            if actual_close is not None:
                changed = self._reconcile_actual_close_in_state(
                    state,
                    parent_order_id=parent_order_id,
                    parent_client_order_id=parent_client_order_id,
                    actual_close_verified=bool(actual_close.get("verified", False)),
                    exit_price=actual_close.get("exit_price"),
                    exit_quantity=actual_close.get("exit_quantity"),
                    closed_at=actual_close.get("closed_at"),
                    exit_order_id=actual_close.get("exit_order_id", ""),
                    exit_client_order_id=actual_close.get("exit_client_order_id", ""),
                ) or changed
                trial = state["trials"][make_trial_key(parent_order_id, parent_client_order_id)]
            if changed:
                self._save(state)
            return deepcopy(trial)

    def reconcile_actual_close(
        self,
        *,
        parent_order_id: str,
        parent_client_order_id: str,
        actual_close_verified: bool,
        exit_price: float,
        exit_quantity: float,
        closed_at: datetime | str | None,
        exit_order_id: str = "",
        exit_client_order_id: str = "",
    ) -> dict[str, Any]:
        """Attach a verified actual close without altering any broker state."""
        with self._exclusive_access():
            state = self._load()
            changed = self._reconcile_actual_close_in_state(
                state,
                parent_order_id=parent_order_id,
                parent_client_order_id=parent_client_order_id,
                actual_close_verified=actual_close_verified,
                exit_price=exit_price,
                exit_quantity=exit_quantity,
                closed_at=closed_at,
                exit_order_id=exit_order_id,
                exit_client_order_id=exit_client_order_id,
            )
            if changed:
                self._save(state)
            return deepcopy(state["trials"][make_trial_key(parent_order_id, parent_client_order_id)])

    def update(
        self,
        reviews: Sequence[Mapping[str, Any]],
        *,
        observed_at: datetime | str,
        positions: Mapping[str, Mapping[str, Any]],
        order_history: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Apply a runner observation batch and return the durable summary.

        Reviews that do not explicitly prove a filled parent identity are only
        counted as unverified coverage; they never create or mutate a trial.
        Filled closing orders are reconciled only when exactly one child order
        matches both parent identifiers, symbol, side, and full quantity.
        """
        timestamp = _normalized_timestamp(observed_at)
        with self._exclusive_access():
            state = self._load()
            changed = False
            normalized_positions = {
                str(symbol or "").strip().upper(): _as_mapping(position)
                for symbol, position in dict(positions or {}).items()
            }
            for raw_review in list(reviews or []):
                review = _as_mapping(raw_review)
                symbol = str(review.get("symbol") or "").strip().upper()
                position = normalized_positions.get(symbol, {})
                parent_order_id = str(review.get("parent_order_id") or "").strip()
                parent_client_order_id = str(review.get("parent_client_order_id") or "").strip()
                parent_verified = self._review_parent_is_verified(review)
                if not (parent_verified and parent_order_id and parent_client_order_id):
                    state["observation_counts"]["unverified"] += 1
                    changed = True
                    continue

                quantity_value = review.get("entry_quantity")
                if quantity_value in (None, ""):
                    quantity_value = position.get("quantity", position.get("qty"))
                try:
                    signed_quantity = float(quantity_value or 0.0)
                except (TypeError, ValueError):
                    signed_quantity = 0.0
                quantity = abs(signed_quantity)
                side = str(review.get("position_side") or "").strip().upper()
                if not side:
                    side = "SHORT" if signed_quantity < 0 else "LONG"
                entry_price = review.get("entry_price")
                if entry_price in (None, ""):
                    entry_price = (
                        position.get("avg_entry_price")
                        or position.get("average_entry_price")
                        or position.get("avg_price")
                    )
                current_price = review.get("current_price")
                if current_price in (None, ""):
                    current_price = position.get("current_price") or position.get("market_price")
                try:
                    _, trial_changed, counted = self._record_observation_in_state(
                        state,
                        parent_order_id=parent_order_id,
                        parent_client_order_id=parent_client_order_id,
                        filled_parent_verified=True,
                        symbol=symbol,
                        position_side=side,
                        entry_price=entry_price,
                        entry_quantity=quantity,
                        observed_price=current_price,
                        observed_at=timestamp,
                        strategy_id=str(review.get("strategy_id") or ""),
                        activation_reached=bool(review.get("trailing_activation_reached")),
                        proposed_shadow_stop=review.get("proposed_protective_stop_price"),
                        current_protective_stop_price=review.get(
                            "current_protective_stop_price"
                        ),
                        protective_stop_verified=(
                            review.get("broker_bracket_verified") is True
                            and str(review.get("protective_stop_source") or "")
                            .strip()
                            .upper()
                            == "BROKER_OPEN_STOP_LEG"
                        ),
                        would_tighten_protective_stop=(
                            review.get("would_tighten_protective_stop") is True
                        ),
                        hypothetical_triggered=bool(review.get("would_trigger_exit_now")),
                    )
                except ShadowExitTrialValidationError:
                    state["observation_counts"]["unverified"] += 1
                    changed = True
                    continue
                if counted:
                    state["observation_counts"]["verified"] += 1
                    changed = True
                changed = trial_changed or changed

            changed = self._reconcile_filled_closes(
                state,
                positions=normalized_positions,
                order_history=list(order_history or []),
            ) or changed
            state["last_update_at"] = timestamp
            changed = True
            if changed:
                self._save(state)
            return self._summary_from_state(state)

    def get_trial(self, parent_order_id: str, parent_client_order_id: str) -> dict[str, Any] | None:
        with self._exclusive_access():
            state = self._load()
            trial = state["trials"].get(make_trial_key(parent_order_id, parent_client_order_id))
            return deepcopy(trial) if trial is not None else None

    def list_trials(self) -> list[dict[str, Any]]:
        with self._exclusive_access():
            state = self._load()
            return [deepcopy(state["trials"][key]) for key in sorted(state["trials"])]

    def summary(self) -> dict[str, Any]:
        with self._exclusive_access():
            return self._summary_from_state(self._load())

    @staticmethod
    def _review_parent_is_verified(review: Mapping[str, Any]) -> bool:
        if "filled_parent_verified" in review:
            return review.get("filled_parent_verified") is True
        return bool(review.get("bot_position_attributed")) and (
            str(review.get("entry_timestamp_source") or "").strip().upper()
            == "BROKER_FILLED_PARENT"
        )

    def _record_observation_in_state(
        self,
        state: dict[str, Any],
        *,
        parent_order_id: str,
        parent_client_order_id: str,
        filled_parent_verified: bool,
        symbol: str,
        position_side: str,
        entry_price: Any,
        entry_quantity: Any,
        observed_price: Any,
        observed_at: datetime | str | None,
        strategy_id: str,
        activation_reached: bool,
        proposed_shadow_stop: Any,
        current_protective_stop_price: Any = None,
        protective_stop_verified: bool = False,
        would_tighten_protective_stop: bool | None = None,
        observed_low: Any = None,
        observed_high: Any = None,
        hypothetical_triggered: bool | None = None,
        hypothetical_fill_price: Any = None,
    ) -> tuple[dict[str, Any], bool, bool]:
        if filled_parent_verified is not True:
            raise ShadowExitTrialValidationError("filled parent order must be explicitly verified")
        order_id = _required_text(parent_order_id, "parent_order_id")
        client_order_id = _required_text(parent_client_order_id, "parent_client_order_id")
        normalized_symbol = _required_text(symbol, "symbol").upper()
        side = _required_text(position_side, "position_side").upper()
        if side not in {"LONG", "SHORT"}:
            raise ShadowExitTrialValidationError("position_side must be LONG or SHORT")
        entry = _positive_number(entry_price, "entry_price")
        quantity = _positive_number(entry_quantity, "entry_quantity")
        price = _positive_number(observed_price, "observed_price")
        low = _optional_positive_number(observed_low, "observed_low")
        high = _optional_positive_number(observed_high, "observed_high")
        proposed_stop = _optional_positive_number(proposed_shadow_stop, "proposed_shadow_stop")
        broker_stop = (
            _positive_number(
                current_protective_stop_price, "current_protective_stop_price"
            )
            if protective_stop_verified is True
            else None
        )
        fill_price = _optional_positive_number(hypothetical_fill_price, "hypothetical_fill_price")
        timestamp = _normalized_timestamp(observed_at)
        key = make_trial_key(order_id, client_order_id)
        trial = state["trials"].get(key)
        changed = False
        if trial is None:
            trial = {
                "trial_key": key,
                "parent_order_id": order_id,
                "parent_client_order_id": client_order_id,
                "symbol": normalized_symbol,
                "position_side": side,
                "strategy_id": str(strategy_id or "").strip(),
                "entry_price": _rounded(entry),
                "entry_quantity": _rounded(quantity),
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_observed_at": None,
                "observation_count": 0,
                "activation_reached": False,
                "activated_at": None,
                "activation_observed_price": None,
                "shadow_stop_price": None,
                "shadow_stop_updated_at": None,
                "latest_verified_broker_stop_price": None,
                "latest_verified_broker_stop_observed_at": None,
                "first_hypothetical_trigger": None,
                "simulated_exit_price": None,
                "simulated_fill_model": None,
                "simulated_return_percent": None,
                "simulated_pnl": None,
                "actual_close": None,
                "actual_exit_order_id": None,
                "actual_exit_client_order_id": None,
                "actual_exit_price": None,
                "actual_exit_quantity": None,
                "actual_closed_at": None,
                "actual_return_percent": None,
                "actual_pnl": None,
                "actual_minus_simulated_return_percent": None,
                "actual_minus_simulated_pnl": None,
                "shadow_minus_actual_return_percent": None,
                "shadow_minus_actual_pnl": None,
                "reconciliation_status": "OPEN",
            }
            state["trials"][key] = trial
            changed = True
        else:
            self._validate_identity_metadata(
                trial,
                symbol=normalized_symbol,
                side=side,
                entry_price=entry,
                entry_quantity=quantity,
            )
            existing_strategy = str(trial.get("strategy_id") or "")
            supplied_strategy = str(strategy_id or "").strip()
            if not existing_strategy and supplied_strategy:
                trial["strategy_id"] = supplied_strategy
                changed = True
            elif existing_strategy and supplied_strategy and existing_strategy != supplied_strategy:
                raise ShadowExitTrialValidationError("strategy_id conflicts with persisted trial")

        counted = trial.get("last_observed_at") != timestamp
        if counted:
            trial["last_observed_at"] = timestamp
            trial["observation_count"] = int(trial.get("observation_count") or 0) + 1
            changed = True

        if activation_reached and not trial["activation_reached"]:
            trial["activation_reached"] = True
            trial["activated_at"] = timestamp
            trial["activation_observed_price"] = _rounded(price)
            changed = True

        if broker_stop is not None:
            if trial.get("latest_verified_broker_stop_price") != _rounded(broker_stop):
                trial["latest_verified_broker_stop_price"] = _rounded(broker_stop)
                trial["latest_verified_broker_stop_observed_at"] = timestamp
                changed = True
            prior_stop = trial.get("shadow_stop_price")
            broker_tightens = (
                prior_stop is None
                or (side == "LONG" and broker_stop > float(prior_stop))
                or (side == "SHORT" and broker_stop < float(prior_stop))
            )
            if broker_tightens and trial.get("first_hypothetical_trigger") is None:
                trial["shadow_stop_price"] = _rounded(broker_stop)
                trial["shadow_stop_updated_at"] = timestamp
                changed = True

        if (
            trial["activation_reached"]
            and trial.get("first_hypothetical_trigger") is None
            and proposed_stop is not None
            and (
                would_tighten_protective_stop is not False
                or hypothetical_triggered is True
            )
        ):
            prior_stop = trial.get("shadow_stop_price")
            tightens = (
                prior_stop is None
                or (side == "LONG" and proposed_stop > float(prior_stop))
                or (side == "SHORT" and proposed_stop < float(prior_stop))
            )
            if tightens:
                trial["shadow_stop_price"] = _rounded(proposed_stop)
                trial["shadow_stop_updated_at"] = timestamp
                changed = True

        stop = trial.get("shadow_stop_price")
        if (
            trial["activation_reached"]
            and stop is not None
            and trial.get("first_hypothetical_trigger") is None
        ):
            trigger_price = low if side == "LONG" and low is not None else high if side == "SHORT" and high is not None else price
            threshold_crossed = trigger_price <= stop if side == "LONG" else trigger_price >= stop
            should_trigger = threshold_crossed or hypothetical_triggered is True
            if should_trigger:
                if fill_price is not None:
                    simulated_exit = float(fill_price)
                    fill_model = "EXPLICIT_HYPOTHETICAL_FILL"
                elif side == "LONG":
                    simulated_exit = min(float(stop), float(trigger_price))
                    fill_model = "CONSERVATIVE_STOP_OR_OBSERVED_TRIGGER_PRICE"
                else:
                    simulated_exit = max(float(stop), float(trigger_price))
                    fill_model = "CONSERVATIVE_STOP_OR_OBSERVED_TRIGGER_PRICE"
                simulated_return, simulated_pnl = _return_and_pnl(
                    side, entry, simulated_exit, quantity
                )
                trigger = {
                    "triggered_at": timestamp,
                    "observed_price": _rounded(price),
                    "observed_low": _rounded(low) if low is not None else None,
                    "observed_high": _rounded(high) if high is not None else None,
                    "shadow_stop_price": _rounded(stop),
                    "simulated_exit_price": _rounded(simulated_exit),
                    "simulated_fill_model": fill_model,
                    "simulated_return_percent": simulated_return,
                    "simulated_pnl": simulated_pnl,
                }
                trial["first_hypothetical_trigger"] = trigger
                trial["simulated_exit_price"] = trigger["simulated_exit_price"]
                trial["simulated_fill_model"] = fill_model
                trial["simulated_return_percent"] = simulated_return
                trial["simulated_pnl"] = simulated_pnl
                changed = True
                self._refresh_reconciliation(trial)

        if changed:
            trial["updated_at"] = timestamp
            state["last_update_at"] = timestamp
        return trial, changed, counted

    @staticmethod
    def _validate_identity_metadata(
        trial: Mapping[str, Any],
        *,
        symbol: str,
        side: str,
        entry_price: float,
        entry_quantity: float,
    ) -> None:
        if str(trial.get("symbol") or "") != symbol:
            raise ShadowExitTrialValidationError("symbol conflicts with persisted trial")
        if str(trial.get("position_side") or "") != side:
            raise ShadowExitTrialValidationError("position_side conflicts with persisted trial")
        for field, supplied in (("entry_price", entry_price), ("entry_quantity", entry_quantity)):
            persisted = float(trial.get(field) or 0.0)
            tolerance = max(1e-8, abs(persisted) * 1e-8)
            if abs(persisted - supplied) > tolerance:
                raise ShadowExitTrialValidationError(f"{field} conflicts with persisted trial")

    def _reconcile_actual_close_in_state(
        self,
        state: dict[str, Any],
        *,
        parent_order_id: str,
        parent_client_order_id: str,
        actual_close_verified: bool,
        exit_price: Any,
        exit_quantity: Any,
        closed_at: datetime | str | None,
        exit_order_id: str,
        exit_client_order_id: str,
    ) -> bool:
        if actual_close_verified is not True:
            raise ShadowExitTrialValidationError("actual close must be explicitly verified")
        key = make_trial_key(parent_order_id, parent_client_order_id)
        trial = state["trials"].get(key)
        if trial is None:
            raise ShadowExitTrialValidationError("cannot reconcile an unknown shadow trial")
        price = _positive_number(exit_price, "exit_price")
        quantity = _positive_number(exit_quantity, "exit_quantity")
        expected_quantity = float(trial["entry_quantity"])
        tolerance = max(1e-6, expected_quantity * 1e-6)
        if abs(quantity - expected_quantity) > tolerance:
            raise ShadowExitTrialValidationError("actual close quantity does not fully match trial")
        timestamp = _normalized_timestamp(closed_at)
        side = str(trial["position_side"])
        actual_return, actual_pnl = _return_and_pnl(
            side, float(trial["entry_price"]), price, quantity
        )
        close = {
            "verified": True,
            "exit_order_id": str(exit_order_id or "").strip() or None,
            "exit_client_order_id": str(exit_client_order_id or "").strip() or None,
            "exit_price": _rounded(price),
            "exit_quantity": _rounded(quantity),
            "closed_at": timestamp,
            "actual_return_percent": actual_return,
            "actual_pnl": actual_pnl,
        }
        existing = trial.get("actual_close")
        if existing is not None:
            if existing == close:
                return False
            raise ShadowExitTrialValidationError("actual close is immutable once recorded")
        trial["actual_close"] = close
        trial["actual_exit_order_id"] = close["exit_order_id"]
        trial["actual_exit_client_order_id"] = close["exit_client_order_id"]
        trial["actual_exit_price"] = close["exit_price"]
        trial["actual_exit_quantity"] = close["exit_quantity"]
        trial["actual_closed_at"] = timestamp
        trial["actual_return_percent"] = actual_return
        trial["actual_pnl"] = actual_pnl
        trial["updated_at"] = timestamp
        state["last_update_at"] = timestamp
        self._refresh_reconciliation(trial)
        return True

    @staticmethod
    def _refresh_reconciliation(trial: dict[str, Any]) -> None:
        actual = trial.get("actual_close")
        simulated_pnl = trial.get("simulated_pnl")
        simulated_return = trial.get("simulated_return_percent")
        if actual is None:
            trial["reconciliation_status"] = "OPEN"
            return
        if simulated_pnl is None or simulated_return is None:
            trial["reconciliation_status"] = "ACTUAL_CLOSE_WITHOUT_SHADOW_TRIGGER"
            return
        actual_pnl = float(actual["actual_pnl"])
        actual_return = float(actual["actual_return_percent"])
        trial["actual_minus_simulated_pnl"] = _rounded(actual_pnl - float(simulated_pnl))
        trial["actual_minus_simulated_return_percent"] = _rounded(
            actual_return - float(simulated_return)
        )
        trial["shadow_minus_actual_pnl"] = _rounded(float(simulated_pnl) - actual_pnl)
        trial["shadow_minus_actual_return_percent"] = _rounded(
            float(simulated_return) - actual_return
        )
        trial["reconciliation_status"] = "RECONCILED"

    def _reconcile_filled_closes(
        self,
        state: dict[str, Any],
        *,
        positions: Mapping[str, Mapping[str, Any]],
        order_history: Sequence[Mapping[str, Any]],
    ) -> bool:
        del positions  # Broker positions are observational only; fills prove the close.
        changed = False
        for trial in state["trials"].values():
            if trial.get("actual_close") is not None:
                continue
            expected_side = "sell" if trial["position_side"] == "LONG" else "buy"
            expected_intent = "sell_to_close" if trial["position_side"] == "LONG" else "buy_to_close"
            candidates: list[dict[str, Any]] = []
            for raw_order in order_history:
                order = _as_mapping(raw_order)
                if str(order.get("status") or "").strip().lower() != "filled":
                    continue
                if str(order.get("symbol") or "").strip().upper() != trial["symbol"]:
                    continue
                if str(order.get("parent_order_id") or "").strip() != trial["parent_order_id"]:
                    continue
                if str(order.get("parent_client_order_id") or "").strip() != trial["parent_client_order_id"]:
                    continue
                if str(order.get("side") or "").strip().lower() != expected_side:
                    continue
                intent = str(order.get("position_intent") or "").strip().lower()
                if intent and intent != expected_intent:
                    continue
                quantity = _first_positive(order, "filled_quantity", "filled_qty", "quantity", "qty")
                price = _first_positive(order, "average_fill_price", "filled_avg_price", "fill_price")
                if quantity is None or price is None:
                    continue
                expected_quantity = float(trial["entry_quantity"])
                if abs(quantity - expected_quantity) > max(1e-6, expected_quantity * 1e-6):
                    continue
                candidates.append(order)
            if len(candidates) != 1:
                continue
            order = candidates[0]
            close_time = order.get("filled_at") or order.get("updated_at")
            if close_time in (None, ""):
                continue
            try:
                did_change = self._reconcile_actual_close_in_state(
                    state,
                    parent_order_id=trial["parent_order_id"],
                    parent_client_order_id=trial["parent_client_order_id"],
                    actual_close_verified=True,
                    exit_price=_first_positive(
                        order, "average_fill_price", "filled_avg_price", "fill_price"
                    ),
                    exit_quantity=_first_positive(
                        order, "filled_quantity", "filled_qty", "quantity", "qty"
                    ),
                    closed_at=close_time,
                    exit_order_id=str(order.get("order_id") or order.get("id") or ""),
                    exit_client_order_id=str(order.get("client_order_id") or ""),
                )
            except ShadowExitTrialValidationError:
                continue
            changed = did_change or changed
        return changed

    @staticmethod
    def _new_state() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "last_update_at": None,
            "observation_counts": {"verified": 0, "unverified": 0},
            "trials": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._new_state()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ShadowExitTrialLedgerError(f"cannot read shadow trial ledger: {self.path}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise ShadowExitTrialLedgerError("unsupported or malformed shadow trial ledger schema")
        if not isinstance(state.get("trials"), dict):
            raise ShadowExitTrialLedgerError("shadow trial ledger trials must be an object")
        counts = state.get("observation_counts")
        if not isinstance(counts, dict):
            raise ShadowExitTrialLedgerError("shadow trial ledger observation_counts must be an object")
        for name in ("verified", "unverified"):
            value = counts.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ShadowExitTrialLedgerError(f"invalid observation counter: {name}")
        for key, trial in state["trials"].items():
            if not isinstance(trial, dict) or trial.get("trial_key") != key:
                raise ShadowExitTrialLedgerError("shadow trial ledger contains an invalid trial")
            try:
                recomputed_key = make_trial_key(
                    trial.get("parent_order_id"), trial.get("parent_client_order_id")
                )
            except ShadowExitTrialValidationError as exc:
                raise ShadowExitTrialLedgerError(
                    "shadow trial ledger contains a trial without verified identity"
                ) from exc
            if recomputed_key != key:
                raise ShadowExitTrialLedgerError(
                    "shadow trial ledger trial identity does not match its key"
                )
        return state

    def _save(self, state: Mapping[str, Any]) -> None:
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=str(parent)
            )
        except OSError as exc:
            raise ShadowExitTrialLedgerError(f"cannot prepare shadow trial ledger: {self.path}") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except (OSError, TypeError, ValueError) as exc:
            raise ShadowExitTrialLedgerError(f"cannot atomically write shadow trial ledger: {self.path}") from exc
        finally:
            try:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
            except OSError:
                pass

    @staticmethod
    def _summary_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
        trials = list(dict(state.get("trials") or {}).values())
        counts = dict(state.get("observation_counts") or {})

        def aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
            comparable = [
                float(row["shadow_minus_actual_pnl"])
                for row in rows
                if row.get("shadow_minus_actual_pnl") is not None
            ]
            better = [delta for delta in comparable if delta > 0]
            actual_closed_count = sum(row.get("actual_close") is not None for row in rows)
            return {
                "trial_count": len(rows),
                "open_trial_count": sum(row.get("actual_close") is None for row in rows),
                "activated_count": sum(bool(row.get("activation_reached")) for row in rows),
                "triggered_count": sum(row.get("first_hypothetical_trigger") is not None for row in rows),
                # A completed trial is decision-grade: both the hypothetical
                # exit and the real close exist, so their P&L can be compared.
                "completed_count": len(comparable),
                "actual_closed_count": actual_closed_count,
                "censored_count": actual_closed_count - len(comparable),
                "comparable_count": len(comparable),
                "shadow_better_count": len(better),
                "shadow_better_rate_percent": _rounded((len(better) / len(comparable)) * 100.0) if comparable else 0.0,
                "mean_delta_pnl": _rounded(statistics.fmean(comparable)) if comparable else 0.0,
                "median_delta_pnl": _rounded(statistics.median(comparable)) if comparable else 0.0,
                "avoided_drawdown": _rounded(sum(max(delta, 0.0) for delta in comparable)),
                "upside_forfeited": _rounded(sum(max(-delta, 0.0) for delta in comparable)),
            }

        result = aggregate(trials)
        verified = int(counts.get("verified") or 0)
        unverified = int(counts.get("unverified") or 0)
        total = verified + unverified
        result.update(
            {
                "schema_version": SCHEMA_VERSION,
                "last_update_at": state.get("last_update_at"),
                "verified_observation_count": verified,
                "unverified_observation_count": unverified,
                "coverage_percent": _rounded((verified / total) * 100.0) if total else 0.0,
            }
        )
        strategies: dict[str, list[Mapping[str, Any]]] = {}
        for trial in trials:
            strategy_id = str(trial.get("strategy_id") or "UNSPECIFIED")
            strategies.setdefault(strategy_id, []).append(trial)
        result["by_strategy"] = {
            strategy_id: aggregate(strategies[strategy_id]) for strategy_id in sorted(strategies)
        }
        return result


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "ShadowExitTrialLedger",
    "ShadowExitTrialLedgerError",
    "ShadowExitTrialValidationError",
    "make_trial_key",
]
