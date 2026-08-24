from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import pytest

from continuous_scan_cycle import ContinuousScanCycleResult, run_continuous_scan_cycle
from paper_broker import SimulatedPaperBroker
from stock_universe import AlpacaAssetUniverseError


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
        signed = filled if str(side).lower() == "buy" else -filled
        self._positions[str(ticker).upper()] = {"quantity": signed, "avg_price": 100.0}
        self._buying_power -= signed * 100.0
        return {"status": "filled", "order_id": f"ord-{ticker}", "side": side, "symbol": ticker, "filled_quantity": filled, "average_fill_price": 100.0}


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


def _universe_loader():
    return [{"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"}]


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


def test_continuous_scan_cycle_completes_with_one_order(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", False)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDERS", 1)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDER_NOTIONAL", 5000.0)

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
        universe_loader=_universe_loader,
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


def test_continuous_scan_cycle_opens_and_reconciles_an_intentional_short(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)
    monkeypatch.setattr("continuous_scan_cycle.PAPER_ALLOW_SHORT_SELLING", True)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", False)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDERS", 1)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDER_NOTIONAL", 5000.0)
    monkeypatch.setattr("continuous_scan_cycle.evaluate_all_strategies", lambda candidate: [
        {
            "symbol": candidate["symbol"],
            "signal": "SELL",
            "strategy_id": "bearish_trend_short_v1",
            "strategy_version": "1.0.0",
            "strategy_score": 82.0,
            "confidence": 76.0,
            "requested_risk_allocation": 1.0,
            "supports_scaling": False,
        }
    ])

    broker = _Broker()
    repo = _Repo()
    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=lambda universe: _scan_payload(symbol="BEAR", price=100.0),
        shortlist_runner=lambda *args, **kwargs: {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "BEAR",
                    "score": 25.0,
                    "confidence": 76.0,
                    "trade_side": "SELL",
                    "suggested_paper_notional": 1000.0,
                    "suggested_max_allocation_percent": 20.0,
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        },
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        universe_loader=lambda: [{"symbol": "BEAR", "company_name": "Bear", "sector": "Technology", "industry": "Software"}],
        persist=True,
    )

    assert result.execution_status == "completed"
    assert result.execution["paper_order"]["side"] == "SELL"
    assert broker.submissions[0][0] == "sell"
    assert broker.get_positions()["BEAR"]["quantity"] == -5.0
    assert result.execution["reconciliation"]["reconciliation_status"] == "matched"


def test_entry_cycle_never_sells_an_unrelated_existing_position(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)
    monkeypatch.setattr("continuous_scan_cycle.CONTROLLED_PAPER_VALIDATION", False)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDERS", 1)
    monkeypatch.setattr("continuous_scan_cycle.MAX_VALIDATION_ORDER_NOTIONAL", 5000.0)

    broker = _Broker()
    broker._positions = {"BBB": {"quantity": 2.0, "avg_price": 90.0}}
    repo = _Repo()

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=lambda universe: _scan_payload(symbol="AAA", price=100.0),
        shortlist_runner=lambda *args, **kwargs: {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 76.0,
                    "suggested_paper_notional": 500.0,
                    "suggested_max_allocation_percent": 10.0,
                }
            ],
            "rejected": [],
            "portfolio_warnings": [],
            "selection_summary": {"selected_count": 1},
        },
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        history_batch_loader=lambda symbols, start_date, end_date: {"BBB": _history_frame(90.0)},
        history_single_loader=lambda *args, **kwargs: pd.DataFrame(),
        persist=True,
    )

    assert result.execution_status == "completed"
    assert broker.submissions
    side, symbol, _quantity = broker.submissions[0]
    assert str(side).lower() == "buy"
    assert symbol == "AAA"
    assert broker.get_positions()["BBB"]["quantity"] == 2.0


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
        universe_loader=_universe_loader,
        persist=True,
    )

    assert result.execution_status == "no_candidates"
    assert result.confirmed_order_count == 0
    assert result.execution["paper_order"] == {}
    assert repo.saved is None
    assert scan_persist_calls


