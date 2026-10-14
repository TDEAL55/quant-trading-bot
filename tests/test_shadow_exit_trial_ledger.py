from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading

import pytest

import shadow_exit_trial_ledger as ledger_module
from shadow_exit_trial_ledger import (
    ShadowExitTrialLedger,
    ShadowExitTrialLedgerError,
    ShadowExitTrialValidationError,
    make_trial_key,
)


PARENT = "parent-order-1"
CLIENT = "qtb-live-parent-1"


def _long_observation(ledger, **overrides):
    values = {
        "parent_order_id": PARENT,
        "parent_client_order_id": CLIENT,
        "filled_parent_verified": True,
        "symbol": "NVDA",
        "position_side": "LONG",
        "strategy_id": "stock_trend_pullback_v3",
        "entry_price": 100,
        "entry_quantity": 2,
        "observed_price": 108,
        "observed_at": "2026-10-13T15:00:00Z",
        "activation_reached": True,
        "proposed_shadow_stop": 105,
    }
    values.update(overrides)
    return ledger.record_observation(**values)


def test_rejects_unverified_or_incomplete_parent_identity_without_creating_file(tmp_path):
    path = tmp_path / "shadow.json"
    ledger = ShadowExitTrialLedger(path)

    with pytest.raises(ShadowExitTrialValidationError, match="explicitly verified"):
        _long_observation(ledger, filled_parent_verified=False)
    with pytest.raises(ShadowExitTrialValidationError, match="parent_order_id"):
        _long_observation(ledger, parent_order_id="")

    assert not path.exists()


def test_persists_trials_keyed_by_both_parent_identifiers(tmp_path):
    path = tmp_path / "shadow.json"
    ledger = ShadowExitTrialLedger(path)
    recorded = _long_observation(ledger)

    reloaded = ShadowExitTrialLedger(path)
    assert recorded["trial_key"] == make_trial_key(PARENT, CLIENT)
    assert reloaded.get_trial(PARENT, CLIENT) == recorded
    assert reloaded.get_trial(PARENT, "different-client") is None
    assert reloaded.summary()["trial_count"] == 1
    assert reloaded.summary()["verified_observation_count"] == 1


