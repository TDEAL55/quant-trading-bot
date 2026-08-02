from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import continuous_paper_runner
from continuous_paper_runner import run_continuous_paper_runner
from run_lock import RunLockBusyError


EASTERN_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


class _Clock:
    def __init__(self, values):
        self._values = list(values)
        self._index = 0

    def __call__(self):
        if self._index < len(self._values):
            value = self._values[self._index]
            self._index += 1
            return value
        return self._values[-1]


class _SetStopEvent:
    def is_set(self):
        return True


def _config(tmp_path, trading_mode="PAPER", scan_interval_minutes=5, max_daily_orders=1, db_subpath="runner.db"):
    db_path = tmp_path / db_subpath
    return type(
        "Cfg",
        (),
        {
            "trading_mode": trading_mode,
            "database_url": f"sqlite:///{db_path}",
            "database_path": db_path,
            "scan_interval_minutes": scan_interval_minutes,
            "max_daily_orders": max_daily_orders,
        },
    )()


def _successful_result(order_id="paper-order-1"):
    return {
        "execution_status": "completed",
        "execution": {
            "paper_order": {"order_id": order_id},
            "risk_result": {"approved": True, "checks": {"duplicate_protection": True}},
            "reconciliation": {"reconciliation_status": "matched", "position_mismatch_count": 0},
        },
    }


def _failed_result(status="risk_rejected"):
    return {
        "execution_status": status,
        "execution": {
            "paper_order": {"order_id": ""},
            "risk_result": {"approved": False, "checks": {"duplicate_protection": True}},
            "reconciliation": {"reconciliation_status": "matched", "position_mismatch_count": 0},
        },
    }


def _state_path(tmp_path):
    return tmp_path / "explicit" / "runner.state.json"


def test_requires_paper_mode(tmp_path):
    cfg = _config(tmp_path, trading_mode="LIVE")

    with pytest.raises(RuntimeError, match="TRADING_MODE=PAPER"):
        run_continuous_paper_runner(
            config_loader=lambda: cfg,
            state_path=_state_path(tmp_path),
            max_iterations=1,
            now_provider=lambda: datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ),
            sleep_fn=lambda _seconds: None,
        )


def test_rejects_invalid_scan_interval(tmp_path):
    cfg = _config(tmp_path, scan_interval_minutes=0)

    with pytest.raises(ValueError, match="SCAN_INTERVAL_MINUTES"):
        run_continuous_paper_runner(
            config_loader=lambda: cfg,
            state_path=_state_path(tmp_path),
            max_iterations=1,
            now_provider=lambda: datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ),
            sleep_fn=lambda _seconds: None,
        )


