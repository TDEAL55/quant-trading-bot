from datetime import datetime, timedelta, timezone

import pytest

from paper_approval import build_approval_record
from paper_validation import _run_fingerprint, run_paper_validation


class _FakeEvalDb:
    enabled = True

    def ensure_schema(self):
        return None


class _FakeEvalRepo:
    def __init__(self, database_url=None):
        self.db = _FakeEvalDb()

    def fetch_evaluation_rows_for_dashboard(self, limit=20000):
        rows = []
        for idx, symbol in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], start=1):
            rows.append(
                {
                    "research_candidate_id": idx,
                    "research_run_id": "run-1",
                    "symbol": symbol,
                    "observation_date": "2026-07-18",
                    "rank": idx,
                    "overall_score": 70 - idx,
                    "confidence": 65 - idx,
                    "signal": "BUY",
                    "market_regime": "bull",
                    "sector": "Technology" if idx % 2 == 0 else "Healthcare",
                    "candidate_latest_price": 100 + idx,
                    "trend_score": 65 + idx,
                    "momentum_score": 60 + idx,
                    "volume_score": 55 + idx,
                    "volatility_score": 50 + idx,
                    "liquidity_score": 45 + idx,
                    "market_regime_score": 52 + idx,
                    "risk_quality_score": 58 + idx,
                    "forward_20d_return": 0.01,
                    "forward_20d_benchmark_return": 0.005,
                    "forward_20d_excess_return": 0.005,
                    "forward_20d_status": "complete",
                }
            )
        return rows

    def close(self):
        return None


class _FakeBroker:
    def __init__(self, mode="SIMULATION"):
        self.mode = mode
        self._positions = {
            "AAA": {"quantity": 10.0, "avg_price": 100.0},
            "BBB": {"quantity": 20.0, "avg_price": 100.0},
            "CCC": {"quantity": 15.0, "avg_price": 100.0},
            "DDD": {"quantity": 8.0, "avg_price": 100.0},
        }
        self._buying_power = 5000.0

    def get_positions(self):
        return self._positions

    def get_buying_power(self):
        return self._buying_power

    def submit_order(self, side, ticker, quantity, **kwargs):
        qty = float(quantity or 0.0)
        symbol = str(ticker or "").upper()
        if str(side or "").lower() == "buy":
            self._positions[symbol] = {
                "quantity": float((self._positions.get(symbol) or {}).get("quantity", 0.0)) + qty,
                "avg_price": 100.0,
            }
            self._buying_power -= qty * 100.0
        else:
            current = float((self._positions.get(symbol) or {}).get("quantity", 0.0))
            remaining = max(current - qty, 0.0)
            if remaining <= 0:
                self._positions.pop(symbol, None)
            else:
                self._positions[symbol] = {"quantity": remaining, "avg_price": 100.0}
            self._buying_power += qty * 100.0
        return {"status": "filled", "filled_quantity": quantity, "average_fill_price": 100.0, "order_id": f"ord-{ticker}"}


class _FakeRepo:
    def __init__(self, approval):
        self.db = type("_Db", (), {"enabled": True, "ensure_schema": lambda self: None})()
        self._approval = approval
        self.saved_payload = None
        self._runs = {}
        self._execution_runs = {}

    def fetch_approval(self, approval_id):
        return self._approval if self._approval.get("approval_id") == approval_id else None

    def fetch_run_by_fingerprint(self, run_fingerprint):
        return self._runs.get(run_fingerprint)

    def fetch_latest_submitting_run_by_execution_fingerprint(self, execution_fingerprint):
        return self._execution_runs.get(execution_fingerprint)

    def save_validation_run(self, payload):
        self.saved_payload = payload
        self._runs[payload.run.get("run_fingerprint")] = payload.run
        if not bool(payload.run.get("dry_run", True)) and int(payload.run.get("submitted_order_count") or 0) > 0:
            self._execution_runs[payload.run.get("execution_fingerprint")] = payload.run
        return {"run_id": payload.run.get("run_id")}

    def seed_execution_run(self, execution_fingerprint, status, run_id="prior-run", submitted_order_count=1):
        self._execution_runs[str(execution_fingerprint)] = {
            "run_id": str(run_id),
            "execution_fingerprint": str(execution_fingerprint),
            "status": str(status),
            "dry_run": False,
            "submitted_order_count": int(submitted_order_count),
        }

    def close(self):
        return None