def test_long_activation_is_sticky_and_stop_only_ratchets_up(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    activated = _long_observation(ledger)
    worse = _long_observation(
        ledger,
        observed_at="2026-10-13T15:01:00Z",
        observed_price=109,
        activation_reached=False,
        proposed_shadow_stop=103,
    )
    better = _long_observation(
        ledger,
        observed_at="2026-10-13T15:02:00Z",
        observed_price=110,
        activation_reached=False,
        proposed_shadow_stop=107,
    )

    assert activated["activated_at"] == "2026-10-13T15:00:00+00:00"
    assert worse["activation_reached"] is True
    assert worse["activated_at"] == activated["activated_at"]
    assert worse["shadow_stop_price"] == 105
    assert better["shadow_stop_price"] == 107
    assert better["observation_count"] == 3


def test_short_stop_only_ratchets_down(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    common = {
        "parent_order_id": "short-parent",
        "parent_client_order_id": "short-client",
        "filled_parent_verified": True,
        "symbol": "SBLK",
        "position_side": "SHORT",
        "entry_price": 100,
        "entry_quantity": 3,
        "observed_price": 92,
        "activation_reached": True,
    }
    ledger.record_observation(
        **common,
        observed_at="2026-10-13T15:00:00Z",
        proposed_shadow_stop=97,
    )
    worse = ledger.record_observation(
        **common,
        observed_at="2026-10-13T15:01:00Z",
        proposed_shadow_stop=99,
    )
    better = ledger.record_observation(
        **common,
        observed_at="2026-10-13T15:02:00Z",
        proposed_shadow_stop=94,
    )

    assert worse["shadow_stop_price"] == 97
    assert better["shadow_stop_price"] == 94


def test_first_hypothetical_trigger_and_simulated_result_are_frozen(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    _long_observation(ledger)
    triggered = _long_observation(
        ledger,
        observed_at="2026-10-13T15:03:00Z",
        observed_price=108,
        observed_low=104,
        proposed_shadow_stop=105,
    )
    later = _long_observation(
        ledger,
        observed_at="2026-10-13T15:04:00Z",
        observed_price=90,
        observed_low=89,
        proposed_shadow_stop=108,
        hypothetical_fill_price=89,
    )

    assert triggered["first_hypothetical_trigger"]["triggered_at"] == "2026-10-13T15:03:00+00:00"
    assert triggered["simulated_exit_price"] == 104
    assert triggered["simulated_return_percent"] == 4
    assert triggered["simulated_pnl"] == 8
    assert (
        triggered["simulated_fill_model"]
        == "CONSERVATIVE_STOP_OR_OBSERVED_TRIGGER_PRICE"
    )
    assert later["first_hypothetical_trigger"] == triggered["first_hypothetical_trigger"]
    assert later["simulated_pnl"] == 8
    assert later["shadow_stop_price"] == 105


def test_actual_close_reconciles_deltas_and_is_immutable(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    _long_observation(ledger)
    _long_observation(
        ledger,
        observed_at="2026-10-13T15:03:00Z",
        observed_price=104,
        proposed_shadow_stop=105,
    )
    reconciled = ledger.reconcile_actual_close(
        parent_order_id=PARENT,
        parent_client_order_id=CLIENT,
        actual_close_verified=True,
        exit_price=103,
        exit_quantity=2,
        closed_at="2026-10-13T15:05:00Z",
        exit_order_id="stop-leg",
        exit_client_order_id="stop-client",
    )

    assert reconciled["actual_pnl"] == 6
    assert reconciled["simulated_pnl"] == 8
    assert reconciled["actual_minus_simulated_pnl"] == -2
    assert reconciled["shadow_minus_actual_pnl"] == 2
    assert reconciled["reconciliation_status"] == "RECONCILED"

    same = ledger.reconcile_actual_close(
        parent_order_id=PARENT,
        parent_client_order_id=CLIENT,
        actual_close_verified=True,
        exit_price=103,
        exit_quantity=2,
        closed_at="2026-10-13T15:05:00Z",
        exit_order_id="stop-leg",
        exit_client_order_id="stop-client",
    )
    assert same == reconciled
    with pytest.raises(ShadowExitTrialValidationError, match="immutable"):
        ledger.reconcile_actual_close(
            parent_order_id=PARENT,
            parent_client_order_id=CLIENT,
            actual_close_verified=True,
            exit_price=102,
            exit_quantity=2,
            closed_at="2026-10-13T15:05:00Z",
        )


def test_actual_close_without_shadow_trigger_is_censored_not_completed(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    _long_observation(
        ledger,
        activation_reached=False,
        proposed_shadow_stop=None,
        observed_price=101,
    )
    ledger.reconcile_actual_close(
        parent_order_id=PARENT,
        parent_client_order_id=CLIENT,
        actual_close_verified=True,
        exit_price=102,
        exit_quantity=2,
        closed_at="2026-10-13T15:05:00Z",
    )

    summary = ledger.summary()
    assert summary["actual_closed_count"] == 1
    assert summary["censored_count"] == 1
    assert summary["completed_count"] == 0
    assert summary["comparable_count"] == 0


def test_batch_update_counts_coverage_and_reconciles_one_exact_filled_child(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    review = {
        "symbol": "NVDA",
        "strategy_id": "stock_trend_pullback_v3",
        "parent_order_id": PARENT,
        "parent_client_order_id": CLIENT,
        "filled_parent_verified": True,
        "position_side": "LONG",
        "entry_price": 100,
        "entry_quantity": 2,
        "current_price": 104,
        "trailing_activation_reached": True,
        "proposed_protective_stop_price": 105,
        "would_trigger_exit_now": True,
    }
    first_summary = ledger.update(
        [review, {"symbol": "MANUAL", "filled_parent_verified": False}],
        observed_at="2026-10-13T15:00:00Z",
        positions={"NVDA": {"quantity": 2, "avg_entry_price": 100, "current_price": 104}},
        order_history=[],
    )
    assert first_summary["verified_observation_count"] == 1
    assert first_summary["unverified_observation_count"] == 1
    assert first_summary["coverage_percent"] == 50

    close = {
        "order_id": "filled-stop",
        "client_order_id": "filled-stop-client",
        "parent_order_id": PARENT,
        "parent_client_order_id": CLIENT,
        "symbol": "NVDA",
        "side": "sell",
        "position_intent": "sell_to_close",
        "status": "filled",
        "filled_quantity": 2,
        "average_fill_price": 103,
        "filled_at": "2026-10-13T15:05:00Z",
    }
    completed = ledger.update(
        [],
        observed_at="2026-10-13T15:06:00Z",
        positions={},
        order_history=[close],
    )
    assert completed["completed_count"] == 1
    assert completed["shadow_better_count"] == 1
    assert completed["shadow_better_rate_percent"] == 100
    assert completed["mean_delta_pnl"] == 2
    assert completed["median_delta_pnl"] == 2
    assert completed["avoided_drawdown"] == 2
    assert completed["upside_forfeited"] == 0
    assert completed["by_strategy"]["stock_trend_pullback_v3"]["completed_count"] == 1


def test_identity_metadata_conflict_does_not_modify_evidence(tmp_path):
    path = tmp_path / "shadow.json"
    ledger = ShadowExitTrialLedger(path)
    original = _long_observation(ledger)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ShadowExitTrialValidationError, match="entry_price"):
        _long_observation(
            ledger,
            entry_price=101,
            observed_at="2026-10-13T15:10:00Z",
        )

    assert path.read_text(encoding="utf-8") == before
    assert ledger.get_trial(PARENT, CLIENT) == original


def test_corrupt_json_fails_closed_without_overwriting_it(tmp_path):
    path = tmp_path / "shadow.json"
    path.write_text("{not-json", encoding="utf-8")
    ledger = ShadowExitTrialLedger(path)

    with pytest.raises(ShadowExitTrialLedgerError, match="cannot read"):
        _long_observation(ledger)

    assert path.read_text(encoding="utf-8") == "{not-json"


def test_failed_atomic_replace_preserves_previous_file_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "shadow.json"
    ledger = ShadowExitTrialLedger(path)
    _long_observation(ledger)
    original = path.read_text(encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(ledger_module.os, "replace", fail_replace)
    with pytest.raises(ShadowExitTrialLedgerError, match="atomically write"):
        _long_observation(
            ledger,
            observed_at="2026-10-13T15:10:00Z",
            observed_price=110,
            proposed_shadow_stop=107,
        )

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".shadow.json.*.tmp")) == []
    assert json.loads(original)["schema_version"] == 1


def test_update_seeds_verified_broker_stop_but_does_not_trigger_before_activation(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    review = {
        "symbol": "NVDA",
        "strategy_id": "stock_trend_pullback_v3",
        "parent_order_id": PARENT,
        "parent_client_order_id": CLIENT,
        "filled_parent_verified": True,
        "position_side": "LONG",
        "entry_price": 100,
        "entry_quantity": 2,
        "current_price": 94,
        "trailing_activation_reached": False,
        "broker_bracket_verified": True,
        "protective_stop_source": "BROKER_OPEN_STOP_LEG",
        "current_protective_stop_price": 95,
        "proposed_protective_stop_price": 105,
        "would_tighten_protective_stop": False,
        "would_trigger_exit_now": False,
    }

    summary = ledger.update(
        [review],
        observed_at="2026-10-13T15:00:00Z",
        positions={"NVDA": {"quantity": 2, "current_price": 94}},
        order_history=[],
    )
    trial = ledger.get_trial(PARENT, CLIENT)

    assert summary["triggered_count"] == 0
    assert trial["activation_reached"] is False
    assert trial["latest_verified_broker_stop_price"] == 95
    assert trial["shadow_stop_price"] == 95
    assert trial["first_hypothetical_trigger"] is None


def test_update_rejects_non_tightening_proposal_and_never_loosens_broker_baseline(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    review = {
        "symbol": "NVDA",
        "strategy_id": "stock_trend_pullback_v3",
        "parent_order_id": PARENT,
        "parent_client_order_id": CLIENT,
        "filled_parent_verified": True,
        "position_side": "LONG",
        "entry_price": 100,
        "entry_quantity": 2,
        "current_price": 110,
        "trailing_activation_reached": True,
        "broker_bracket_verified": True,
        "protective_stop_source": "BROKER_OPEN_STOP_LEG",
        "current_protective_stop_price": 95,
        "proposed_protective_stop_price": 105,
        "would_tighten_protective_stop": False,
        "would_trigger_exit_now": False,
    }
    ledger.update(
        [review],
        observed_at="2026-10-13T15:00:00Z",
        positions={},
        order_history=[],
    )
    assert ledger.get_trial(PARENT, CLIENT)["shadow_stop_price"] == 95

    review.update(
        current_protective_stop_price=107,
        proposed_protective_stop_price=106,
    )
    ledger.update(
        [review],
        observed_at="2026-10-13T15:01:00Z",
        positions={},
        order_history=[],
    )
    assert ledger.get_trial(PARENT, CLIENT)["shadow_stop_price"] == 107

    review.update(
        current_protective_stop_price=90,
        proposed_protective_stop_price=106,
        would_tighten_protective_stop=True,
    )
    ledger.update(
        [review],
        observed_at="2026-10-13T15:02:00Z",
        positions={},
        order_history=[],
    )
    trial = ledger.get_trial(PARENT, CLIENT)
    assert trial["latest_verified_broker_stop_price"] == 90
    assert trial["shadow_stop_price"] == 107


def test_short_shadow_stop_never_loosens_above_verified_broker_baseline(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    review = {
        "symbol": "SBLK",
        "strategy_id": "stock_trend_ensemble_v2",
        "parent_order_id": "short-parent",
        "parent_client_order_id": "short-client",
        "filled_parent_verified": True,
        "position_side": "SHORT",
        "entry_price": 100,
        "entry_quantity": 2,
        "current_price": 90,
        "trailing_activation_reached": True,
        "broker_bracket_verified": True,
        "protective_stop_source": "BROKER_OPEN_STOP_LEG",
        "current_protective_stop_price": 96,
        "proposed_protective_stop_price": 94,
        "would_tighten_protective_stop": True,
        "would_trigger_exit_now": False,
    }
    ledger.update(
        [review],
        observed_at="2026-10-13T15:00:00Z",
        positions={},
        order_history=[],
    )
    assert ledger.get_trial("short-parent", "short-client")["shadow_stop_price"] == 94

    review.update(
        current_protective_stop_price=98,
        proposed_protective_stop_price=97,
    )
    ledger.update(
        [review],
        observed_at="2026-10-13T15:01:00Z",
        positions={},
        order_history=[],
    )
    assert ledger.get_trial("short-parent", "short-client")["shadow_stop_price"] == 94


def test_true_trigger_can_use_proposed_stop_without_tighten_flag(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    review = {
        "symbol": "NVDA",
        "strategy_id": "stock_trend_pullback_v3",
        "parent_order_id": PARENT,
        "parent_client_order_id": CLIENT,
        "filled_parent_verified": True,
        "position_side": "LONG",
        "entry_price": 100,
        "entry_quantity": 2,
        "current_price": 104,
        "trailing_activation_reached": True,
        "broker_bracket_verified": True,
        "protective_stop_source": "BROKER_OPEN_STOP_LEG",
        "current_protective_stop_price": 95,
        "proposed_protective_stop_price": 105,
        "would_tighten_protective_stop": False,
        "would_trigger_exit_now": True,
    }

    ledger.update(
        [review],
        observed_at="2026-10-13T15:00:00Z",
        positions={},
        order_history=[],
    )
    trial = ledger.get_trial(PARENT, CLIENT)
    assert trial["shadow_stop_price"] == 105
    assert trial["simulated_exit_price"] == 104
    assert trial["first_hypothetical_trigger"]["shadow_stop_price"] == 105


def test_conservative_default_fill_models_long_and_short_gap_through_stop(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    _long_observation(ledger)
    long_trial = _long_observation(
        ledger,
        observed_at="2026-10-13T15:01:00Z",
        observed_price=101,
        observed_low=99,
        proposed_shadow_stop=105,
    )
    short_common = {
        "parent_order_id": "short-parent",
        "parent_client_order_id": "short-client",
        "filled_parent_verified": True,
        "symbol": "SBLK",
        "position_side": "SHORT",
        "entry_price": 100,
        "entry_quantity": 3,
        "activation_reached": True,
    }
    ledger.record_observation(
        **short_common,
        observed_price=92,
        observed_at="2026-10-13T15:00:00Z",
        proposed_shadow_stop=97,
    )
    short_trial = ledger.record_observation(
        **short_common,
        observed_price=99,
        observed_high=101,
        observed_at="2026-10-13T15:01:00Z",
        proposed_shadow_stop=97,
    )

    assert long_trial["simulated_exit_price"] == 99
    assert short_trial["simulated_exit_price"] == 101
    assert long_trial["simulated_fill_model"] == short_trial["simulated_fill_model"]
    assert (
        long_trial["simulated_fill_model"]
        == "CONSERVATIVE_STOP_OR_OBSERVED_TRIGGER_PRICE"
    )


def test_explicit_hypothetical_fill_is_labeled_and_preserved(tmp_path):
    ledger = ShadowExitTrialLedger(tmp_path / "shadow.json")
    _long_observation(ledger)
    trial = _long_observation(
        ledger,
        observed_at="2026-10-13T15:01:00Z",
        observed_price=101,
        observed_low=99,
        proposed_shadow_stop=105,
        hypothetical_fill_price=98.5,
    )

    assert trial["simulated_exit_price"] == 98.5
    assert trial["simulated_fill_model"] == "EXPLICIT_HYPOTHETICAL_FILL"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("parent_order_id", "tampered-parent", "identity does not match"),
        ("parent_client_order_id", "", "without verified identity"),
    ],
)
def test_load_recomputes_trial_key_and_fails_closed_on_identity_tampering(
    tmp_path, field, value, message
):
    path = tmp_path / "shadow.json"
    ledger = ShadowExitTrialLedger(path)
    _long_observation(ledger)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trials"][make_trial_key(PARENT, CLIENT)][field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ShadowExitTrialLedgerError, match=message):
        ledger.summary()


def test_same_path_instances_coordinate_concurrent_load_modify_save(tmp_path):
    path = tmp_path / "shadow.json"
    ledgers = [ShadowExitTrialLedger(path), ShadowExitTrialLedger(path)]

    def write(index):
        return _long_observation(
            ledgers[index % 2],
            observed_at=f"2026-10-13T15:{index:02d}:00Z",
            observed_price=108 + (index / 100),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(20)))

    trial = ledgers[0].get_trial(PARENT, CLIENT)
    assert trial["observation_count"] == 20
    assert ledgers[1].summary()["verified_observation_count"] == 20


def test_interprocess_lock_acquisition_has_bounded_timeout(tmp_path):
    path = tmp_path / "shadow.json"
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if os.name == "nt":
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
    assert ledger_module._acquire_file_lock_nonblocking(handle) is True
    try:
        ledger = ShadowExitTrialLedger(
            path,
            lock_timeout_seconds=0.05,
            lock_poll_seconds=0.005,
        )
        with pytest.raises(ShadowExitTrialLedgerError, match="timed out acquiring"):
            ledger.summary()
    finally:
        ledger_module._release_file_lock(handle)
        handle.close()


def test_same_path_inprocess_lock_acquisition_has_bounded_timeout(tmp_path):
    ledger = ShadowExitTrialLedger(
        tmp_path / "shadow.json",
        lock_timeout_seconds=0.05,
        lock_poll_seconds=0.005,
    )
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock():
        with ledger._lock:
            acquired.set()
            release.wait(timeout=2)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert acquired.wait(timeout=1)
    try:
        with pytest.raises(ShadowExitTrialLedgerError, match="in-process"):
            ledger.summary()
    finally:
        release.set()
        holder.join(timeout=1)
    assert not holder.is_alive()