def test_exactly_930_et_is_open(tmp_path):
    cfg = _config(tmp_path)
    calls = {"runner": 0}
    sleeps = []

    def _runner(**kwargs):
        calls["runner"] += 1
        return _failed_result(status="no_candidates")

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=_state_path(tmp_path),
        now_provider=_Clock([datetime(2026, 7, 22, 9, 30, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        max_iterations=1,
    )

    assert calls["runner"] == 1
    assert stats["closed_market_sleeps"] == 0
    assert sleeps == [300]


def test_exactly_1600_et_is_closed(tmp_path):
    cfg = _config(tmp_path)
    calls = {"runner": 0}
    sleeps = []

    def _runner(**kwargs):
        calls["runner"] += 1
        return _failed_result(status="no_candidates")

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=_state_path(tmp_path),
        now_provider=_Clock([datetime(2026, 7, 22, 16, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        max_iterations=1,
    )

    assert calls["runner"] == 0
    assert stats["closed_market_sleeps"] == 1
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_friday_after_close_sleeps_until_monday(tmp_path):
    cfg = _config(tmp_path)
    friday_after_close = datetime(2026, 7, 24, 16, 1, tzinfo=EASTERN_TZ)
    sleeps = []

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _failed_result(status="no_candidates"),
        state_path=_state_path(tmp_path),
        now_provider=_Clock([friday_after_close]),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        max_iterations=1,
    )

    assert stats["closed_market_sleeps"] == 1
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(235740.0)


def test_saturday_and_sunday_sleep_until_monday(tmp_path):
    cfg = _config(tmp_path)
    sleeps_sat = []
    sleeps_sun = []

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _failed_result(status="no_candidates"),
        state_path=_state_path(tmp_path),
        now_provider=_Clock([datetime(2026, 7, 25, 12, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps_sat.append(seconds),
        max_iterations=1,
    )
    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _failed_result(status="no_candidates"),
        state_path=_state_path(tmp_path),
        now_provider=_Clock([datetime(2026, 7, 26, 12, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps_sun.append(seconds),
        max_iterations=1,
    )

    assert sleeps_sat == [pytest.approx(163800.0)]
    assert sleeps_sun == [pytest.approx(77400.0)]


def test_utc_timestamp_converts_to_eastern_market_open(tmp_path):
    cfg = _config(tmp_path)
    calls = {"runner": 0}

    def _runner(**kwargs):
        calls["runner"] += 1
        return _failed_result(status="no_candidates")

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=_state_path(tmp_path),
        now_provider=_Clock([datetime(2026, 7, 22, 13, 30, tzinfo=UTC_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    assert calls["runner"] == 1


def test_missing_state_file_is_handled_and_created(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    assert not state_path.exists()

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload == {"market_date": "2026-07-22", "orders_submitted": 1}


def test_malformed_state_file_is_handled(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not-json", encoding="utf-8")

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload == {"market_date": "2026-07-22", "orders_submitted": 1}


def test_date_rollover_resets_effective_daily_count(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"market_date": "2026-07-21", "orders_submitted": 1}) + "\n", encoding="utf-8")

    calls = {"runner": 0}

    def _runner(**kwargs):
        calls["runner"] += 1
        return _successful_result()

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert calls["runner"] == 1
    assert payload == {"market_date": "2026-07-22", "orders_submitted": 1}


def test_state_write_is_atomic_when_replace_fails(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    original_payload = {"market_date": "2026-07-22", "orders_submitted": 0}
    state_path.write_text(json.dumps(original_payload) + "\n", encoding="utf-8")

    def _failing_replace(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(continuous_paper_runner.os, "replace", _failing_replace)

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    assert stats["scans_failed"] == 1
    unchanged = json.loads(state_path.read_text(encoding="utf-8"))
    assert unchanged == original_payload
    assert not state_path.with_suffix(state_path.suffix + ".tmp").exists()


def test_state_directory_created_when_missing(tmp_path):
    cfg = _config(tmp_path, db_subpath="missing/state/runner.db")
    state_path = tmp_path / "new" / "state" / "runner.state.json"
    assert not state_path.parent.exists()

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    assert state_path.parent.exists()
    assert state_path.exists()


def test_daily_quota_only_increments_after_confirmed_submission(tmp_path):
    cfg = _config(tmp_path, max_daily_orders=9)
    state_path = _state_path(tmp_path)

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _failed_result(status="risk_rejected"),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )
    assert not state_path.exists()

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(order_id=""),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 5, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )
    assert not state_path.exists()

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(order_id="paper-order-1"),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 10, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["orders_submitted"] == 1


def test_max_daily_orders_uses_configured_limit(tmp_path):
    cfg = _config(tmp_path, max_daily_orders=2)
    sleeps = []
    calls = {"runner": 0}
    state_path = _state_path(tmp_path)

    def _runner(**kwargs):
        calls["runner"] += 1
        return _successful_result()

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=state_path,
        now_provider=_Clock(
            [
                datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ),
                datetime(2026, 7, 22, 10, 5, tzinfo=EASTERN_TZ),
            ]
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        max_iterations=2,
    )

    assert calls["runner"] == 2
    assert stats["quota_skips"] == 0
    assert sleeps == [300, 300]


def test_each_loop_path_sleeps_once(tmp_path):
    cfg = _config(tmp_path, scan_interval_minutes=5)
    state_path = _state_path(tmp_path)

    sleeps_closed = []
    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 25, 12, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps_closed.append(seconds),
        max_iterations=1,
    )
    assert len(sleeps_closed) == 1
    assert sleeps_closed[0] >= 0

    sleeps_quota = []
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"market_date": "2026-07-22", "orders_submitted": 1}) + "\n", encoding="utf-8")
    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps_quota.append(seconds),
        max_iterations=1,
    )
    assert sleeps_quota == [300]

    sleeps_lock = []

    class BusyLock:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            raise RunLockBusyError("busy")

        def __exit__(self, exc_type, exc, tb):
            return None

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 5, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps_lock.append(seconds),
        lock_factory=BusyLock,
        max_iterations=1,
    )
    assert sleeps_lock == [300]

    sleeps_exc = []

    def _failing_runner(**kwargs):
        raise RuntimeError("boom")

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_failing_runner,
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 10, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps_exc.append(seconds),
        max_iterations=1,
    )
    assert sleeps_exc == [300]


def test_lock_is_held_while_runner_executes_with_no_nested_scheduler_lock(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    lock_state = {"active": False, "entered": 0}

    class TrackingLock:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            if lock_state["active"]:
                raise AssertionError("scheduler lock re-entered")
            lock_state["active"] = True
            lock_state["entered"] += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            lock_state["active"] = False
            return None

    def _runner(**kwargs):
        assert lock_state["active"] is True
        return _failed_result(status="no_candidates")

    run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda _seconds: None,
        lock_factory=TrackingLock,
        max_iterations=1,
    )

    assert lock_state["entered"] == 1


def test_stop_event_can_prevent_loop_start(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    sleeps = []

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _successful_result(),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        stop_event=_SetStopEvent(),
    )

    assert stats["cycles"] == 0
    assert sleeps == []


def test_keyboard_interrupt_during_sleep_shuts_down_cleanly(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)

    def _interrupting_sleep(_seconds):
        raise KeyboardInterrupt()

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=lambda **kwargs: _failed_result(status="no_candidates"),
        state_path=state_path,
        now_provider=_Clock([datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ)]),
        sleep_fn=_interrupting_sleep,
        max_iterations=1,
    )

    assert stats["cycles"] == 0


def test_scan_interval_path_continues_after_lock_busy_and_exception(tmp_path):
    cfg = _config(tmp_path)
    state_path = _state_path(tmp_path)
    sleeps = []
    state = {"attempt": 0}

    class BusyThenPassLock:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            if state["attempt"] == 0:
                state["attempt"] += 1
                raise RunLockBusyError("busy")
            state["attempt"] += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def _runner(**kwargs):
        if state["attempt"] == 2:
            raise RuntimeError("boom")
        return _failed_result(status="no_candidates")

    stats = run_continuous_paper_runner(
        config_loader=lambda: cfg,
        runner=_runner,
        state_path=state_path,
        now_provider=_Clock(
            [
                datetime(2026, 7, 22, 10, 0, tzinfo=EASTERN_TZ),
                datetime(2026, 7, 22, 10, 5, tzinfo=EASTERN_TZ),
            ]
        ),
        sleep_fn=lambda seconds: sleeps.append(seconds),
        lock_factory=BusyThenPassLock,
        max_iterations=2,
    )

    assert stats["lock_skips"] == 1
    assert stats["scans_failed"] == 1
    assert sleeps == [300, 300]