def test_position_guard_exit_takes_precedence_over_new_entry_scan(monkeypatch):
    monkeypatch.setattr("continuous_scan_cycle.PAPER_EXECUTION_ENABLED", True)

    class _GuardConfig(_Config):
        position_guard_enabled = True
        position_guard_auto_exit_enabled = True
        position_guard_stop_loss_percent = 4.0
        position_guard_take_profit_percent = 8.0
        position_guard_max_exits_per_cycle = 1

    class _GuardBroker(SimulatedPaperBroker):
        def get_positions(self):
            positions = super().get_positions()
            for payload in positions.values():
                payload["current_price"] = 95.0
                payload["market_value"] = float(payload["quantity"]) * 95.0
            return positions

    broker = _GuardBroker(
        mode="PAPER",
        buying_power=1000.0,
        positions={"JPM": {"quantity": 2.0, "avg_price": 100.0}},
    )

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=lambda: _GuardConfig(),
        now_provider=lambda: datetime(2026, 7, 22, 10, 5, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=lambda universe: pytest.fail("scanner must not run before a required risk exit"),
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        persist=False,
    )

    assert result.execution_status == "completed"
    assert result.confirmed_order_count == 1
    assert result.execution["paper_order"]["side"] == "SELL"
    assert result.execution["paper_order"]["exit_reason"] == "stop_loss_threshold_reached"
    assert broker.get_positions() == {}


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
        universe_loader=_universe_loader,
        persist=True,
    )

    assert result.execution_status == "duplicate_rejected"
    assert result.confirmed_order_count == 0
    assert broker.submissions == []
    assert repo.saved is None


def test_continuous_scan_cycle_dry_run_never_submits_orders():
    broker = _Broker()
    repo = _Repo()

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
        universe_loader=_universe_loader,
        persist=True,
        dry_run=True,
    )

    assert result.execution_status == "completed"
    assert broker.submissions == []
    assert result.execution["paper_order"]["submission_status"] == "accepted"


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
        universe_loader=_universe_loader,
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
        universe_loader=_universe_loader,
        persist=True,
    )

    assert result.execution_status == "risk_rejected"
    assert result.execution["risk_result"]["checks"]["max_open_positions"] is False
    assert broker.submissions == []


