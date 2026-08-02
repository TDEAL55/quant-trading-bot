from __future__ import annotations

from datetime import datetime, timezone

from continuous_scan_cycle import ContinuousScanCycleResult, run_continuous_scan_cycle


class _Config:
    trading_mode = "PAPER"
    database_url = "sqlite:///unused.db"
    max_open_positions = 10
    max_position_equity_percent = 10.0


class _Broker:
    def __init__(self, mode="PAPER"):
        self.mode = mode
        self._positions = {}
        self._buying_power = 5000.0
        self.submissions = []

    def get_positions(self):
        return self._positions

    def get_buying_power(self):
        return self._buying_power

    def submit_order(self, side, ticker, quantity, **kwargs):
        self.submissions.append((side, ticker, quantity))
        filled = float(quantity)
        self._positions[str(ticker).upper()] = {"quantity": filled, "avg_price": 100.0}
        self._buying_power -= filled * 100.0
        return {"status": "filled", "order_id": f"ord-{ticker}", "filled_quantity": filled, "average_fill_price": 100.0}


class _Repo:
    def __init__(self, database_url=None):
        self.database_url = database_url
        self.db = type("_Db", (), {"enabled": True})()
        self.closed = False
        self.saved = None
        self._existing = None

    def fetch_latest_submitting_run_by_execution_fingerprint(self, execution_fingerprint):
        return self._existing

    def save_validation_run(self, payload):
        self.saved = payload
        return {"run_id": payload.run.get("run_id")}

    def close(self):
        self.closed = True


def _config_loader():
    return _Config()


def _positions_loader():
    return ([], 5000.0, 5000.0)


def _scan_payload(symbol="AAA", price=100.0):
    return {
        "scan_results": [
            {
                "symbol": symbol,
                "latest_price": price,
                "overall_score": 82.0,
                "confidence": 76.0,
                "eligible": True,
                "rejection_reasons": [],
            }
        ],
        "ranked_candidates": [
            {
                "rank": 1,
                "symbol": symbol,
                "latest_price": price,
                "overall_score": 82.0,
                "confidence": 76.0,
            }
        ],
        "summary": {"symbol_count": 1, "success_count": 1, "rejection_count": 0, "error_count": 0, "eligible_count": 1, "duration_seconds": 0.01},
    }


def test_continuous_scan_cycle_completes_with_one_order():
    broker = _Broker()
    repo = _Repo()
    scan_calls = []
    shortlist_calls = []
    scan_persist_calls = []

    def _scan_runner(universe):
        scan_calls.append(list(universe))
        return _scan_payload()

    def _shortlist_runner(scan_payload, positions, cash, portfolio_value):
        shortlist_calls.append((scan_payload, positions, cash, portfolio_value))
        return {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 76.0,
                    "suggested_paper_notional": 1000.0,
                    "suggested_max_allocation_percent": 20.0,
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        }

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: scan_persist_calls.append(kwargs) or {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        persist=True,
    )

    assert isinstance(result, ContinuousScanCycleResult)
    assert result.execution_status == "completed"
    assert result.confirmed_order_count == 1
    assert result.execution["paper_order"]["order_id"] == "ord-AAA"
    assert scan_calls
    assert shortlist_calls
    assert scan_persist_calls
    assert repo.saved is not None
    assert repo.closed is True


def test_continuous_scan_cycle_returns_no_candidates_without_execution():
    repo = _Repo()
    scan_persist_calls = []

    def _scan_runner(universe):
        return {"scan_results": [], "ranked_candidates": [], "summary": {"symbol_count": 1, "eligible_count": 0}}

    def _shortlist_runner(scan_payload, positions, cash, portfolio_value):
        return {"selected": [], "rejected": [], "portfolio_warnings": [], "selection_summary": {"selected_count": 0}}

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 5, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: _Broker(),
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: scan_persist_calls.append(kwargs) or {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        persist=True,
    )

    assert result.execution_status == "no_candidates"
    assert result.confirmed_order_count == 0
    assert result.execution["paper_order"] == {}
    assert repo.saved is None
    assert scan_persist_calls


def test_continuous_scan_cycle_enforces_duplicate_protection():
    broker = _Broker()
    repo = _Repo()
    repo._existing = {"run_id": "prior-run", "status": "completed", "submitted_order_count": 1}

    def _scan_runner(universe):
        return _scan_payload()

    def _shortlist_runner(scan_payload, positions, cash, portfolio_value):
        return {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 76.0,
                    "suggested_paper_notional": 1000.0,
                    "suggested_max_allocation_percent": 20.0,
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        }

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 10, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        persist=True,
    )

    assert result.execution_status == "duplicate_rejected"
    assert result.confirmed_order_count == 0
    assert broker.submissions == []
    assert repo.saved is None


def test_continuous_scan_cycle_uses_broker_positions_as_source_of_truth():
    broker = _Broker()
    broker._positions = {"AAA": {"quantity": 3.0, "avg_price": 100.0}}
    repo = _Repo()

    def _scan_runner(universe):
        return _scan_payload(symbol="AAA", price=100.0)

    def _shortlist_runner(scan_payload, positions, cash, portfolio_value):
        return {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 76.0,
                    "suggested_paper_notional": 1000.0,
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        }

    # positions_loader intentionally disagrees; broker state should win.
    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 10, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=lambda: ([], 0.0, 0.0),
        persist=True,
    )

    assert result.execution_status == "risk_rejected"
    assert result.execution["risk_result"]["checks"]["existing_position"] is False
    assert broker.submissions == []


def test_continuous_scan_cycle_enforces_max_open_positions():
    broker = _Broker()
    broker._positions = {
        "AAA": {"quantity": 1.0, "avg_price": 100.0},
        "BBB": {"quantity": 1.0, "avg_price": 100.0},
    }
    repo = _Repo()

    class _Cfg(_Config):
        max_open_positions = 2

    def _scan_runner(universe):
        return _scan_payload(symbol="CCC", price=100.0)

    def _shortlist_runner(scan_payload, positions, cash, portfolio_value):
        return {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "CCC",
                    "score": 82.0,
                    "confidence": 76.0,
                    "suggested_paper_notional": 1000.0,
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        }

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _Cfg(),
        now_provider=lambda: datetime(2026, 7, 22, 10, 10, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=_shortlist_runner,
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        persist=True,
    )

    assert result.execution_status == "risk_rejected"
    assert result.execution["risk_result"]["checks"]["max_open_positions"] is False
    assert broker.submissions == []
