from paper_broker import SimulatedPaperBroker
from paper_exit_execution import execute_guard_exit, load_stock_entry_contexts


class _Repo:
    def __init__(self, duplicate=None):
        self.duplicate = duplicate
        self.validation_payload = None
        self.closed_trade = None
        self.transitions = []

    def fetch_latest_submitting_run_by_execution_fingerprint(self, fingerprint):
        return self.duplicate

    def fetch_latest_filled_buy(self, symbol):
        return {
            "symbol": symbol,
            "average_fill_price": 100.0,
            "filled_at": "2026-08-01T14:00:00+00:00",
            "strategy_id": "multi_factor_v1",
            "strategy_version": "v1",
        }

    def save_validation_run(self, payload):
        self.validation_payload = payload

    def save_closed_trade(self, payload):
        self.closed_trade = payload

    def save_order_status_transitions(self, **kwargs):
        self.transitions.append(kwargs)


def _candidate():
    return {
        "symbol": "JPM",
        "quantity": 2.0,
        "current_market_price": 95.0,
        "return_percent": -5.0,
        "exit_reason": "stop_loss_threshold_reached",
    }


def test_guard_exit_fills_reconciles_and_records_closed_trade():
    broker = SimulatedPaperBroker(
        mode="PAPER",
        buying_power=1000.0,
        positions={"JPM": {"quantity": 2.0, "avg_price": 100.0}},
    )
    repo = _Repo()

    result = execute_guard_exit(
        _candidate(),
        broker=broker,
        broker_positions={"JPM": {"quantity": 2.0, "avg_price": 100.0, "current_price": 95.0}},
        broker_cash=1000.0,
        broker_buying_power=1000.0,
        broker_equity=1200.0,
        execution_repo=repo,
        cycle_run_id="cycle-1",
        started_at="2026-08-23T15:00:00+00:00",
        dry_run=False,
        paper_execution_enabled=True,
        allow_fractional=True,
        reconciliation_tolerance=0.000001,
        persist=True,
    )

    assert result["status"] == "completed"
    assert result["confirmed_order_count"] == 1
    assert result["execution_counters"]["orders_submitted"] == 1
    assert result["execution_counters"]["orders_filled"] == 1
    assert result["reconciliation"]["reconciliation_status"] == "matched"
    assert broker.get_positions() == {}
    assert repo.validation_payload is not None
    assert repo.closed_trade["symbol"] == "JPM"
    assert repo.closed_trade["exit_reason"] == "stop_loss_threshold_reached"
    assert repo.closed_trade["net_pnl"] == repo.closed_trade["realized_gross_pnl"]
    assert repo.closed_trade["estimated_slippage"] > 0.0
    assert repo.closed_trade["estimated_fees"] > 0.0


def test_guard_exit_duplicate_is_rejected_before_broker_submission():
    broker = SimulatedPaperBroker(
        mode="PAPER",
        buying_power=1000.0,
        positions={"JPM": {"quantity": 2.0, "avg_price": 100.0}},
    )
    repo = _Repo(duplicate={"run_id": "prior"})

    result = execute_guard_exit(
        _candidate(),
        broker=broker,
        broker_positions={"JPM": {"quantity": 2.0, "avg_price": 100.0, "current_price": 95.0}},
        broker_cash=1000.0,
        broker_buying_power=1000.0,
        broker_equity=1200.0,
        execution_repo=repo,
        cycle_run_id="cycle-2",
        started_at="2026-08-23T15:00:00+00:00",
        dry_run=False,
        paper_execution_enabled=True,
        allow_fractional=True,
        reconciliation_tolerance=0.000001,
        persist=True,
    )

    assert result["status"] == "duplicate_rejected"
    assert result["execution_counters"]["orders_submission_requested"] == 0
    assert broker.get_positions()["JPM"]["quantity"] == 2.0


def test_guard_exit_dry_run_is_recommendation_only():
    broker = SimulatedPaperBroker(
        mode="PAPER",
        buying_power=1000.0,
        positions={"JPM": {"quantity": 2.0, "avg_price": 100.0}},
    )

    result = execute_guard_exit(
        _candidate(),
        broker=broker,
        broker_positions={"JPM": {"quantity": 2.0, "avg_price": 100.0, "current_price": 95.0}},
        broker_cash=1000.0,
        broker_buying_power=1000.0,
        broker_equity=1200.0,
        execution_repo=_Repo(),
        cycle_run_id="cycle-3",
        started_at="2026-08-23T15:00:00+00:00",
        dry_run=True,
        paper_execution_enabled=True,
        allow_fractional=True,
        reconciliation_tolerance=0.000001,
        persist=True,
    )

    assert result["status"] == "exit_recommended"
    assert result["paper_order"]["submission_status"] == "not_submitted"
    assert broker.get_positions()["JPM"]["quantity"] == 2.0


