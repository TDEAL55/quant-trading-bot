from __future__ import annotations

from datetime import datetime, timezone

from continuous_scan_cycle import run_continuous_scan_cycle


class _Config:
    trading_mode = "PAPER"
    database_url = "sqlite:///unused.db"
    max_open_positions = 10
    max_position_equity_percent = 10.0


class _Broker:
    def __init__(self):
        self.mode = "PAPER"
        self.backend = "SIMULATED"
        self._positions = {}
        self._buying_power = 5000.0
        self.submissions = []

    def get_positions(self):
        return self._positions

    def get_buying_power(self):
        return self._buying_power

    def submit_order(self, side, ticker, quantity, **kwargs):
        self.submissions.append((side, ticker, quantity, dict(kwargs)))
        qty = float(quantity)
        self._positions[str(ticker).upper()] = {"quantity": qty, "avg_price": 100.0}
        self._buying_power -= qty * 100.0
        return {
            "status": "filled",
            "order_id": f"ord-{ticker}",
            "filled_quantity": qty,
            "average_fill_price": 100.0,
            "requested_quantity": qty,
            "client_order_id": str(kwargs.get("client_order_id") or ""),
            "symbol": str(ticker).upper(),
        }


class _Repo:
    def __init__(self, database_url=None):
        self.database_url = database_url
        self.db = type("_Db", (), {"enabled": True})()

    def fetch_latest_submitting_run_by_execution_fingerprint(self, execution_fingerprint):
        return None

    def save_validation_run(self, payload):
        return {"run_id": payload.run.get("run_id")}

    def close(self):
        return None


class _RejectingBroker(_Broker):
    def submit_order(self, side, ticker, quantity, **kwargs):
        self.submissions.append((side, ticker, quantity, dict(kwargs)))
        qty = float(quantity)
        return {
            "status": "rejected",
            "order_id": f"ord-rejected-{ticker}",
            "filled_quantity": 0.0,
            "average_fill_price": 0.0,
            "requested_quantity": qty,
            "client_order_id": str(kwargs.get("client_order_id") or ""),
            "symbol": str(ticker).upper(),
            "rejection_reason": "insufficient_buying_power",
        }


def _scan_runner(_universe):
    return {
        "scan_results": [{"symbol": "AAA", "latest_price": 100.0, "overall_score": 82.0, "confidence": 76.0, "eligible": True, "rejection_reasons": []}],
        "ranked_candidates": [{"rank": 1, "symbol": "AAA", "latest_price": 100.0, "overall_score": 82.0, "confidence": 76.0, "eligible": True, "quantum_score": {"rejection_reasons": []}}],
        "summary": {"symbol_count": 1, "success_count": 1, "rejection_count": 0, "error_count": 0, "eligible_count": 1, "duration_seconds": 0.01},
    }


def _shortlist_runner(scan_payload, positions, cash, portfolio_value):
    del scan_payload, positions, cash, portfolio_value
    return {
        "selected": [
            {
                "rank": 1,
                "symbol": "AAA",
                "score": 82.0,
                "confidence": 76.0,
                "suggested_paper_notional": 1000.0,
                "quantum_score": {"rejection_reasons": []},
                "strategy_specific_scores": {"trend_momentum_v1": {"strategy_id": "trend_momentum_v1", "strategy_score": 80.0, "eligible": True}},
                "eligible_strategy_ids": ["trend_momentum_v1"],
            }
        ],
        "rejected": [],
        "portfolio_warnings": [],
        "selection_summary": {"selected_count": 1},
    }


def test_execution_is_fail_closed_by_default(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", False)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", False)

    broker = _Broker()
    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Config(),
        now_provider=lambda: datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database"},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=lambda: ([], 5000.0, 5000.0),
        universe_loader=lambda: [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}],
        persist=True,
        dry_run=False,
    )

    counters = result.execution.get("execution_counters") or {}
    assert result.execution_status == "completed"
    assert broker.submissions == []
    assert int(counters.get("orders_recommended") or 0) == 1
    assert int(counters.get("orders_submission_requested") or 0) == 0
    assert int(counters.get("orders_submitted") or 0) == 0
    assert int(counters.get("orders_filled") or 0) == 0
    assert int(counters.get("orders_rejected") or 0) == 0


def test_normal_dry_run_has_zero_submission_and_fill_counters(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", False)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", False)

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Config(),
        now_provider=lambda: datetime(2026, 8, 6, 14, 1, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: _Broker(),
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database"},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=lambda: ([], 5000.0, 5000.0),
        universe_loader=lambda: [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}],
        persist=True,
        dry_run=True,
    )

    counters = result.execution.get("execution_counters") or {}
    assert int(counters.get("orders_recommended") or 0) >= 0
    assert int(counters.get("orders_submission_requested") or 0) == 0
    assert int(counters.get("orders_submitted") or 0) == 0
    assert int(counters.get("orders_filled") or 0) == 0


def test_controlled_validation_executes_and_emits_lifecycle_events(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", True)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDERS", 1)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDER_NOTIONAL", 5000.0)

    events = []
    broker = _Broker()

    def _notify(**kwargs):
        events.append(dict(kwargs))

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Config(),
        now_provider=lambda: datetime(2026, 8, 6, 14, 5, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database"},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=lambda: ([], 5000.0, 5000.0),
        universe_loader=lambda: [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}],
        persist=True,
        dry_run=False,
        notification_callback=_notify,
    )

    counters = result.execution.get("execution_counters") or {}
    event_types = [str(item.get("event_type") or "") for item in events]

    assert broker.submissions
    assert int(counters.get("orders_recommended") or 0) == 1
    assert int(counters.get("orders_submission_requested") or 0) == 1
    assert int(counters.get("orders_submitted") or 0) == 1
    assert "trade_recommended" in event_types
    assert "paper_order_submission_requested" in event_types
    assert "paper_order_submitted" in event_types
    assert "paper_order_filled" in event_types


def test_autonomous_paper_executes_without_controlled_validation(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", False)

    broker = _Broker()
    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Config(),
        now_provider=lambda: datetime(2026, 8, 6, 14, 6, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database"},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=lambda: ([], 5000.0, 5000.0),
        universe_loader=lambda: [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}],
        persist=True,
        dry_run=False,
    )

    counters = result.execution.get("execution_counters") or {}
    assert broker.submissions
    assert int(counters.get("orders_recommended") or 0) == 1
    assert int(counters.get("orders_submission_requested") or 0) == 1
    assert int(counters.get("orders_submitted") or 0) == 1


def test_controlled_validation_rejected_order_does_not_increment_submitted(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", True)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDERS", 1)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDER_NOTIONAL", 5000.0)

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Config(),
        now_provider=lambda: datetime(2026, 8, 6, 14, 8, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: _RejectingBroker(),
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database"},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=lambda: ([], 5000.0, 5000.0),
        universe_loader=lambda: [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}],
        persist=True,
        dry_run=False,
    )

    counters = result.execution.get("execution_counters") or {}
    assert int(counters.get("orders_submission_requested") or 0) == 1
    assert int(counters.get("orders_submitted") or 0) == 0
    assert int(counters.get("orders_rejected") or 0) == 1