def test_continuous_scan_cycle_blocks_live_mode():
    class _LiveConfig(_Config):
        trading_mode = "LIVE"

    try:
        run_continuous_scan_cycle(
            database_url="sqlite:///unused.db",
            config_loader=lambda: _LiveConfig(),
            now_provider=lambda: datetime(2026, 7, 22, 10, 10, tzinfo=timezone.utc),
            broker_factory=lambda **kwargs: _Broker(mode="PAPER"),
            scan_runner=lambda universe: _scan_payload(),
            shortlist_runner=lambda *args, **kwargs: {"selected": []},
            scan_persistor=lambda **kwargs: {"storage": "database"},
            execution_repo_factory=lambda **kwargs: _Repo(),
            positions_loader=_positions_loader,
            universe_loader=_universe_loader,
            persist=False,
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "TRADING_MODE=PAPER" in str(exc)


def test_continuous_scan_cycle_reports_full_universe_count_in_diagnostic_mode():
    broker = _Broker()
    repo = _Repo()
    captured_universe = {"symbols": []}
    telemetry = []

    def _scan_runner(universe):
        captured_universe["symbols"] = [str(item.get("symbol")) for item in universe]
        return {
            "scan_results": [],
            "ranked_candidates": [],
            "summary": {"symbol_count": len(universe), "eligible_count": 0},
        }

    def _telemetry(event, payload):
        telemetry.append((event, dict(payload)))

    result = run_continuous_scan_cycle(
        database_url="sqlite:///unused.db",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 10, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=_scan_runner,
        shortlist_runner=lambda *args, **kwargs: {"selected": [], "rejected": [], "portfolio_warnings": [], "selection_summary": {"selected_count": 0}},
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        universe_loader=lambda: [
            {"symbol": "ZZZ", "company_name": "ZZZ", "sector": "Unknown", "industry": "Unknown"},
            {"symbol": "AAA", "company_name": "AAA", "sector": "Unknown", "industry": "Unknown"},
            {"symbol": "MMM", "company_name": "MMM", "sector": "Unknown", "industry": "Unknown"},
        ],
        persist=False,
        dry_run=True,
        diagnostic_symbol_limit=2,
        telemetry_callback=_telemetry,
    )

    assert captured_universe["symbols"] == ["AAA", "MMM"]
    assert result.scan["scan_payload"]["summary"]["full_universe_count"] == 3
    assert result.scan["scan_payload"]["summary"]["diagnostic_mode"] is True

    names = [name for name, _ in telemetry]
    assert "paper_connection_check_start" in names
    assert "paper_connection_check_complete" in names
    assert "universe_fetch_start" in names
    assert "universe_fetch_complete" in names
    assert "dry_run_execution_skipped" in names
    assert "scan_cycle_complete" in names


def test_universe_api_failure_does_not_become_no_candidates():
    telemetry = []

    def _telemetry(event, payload):
        telemetry.append((event, dict(payload)))

    def _failing_universe_loader():
        raise AlpacaAssetUniverseError(
            "alpaca assets request failed",
            telemetry={
                "api_exception_type": "RuntimeError",
                "unfiltered_asset_count": 0,
                "filtered_api_asset_count": 0,
                "client_filtered_asset_count": 0,
                "fallback_used": False,
                "api_request_elapsed_time": 0.0,
            },
        )

    with pytest.raises(AlpacaAssetUniverseError):
        run_continuous_scan_cycle(
            database_url="sqlite:///unused.db",
            config_loader=_config_loader,
            now_provider=lambda: datetime(2026, 7, 22, 10, 10, tzinfo=timezone.utc),
            broker_factory=lambda **kwargs: _Broker(mode="PAPER"),
            scan_runner=lambda universe: _scan_payload(),
            shortlist_runner=lambda *args, **kwargs: {"selected": []},
            scan_persistor=lambda **kwargs: {"storage": "database"},
            execution_repo_factory=lambda **kwargs: _Repo(),
            positions_loader=_positions_loader,
            universe_loader=_failing_universe_loader,
            persist=False,
            dry_run=True,
            telemetry_callback=_telemetry,
        )

    names = [name for name, _ in telemetry]
    assert "alpaca_asset_universe_fetch_failed" in names
    assert "alpaca_asset_universe_empty" in names
    assert "scan_cycle_complete" not in names


def test_portfolio_intelligence_integration_is_review_only_and_dry_run_safe(tmp_path):
    broker = _Broker()
    repo = _Repo()
    events = []

    def _telemetry(event, payload):
        events.append((event, dict(payload)))

    result = run_continuous_scan_cycle(
        database_url=f"sqlite:///{tmp_path / 'scan.db'}",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 15, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=lambda _u: _scan_payload(),
        shortlist_runner=lambda *_args, **_kwargs: {
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
        },
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: repo,
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        persist=True,
        dry_run=True,
        telemetry_callback=_telemetry,
    )

    assert result.execution_status == "completed"
    assert broker.submissions == []
    assert "portfolio_intelligence" in result.scan
    assert "portfolio_intelligence" in result.selection
    assert result.selection["portfolio_intelligence"]["review_required"] is True

    names = [name for name, _ in events]
    assert "portfolio_intelligence_start" in names
    assert "correlation_analysis_complete" in names
    assert "sector_analysis_complete" in names
    assert "strategy_allocation_analysis_complete" in names
    assert "portfolio_allocation_complete" in names
    assert "portfolio_recommendation_generated" in names


def _history_rows(base_close: float = 100.0, days: int = 80):
    index = pd.date_range("2026-01-01", periods=days, freq="D")
    rows = []
    for offset, ts in enumerate(index):
        rows.append({"date": ts.date().isoformat(), "close": float(base_close + offset)})
    return rows


def _history_frame(base_close: float = 100.0, days: int = 80):
    index = pd.date_range("2026-01-01", periods=days, freq="D")
    values = [float(base_close + offset) for offset in range(days)]
    return pd.DataFrame({"close": values}, index=index)


def test_portfolio_intelligence_reuses_scan_history_without_duplicate_fetch(tmp_path):
    broker = _Broker()
    batch_calls = []

    def _batch_loader(symbols, start_date, end_date):
        batch_calls.append((list(symbols), start_date, end_date))
        raise AssertionError("history batch loader should not be called when scan history is complete")

    result = run_continuous_scan_cycle(
        database_url=f"sqlite:///{tmp_path / 'scan-cache.db'}",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 20, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=lambda _u: {
            **_scan_payload(),
            "price_history_by_symbol": {"AAA": _history_rows(100.0)},
            "benchmark_price_history": _history_rows(420.0),
        },
        shortlist_runner=lambda *_args, **_kwargs: {"selected": [{"rank": 1, "symbol": "AAA", "score": 82.0}], "rejected": [], "portfolio_warnings": [], "selection_summary": {"selected_count": 1}},
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        history_batch_loader=_batch_loader,
        history_single_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("single loader should not be called")),
        persist=True,
        dry_run=True,
    )

    assert result.execution_status == "completed"
    assert batch_calls == []
    history_meta = result.selection["portfolio_intelligence"]["correlation_history_metadata"]
    assert history_meta["symbols_requested"] == ["AAA"]
    assert history_meta["symbols_missing_history"] == []