def test_guard_exit_buys_to_cover_short_and_records_short_pnl():
    broker = SimulatedPaperBroker(
        mode="PAPER",
        buying_power=1200.0,
        positions={"BEAR": {"quantity": -2.0, "avg_price": 100.0}},
    )
    repo = _Repo()
    candidate = {
        "symbol": "BEAR",
        "quantity": 2.0,
        "current_market_price": 90.0,
        "return_percent": 10.0,
        "exit_reason": "take_profit_threshold_reached",
    }

    result = execute_guard_exit(
        candidate,
        broker=broker,
        broker_positions={"BEAR": {"quantity": -2.0, "avg_price": 100.0, "current_price": 90.0}},
        broker_cash=1200.0,
        broker_buying_power=1200.0,
        broker_equity=1000.0,
        execution_repo=repo,
        cycle_run_id="cycle-short",
        started_at="2026-08-23T15:00:00+00:00",
        dry_run=False,
        paper_execution_enabled=True,
        allow_fractional=True,
        reconciliation_tolerance=0.000001,
        persist=True,
    )

    assert result["status"] == "completed"
    assert result["paper_order"]["side"] == "BUY"
    assert broker.get_positions() == {}
    assert repo.closed_trade["realized_gross_pnl"] == 20.0


def test_guard_exit_records_broker_fill_when_position_snapshot_is_stale():
    class _StalePositionBroker(SimulatedPaperBroker):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.force_stale_snapshot = False

        def submit_order(self, side, ticker, quantity, **kwargs):
            order = super().submit_order(side, ticker, quantity, **kwargs)
            self.force_stale_snapshot = True
            return order

        def get_positions(self):
            if self.force_stale_snapshot:
                return {"JPM": {"quantity": 2.0, "avg_price": 100.0, "current_price": 95.0}}
            return super().get_positions()

    broker = _StalePositionBroker(
        mode="PAPER",
        buying_power=1000.0,
        positions={"JPM": {"quantity": 2.0, "avg_price": 100.0}},
    )
    repo = _Repo()

    result = execute_guard_exit(
        _candidate(),
        broker=broker,
        broker_positions={"JPM": {"quantity": 2.0, "avg_price": 100.0, "current_price": 95.0}},
        broker_cash=1000.0,
        broker_buying_power=1000.0,
        broker_equity=1200.0,
        execution_repo=repo,
        cycle_run_id="cycle-stale-snapshot",
        started_at="2026-08-23T15:00:00+00:00",
        dry_run=False,
        paper_execution_enabled=True,
        allow_fractional=True,
        reconciliation_tolerance=0.000001,
        persist=True,
    )

    assert result["status"] == "failed"
    assert result["paper_order"]["submission_status"] == "filled"
    assert result["reconciliation"]["reconciliation_status"] == "mismatch"
    assert repo.closed_trade["symbol"] == "JPM"
    assert repo.closed_trade["realized_gross_pnl"] == -10.0


def test_guard_exit_uses_broker_weighted_average_after_scale_ins():
    class _LatestEntryRepo(_Repo):
        def fetch_latest_filled_buy(self, symbol):
            return {
                "symbol": symbol,
                "average_fill_price": 110.0,
                "filled_at": "2026-08-02T14:00:00+00:00",
                "strategy_id": "stock_trend_ensemble_v2",
                "strategy_version": "2.0.0",
            }

    broker = SimulatedPaperBroker(
        mode="PAPER",
        buying_power=1000.0,
        positions={"JPM": {"quantity": 2.0, "avg_price": 105.0}},
    )
    repo = _LatestEntryRepo()

    execute_guard_exit(
        _candidate(),
        broker=broker,
        broker_positions={"JPM": {"quantity": 2.0, "avg_price": 105.0, "current_price": 95.0}},
        broker_cash=1000.0,
        broker_buying_power=1000.0,
        broker_equity=1200.0,
        execution_repo=repo,
        cycle_run_id="cycle-scale-in",
        started_at="2026-08-23T15:00:00+00:00",
        dry_run=False,
        paper_execution_enabled=True,
        allow_fractional=True,
        reconciliation_tolerance=0.000001,
        persist=True,
    )

    assert repo.closed_trade["entry_price"] == 105.0
    assert repo.closed_trade["realized_gross_pnl"] == -20.0
    assert repo.closed_trade["net_pnl"] == -20.0


def test_entry_context_loader_confirms_only_bot_attributed_v2_entries():
    class _AttributionRepo:
        def fetch_latest_filled_entry(self, symbol, side):
            expected_side = "SELL" if symbol == "BEAR" else "BUY"
            if side != expected_side:
                return None
            strategy_id = {
                "TREND": "stock_trend_ensemble_v2",
                "BEAR": "stock_bearish_trend_v2",
                "MANUAL": "stock_trend_ensemble_v2",
            }[symbol]
            return {
                "strategy_id": strategy_id,
                "strategy_version": "2.0.0",
                "filled_quantity": 2,
                "average_fill_price": 100,
                "filled_at": "2026-08-03T14:00:00+00:00",
                "order_payload": {
                    "source": "manual_import" if symbol == "MANUAL" else "continuous_scan_cycle",
                    "strategy": {"strategy_id": strategy_id, "stop": 94},
                },
            }

    contexts = load_stock_entry_contexts(
        {
            "TREND": {"quantity": 2},
            "BEAR": {"quantity": -2},
            "MANUAL": {"quantity": 2},
        },
        _AttributionRepo(),
    )

    assert contexts["TREND"]["bot_entry_confirmed"] is True
    assert contexts["TREND"]["entry_side"] == "BUY"
    assert contexts["BEAR"]["bot_entry_confirmed"] is True
    assert contexts["BEAR"]["entry_side"] == "SELL"
    assert contexts["MANUAL"]["bot_entry_attributed"] is False
    assert contexts["MANUAL"]["bot_entry_confirmed"] is False