@pytest.fixture
def valid_approval():
    now = datetime.now(timezone.utc)
    return build_approval_record(
        approval_id="ap-1",
        strategy_id="baseline_scanner",
        strategy_version="v1",
        strategy_fingerprint="fp-1",
        portfolio_configuration={"top_n": 5, "weighting_method": "equal_weight", "min_holdings": 1},
        risk_configuration={"max_position_size": 0.25, "max_daily_loss": 500, "daily_loss_limit": 500},
        benchmark="SPY",
        horizon=20,
        approved_by="tester",
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        enabled=True,
        notes="ok",
    )


class _SnapshotContext:
    @staticmethod
    def build(observation_date="2026-07-18"):
        from paper_validation import RunnerContext

        rows = []
        for rank, symbol in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], start=1):
            rows.append(
                {
                    "_run_id": "run-1",
                    "_observation_date": observation_date,
                    "_rank": rank,
                    "_symbol": symbol,
                    "symbol": symbol,
                    "candidate_latest_price": 100.0,
                }
            )
        return RunnerContext(
            strategy_definition={"strategy_id": "baseline_scanner"},
            approval={},
            latest_rows=rows,
            latest_research_run_id="run-1",
            latest_observation_date=observation_date,
        )


def _fake_target(*args, **kwargs):
    holdings = [
        {"symbol": "AAA", "weight": 0.20},
        {"symbol": "BBB", "weight": 0.20},
        {"symbol": "CCC", "weight": 0.12},
        {"symbol": "EEE", "weight": 0.08},
        {"symbol": "FFF", "weight": 0.40},
    ]
    return ({"AAA": 0.20, "BBB": 0.20, "CCC": 0.12, "EEE": 0.08, "FFF": 0.40}, {"largest_sector_weight": 0.35}, holdings)


def _fake_plan(*args, **kwargs):
    return {
        "orders": [
            {"symbol": "DDD", "side": "SELL", "quantity": 8.0, "notional": 800.0, "reference_price": 100.0, "target_weight": 0.0, "current_weight": 0.16, "weight_delta": -0.16},
            {"symbol": "CCC", "side": "SELL", "quantity": 5.0, "notional": 500.0, "reference_price": 100.0, "target_weight": 0.12, "current_weight": 0.30, "weight_delta": -0.18},
            {"symbol": "EEE", "side": "BUY", "quantity": 4.0, "notional": 400.0, "reference_price": 100.0, "target_weight": 0.08, "current_weight": 0.0, "weight_delta": 0.08},
            {"symbol": "BBB", "side": "BUY", "quantity": 5.0, "notional": 500.0, "reference_price": 100.0, "target_weight": 0.20, "current_weight": 0.15, "weight_delta": 0.05},
            {"symbol": "FFF", "side": "BUY", "quantity": 30.0, "notional": 3000.0, "reference_price": 100.0, "target_weight": 0.40, "current_weight": 0.0, "weight_delta": 0.40},
        ],
        "rejections": [],
        "holds": [{"symbol": "AAA", "reason": "within_tolerance"}],
        "summary": {"estimated_turnover": 0.44},
    }