def test_portfolio_intelligence_fetches_missing_history_for_current_positions(tmp_path):
    broker = _Broker()
    broker._positions = {"BBB": {"quantity": 3.0, "avg_price": 98.0}}
    batch_requests = []

    def _batch_loader(symbols, start_date, end_date):
        batch_requests.append((sorted(symbols), start_date, end_date))
        return {"BBB": _history_frame(90.0)}

    result = run_continuous_scan_cycle(
        database_url=f"sqlite:///{tmp_path / 'scan-missing.db'}",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 25, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: broker,
        scan_runner=lambda _u: {**_scan_payload(), "price_history_by_symbol": {"AAA": _history_rows(100.0)}},
        shortlist_runner=lambda *_args, **_kwargs: {"selected": [{"rank": 1, "symbol": "AAA", "score": 82.0}], "rejected": [], "portfolio_warnings": [], "selection_summary": {"selected_count": 1}},
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        history_batch_loader=_batch_loader,
        history_single_loader=lambda *_args, **_kwargs: pd.DataFrame(),
        persist=True,
        dry_run=True,
    )

    assert result.execution_status == "completed"
    assert batch_requests
    assert "BBB" in batch_requests[0][0]
    history_meta = result.selection["portfolio_intelligence"]["correlation_history_metadata"]
    assert sorted(history_meta["symbols_requested"]) == ["AAA", "BBB"]
    assert "BBB" in history_meta["symbols_with_usable_history"]


def test_portfolio_intelligence_missing_history_stays_safe_and_reports_insufficient_data(tmp_path):
    events = []

    def _telemetry(event, payload):
        events.append((event, dict(payload)))

    result = run_continuous_scan_cycle(
        database_url=f"sqlite:///{tmp_path / 'scan-insufficient.db'}",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 30, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: _Broker(),
        scan_runner=lambda _u: _scan_payload(),
        shortlist_runner=lambda *_args, **_kwargs: {"selected": [{"rank": 1, "symbol": "AAA", "score": 82.0}], "rejected": [], "portfolio_warnings": [], "selection_summary": {"selected_count": 1}},
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        history_batch_loader=lambda *_args, **_kwargs: {},
        history_single_loader=lambda *_args, **_kwargs: pd.DataFrame(),
        persist=True,
        dry_run=True,
        telemetry_callback=_telemetry,
    )

    assert result.execution_status == "completed"
    corr_status = result.selection["portfolio_intelligence"]["correlation_summary"]["status"]
    assert corr_status == "INSUFFICIENT_DATA"
    corr_event = [payload for name, payload in events if name == "correlation_analysis_complete"]
    assert corr_event
    assert corr_event[-1]["status"] == "INSUFFICIENT_DATA"
    assert int(corr_event[-1]["symbols_missing_history"]) >= 1


def test_portfolio_intelligence_history_fetch_errors_do_not_crash_scan_cycle(tmp_path):
    events = []

    def _telemetry(event, payload):
        events.append((event, dict(payload)))

    result = run_continuous_scan_cycle(
        database_url=f"sqlite:///{tmp_path / 'scan-history-error.db'}",
        config_loader=_config_loader,
        now_provider=lambda: datetime(2026, 7, 22, 10, 35, tzinfo=timezone.utc),
        broker_factory=lambda **kwargs: _Broker(),
        scan_runner=lambda _u: _scan_payload(),
        shortlist_runner=lambda *_args, **_kwargs: {"selected": [{"rank": 1, "symbol": "AAA", "score": 82.0}], "rejected": [], "portfolio_warnings": [], "selection_summary": {"selected_count": 1}},
        scan_persistor=lambda **kwargs: {"storage": "database", "run_id": kwargs["run_payload"]["run_id"]},
        execution_repo_factory=lambda **kwargs: _Repo(),
        positions_loader=_positions_loader,
        universe_loader=_universe_loader,
        history_batch_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("batch unavailable")),
        history_single_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("single unavailable")),
        persist=True,
        dry_run=True,
        telemetry_callback=_telemetry,
    )

    assert result.execution_status == "completed"
    names = [name for name, _ in events]
    assert "correlation_analysis_complete" in names
    assert "portfolio_intelligence_failed" not in names