def _seed_execution_fingerprint(now_ts, approval_id="ap-1"):
    target_payload = {
        "weights": {"AAA": 0.20, "BBB": 0.20, "CCC": 0.12, "EEE": 0.08, "FFF": 0.40},
        "holdings": sorted(
            [
                {"symbol": "AAA", "weight": 0.20},
                {"symbol": "BBB", "weight": 0.20},
                {"symbol": "CCC", "weight": 0.12},
                {"symbol": "EEE", "weight": 0.08},
                {"symbol": "FFF", "weight": 0.40},
            ],
            key=lambda row: row["symbol"],
        ),
        "research_run_id": "run-1",
        "scanner_timestamp": "2026-07-18",
    }
    import hashlib
    import json

    target_fingerprint = hashlib.sha256(json.dumps(target_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return _run_fingerprint(
        approval_id=approval_id,
        strategy_fingerprint="fp-1",
        research_run_id="run-1",
        scanner_timestamp="2026-07-18",
        target_portfolio_fingerprint=target_fingerprint,
        mode="PAPER",
    )


def test_paper_validation_rejects_live_mode(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    with pytest.raises(RuntimeError, match="LIVE mode is rejected"):
        run_paper_validation(database_url="sqlite:///x.db", approval_id="ap-1", mode="LIVE", dry_run=True, execute=False, confirm=False)


def test_paper_validation_dry_run_does_not_submit(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    broker = _FakeBroker(mode="SIMULATION")
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation.MonitoringEvaluationRepository", _FakeEvalRepo)
    monkeypatch.setattr("paper_validation._build_runner_context", lambda *args, **kwargs: _SnapshotContext.build("2026-07-18"))
    monkeypatch.setattr("paper_validation._build_target_from_snapshot", _fake_target)
    monkeypatch.setattr("paper_validation.plan_paper_orders", _fake_plan)
    result = run_paper_validation(
        database_url="sqlite:///x.db",
        approval_id="ap-1",
        mode="SIMULATION",
        dry_run=True,
        execute=False,
        confirm=False,
        broker=broker,
        now_ts="2026-07-18T12:00:00+00:00",
    )
    assert result["dry_run"] is True
    assert all(str(order.get("submission_status")) != "submitted" for order in result["proposed_orders"])


def test_paper_validation_execute_requires_confirm(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation.MonitoringEvaluationRepository", _FakeEvalRepo)
    with pytest.raises(RuntimeError, match="explicit confirmation"):
        run_paper_validation(
            database_url="sqlite:///x.db",
            approval_id="ap-1",
            mode="SIMULATION",
            dry_run=False,
            execute=True,
            confirm=False,
            broker=_FakeBroker(mode="SIMULATION"),
        )


def test_paper_validation_rejects_live_broker_boundary(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation.MonitoringEvaluationRepository", _FakeEvalRepo)
    with pytest.raises(RuntimeError, match="LIVE broker is rejected"):
        run_paper_validation(
            database_url="sqlite:///x.db",
            approval_id="ap-1",
            mode="SIMULATION",
            dry_run=True,
            execute=False,
            confirm=False,
            broker=_FakeBroker(mode="LIVE"),
            now_ts="2026-07-18T12:00:00+00:00",
        )


def test_validate_approval_cli_live_rejected(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    result = __import__("paper_validation").validate_approval_cli("sqlite:///x.db", "ap-1", "LIVE")
    assert result["valid"] is False
    assert any("LIVE mode is rejected" in item for item in result["reasons"])


def test_cli_execute_paper_rejects_live_mode(monkeypatch):
    paper_validation = __import__("paper_validation")
    monkeypatch.setattr(
        "sys.argv",
        [
            "paper_validation.py",
            "--database-url",
            "sqlite:///x.db",
            "execute-paper",
            "--approval-id",
            "ap-live",
            "--mode",
            "LIVE",
            "--confirm",
        ],
    )
    with pytest.raises(RuntimeError, match="LIVE mode is rejected"):
        paper_validation.main()


def test_dry_run_then_execution_distinct_ids_and_duplicate_block(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    monkeypatch.setattr("paper_validation.PAPER_VALIDATION_ENABLED", True)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation._build_runner_context", lambda *args, **kwargs: _SnapshotContext.build("2026-07-18"))
    monkeypatch.setattr("paper_validation._build_target_from_snapshot", _fake_target)
    monkeypatch.setattr("paper_validation.plan_paper_orders", _fake_plan)

    dry_result = run_paper_validation(
        database_url="sqlite:///x.db",
        approval_id="ap-1",
        mode="PAPER",
        dry_run=True,
        execute=False,
        confirm=False,
        broker=_FakeBroker(mode="PAPER"),
        now_ts="2026-07-18T12:00:00+00:00",
    )
    execute_result = run_paper_validation(
        database_url="sqlite:///x.db",
        approval_id="ap-1",
        mode="PAPER",
        dry_run=False,
        execute=True,
        confirm=True,
        broker=_FakeBroker(mode="PAPER"),
        now_ts="2026-07-18T12:00:00+00:00",
    )

    assert dry_result["run_id"] != execute_result["run_id"]
    assert dry_result["metrics"]["submitted_orders"] == 0
    assert execute_result["metrics"]["submitted_orders"] > 0
    assert execute_result["reconciliation"]["reconciliation_status"] in {"matched", "matched_with_tolerance"}
    assert execute_result["reconciliation"]["position_mismatch_count"] == 0

    with pytest.raises(RuntimeError, match="duplicate-run protection: prior_run_id="):
        run_paper_validation(
            database_url="sqlite:///x.db",
            approval_id="ap-1",
            mode="PAPER",
            dry_run=False,
            execute=True,
            confirm=True,
            broker=_FakeBroker(mode="PAPER"),
            now_ts="2026-07-18T12:00:00+00:00",
        )


def test_two_dry_runs_are_safe_non_submitting(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation._build_runner_context", lambda *args, **kwargs: _SnapshotContext.build("2026-07-18"))
    monkeypatch.setattr("paper_validation._build_target_from_snapshot", _fake_target)
    monkeypatch.setattr("paper_validation.plan_paper_orders", _fake_plan)

    first = run_paper_validation(
        database_url="sqlite:///x.db",
        approval_id="ap-1",
        mode="PAPER",
        dry_run=True,
        execute=False,
        confirm=False,
        broker=_FakeBroker(mode="PAPER"),
        now_ts="2026-07-18T12:00:00+00:00",
    )
    second = run_paper_validation(
        database_url="sqlite:///x.db",
        approval_id="ap-1",
        mode="PAPER",
        dry_run=True,
        execute=False,
        confirm=False,
        broker=_FakeBroker(mode="PAPER"),
        now_ts="2026-07-18T12:00:00+00:00",
    )

    assert first["run_id"] != second["run_id"]
    assert first["metrics"]["submitted_orders"] == 0
    assert second["metrics"]["submitted_orders"] == 0


def test_active_duplicate_execution_is_rejected(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    execution_fingerprint = _seed_execution_fingerprint("2026-07-18T12:00:00+00:00")
    fake_repo.seed_execution_run(execution_fingerprint=execution_fingerprint, status="running", run_id="active-run")

    monkeypatch.setattr("paper_validation.PAPER_VALIDATION_ENABLED", True)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation._build_runner_context", lambda *args, **kwargs: _SnapshotContext.build("2026-07-18"))
    monkeypatch.setattr("paper_validation._build_target_from_snapshot", _fake_target)
    monkeypatch.setattr("paper_validation.plan_paper_orders", _fake_plan)

    with pytest.raises(RuntimeError, match="prior_run_id=active-run status=running"):
        run_paper_validation(
            database_url="sqlite:///x.db",
            approval_id="ap-1",
            mode="PAPER",
            dry_run=False,
            execute=True,
            confirm=True,
            broker=_FakeBroker(mode="PAPER"),
            now_ts="2026-07-18T12:00:00+00:00",
        )


def test_failed_run_retry_requires_override(monkeypatch, valid_approval):
    fake_repo = _FakeRepo(valid_approval)
    execution_fingerprint = _seed_execution_fingerprint("2026-07-18T12:00:00+00:00")
    fake_repo.seed_execution_run(execution_fingerprint=execution_fingerprint, status="failed", run_id="failed-run")

    monkeypatch.setattr("paper_validation.PAPER_VALIDATION_ENABLED", True)
    monkeypatch.setattr("paper_validation.MonitoringPaperExecutionRepository", lambda database_url=None: fake_repo)
    monkeypatch.setattr("paper_validation._build_runner_context", lambda *args, **kwargs: _SnapshotContext.build("2026-07-18"))
    monkeypatch.setattr("paper_validation._build_target_from_snapshot", _fake_target)
    monkeypatch.setattr("paper_validation.plan_paper_orders", _fake_plan)

    with pytest.raises(RuntimeError, match="failed-run retry requires explicit override: prior_run_id=failed-run"):
        run_paper_validation(
            database_url="sqlite:///x.db",
            approval_id="ap-1",
            mode="PAPER",
            dry_run=False,
            execute=True,
            confirm=True,
            broker=_FakeBroker(mode="PAPER"),
            now_ts="2026-07-18T12:00:00+00:00",
        )

    retry = run_paper_validation(
        database_url="sqlite:///x.db",
        approval_id="ap-1",
        mode="PAPER",
        dry_run=False,
        execute=True,
        confirm=True,
        broker=_FakeBroker(mode="PAPER"),
        now_ts="2026-07-18T12:00:00+00:00",
        allow_failed_retry=True,
    )
    assert retry["status"] == "completed"
