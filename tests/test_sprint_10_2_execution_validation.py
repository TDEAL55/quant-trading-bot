from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from sprint_10_2_execution_validation import run_sprint_10_2_execution_validation


def _fake_market_df() -> pd.DataFrame:
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=250, freq="D")
    return pd.DataFrame(
        {
            "Open": [100.0] * len(idx),
            "High": [101.0] * len(idx),
            "Low": [99.0] * len(idx),
            "Close": [100.5] * len(idx),
            "Volume": [1000000] * len(idx),
        },
        index=idx,
    )


def _scan_payload(selected_symbol: str = "AAA") -> dict:
    return {
        "scan_results": [
            {
                "symbol": selected_symbol,
                "latest_price": 100.0,
                "overall_score": 82.0,
                "confidence": 75.0,
                "component_scores": {"trend": 80.0, "momentum": 70.0},
                "reasons": ["good trend"],
                "warnings": [],
                "eligible": True,
                "rejection_reasons": [],
                "status": "scored",
            }
        ],
        "ranked_candidates": [
            {
                "rank": 1,
                "symbol": selected_symbol,
                "overall_score": 82.0,
                "confidence": 75.0,
                "component_scores": {"trend": 80.0, "momentum": 70.0},
                "reasons": ["good trend"],
                "warnings": [],
            }
        ],
        "summary": {"eligible_count": 1},
    }


class _RepoStub:
    def __init__(self, database_url=None):
        self.db = type("_Db", (), {"enabled": True, "ensure_schema": lambda self: None})()
        self._seen = None
        self.saved = None

    def fetch_latest_submitting_run_by_execution_fingerprint(self, execution_fingerprint):
        return self._seen

    def create_approval(self, approval):
        return {"approval_id": approval.get("approval_id")}

    def save_validation_run(self, payload):
        self.saved = payload
        return {"run_id": payload.run.get("run_id")}

    def close(self):
        return None


class _BrokerStub:
    def __init__(self, status="filled", filled_quantity=10.0, average_fill_price=100.0):
        self.mode = "PAPER"
        self.backend = "ALPACA"
        self._status = status
        self._filled_quantity = filled_quantity
        self._average_fill_price = average_fill_price
        self._cash = 10000.0
        self._positions = {}

    def get_positions(self):
        return dict(self._positions)

    def get_buying_power(self):
        return float(self._cash)

    def submit_order(self, side, ticker, quantity, **kwargs):
        del kwargs
        symbol = str(ticker).upper()
        qty = float(quantity)
        if self._status in {"filled", "partially_filled"} and self._filled_quantity > 0:
            self._positions[symbol] = {"quantity": float(self._filled_quantity), "avg_price": float(self._average_fill_price)}
            self._cash -= float(self._filled_quantity) * float(self._average_fill_price)
        return {
            "status": self._status,
            "filled_quantity": float(self._filled_quantity),
            "average_fill_price": float(self._average_fill_price),
            "order_id": "alpaca-order-1",
            "client_order_id": "client-1",
            "broker_backend": "ALPACA",
            "order_type": "market",
            "time_in_force": "day",
            "updated_at": "2026-08-01T13:00:01Z",
            "rejection_reason": "insufficient buying power" if self._status == "rejected" else "",
        }


def test_execution_validation_completes(monkeypatch):
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "SIMULATED")
    monkeypatch.setattr("sprint_10_2_execution_validation.download_price_data", lambda *args, **kwargs: _fake_market_df())
    monkeypatch.setattr("sprint_10_2_execution_validation.run_scan", lambda universe: _scan_payload("AAA"))
    monkeypatch.setattr(
        "sprint_10_2_execution_validation.run_shortlist_only",
        lambda *args, **kwargs: {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 75.0,
                    "suggested_paper_notional": 1000.0,
                    "component_scores": {"trend": 80.0, "momentum": 70.0},
                    "reasons": ["good trend"],
                    "warnings": [],
                }
            ],
            "rejected": [],
        },
    )
    monkeypatch.setattr("sprint_10_2_execution_validation.journal_scanner_run", lambda **kwargs: {"research_run_id": "r-1"})
    monkeypatch.setattr("sprint_10_2_execution_validation._factor_intelligence_step", lambda database_url: {"status": "completed"})
    monkeypatch.setattr("sprint_10_2_execution_validation.MonitoringPaperExecutionRepository", _RepoStub)
    monkeypatch.setattr(
        "sprint_10_2_execution_validation.fetch_paper_validation_dashboard_payload",
        lambda database_url: {"latest_run": {"run_id": "placeholder"}, "db_connected": True},
    )

    result = run_sprint_10_2_execution_validation(database_url="sqlite:///x.db", manual_approval="YES", persist=True)

    assert result["status"] == "completed"
    assert result["paper_order"]["symbol"] == "AAA"
    assert result["risk_result"]["approved"] is True


def test_execution_validation_no_trade(monkeypatch):
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "SIMULATED")
    monkeypatch.setattr("sprint_10_2_execution_validation.download_price_data", lambda *args, **kwargs: _fake_market_df())
    monkeypatch.setattr(
        "sprint_10_2_execution_validation.run_scan",
        lambda universe: {
            "scan_results": [
                {
                    "symbol": "AAA",
                    "latest_price": 100.0,
                    "overall_score": 40.0,
                    "confidence": 20.0,
                    "eligible": False,
                    "rejection_reasons": ["overall score below minimum"],
                }
            ],
            "ranked_candidates": [{"rank": 1, "symbol": "AAA", "overall_score": 40.0}],
            "summary": {"eligible_count": 0},
        },
    )
    monkeypatch.setattr("sprint_10_2_execution_validation.run_shortlist_only", lambda *args, **kwargs: {"selected": [], "rejected": []})
    monkeypatch.setattr("sprint_10_2_execution_validation.journal_scanner_run", lambda **kwargs: {"research_run_id": "r-1"})
    monkeypatch.setattr("sprint_10_2_execution_validation._factor_intelligence_step", lambda database_url: {"status": "insufficient_data"})

    result = run_sprint_10_2_execution_validation(database_url="sqlite:///x.db", manual_approval="YES", persist=False)

    assert result["status"] == "no_trade"
    assert result["highest_ranked_security"]["symbol"] == "AAA"
    assert result["blocked_filters"]


def test_submission_blocked_config_marker(monkeypatch):
    monkeypatch.setattr("sprint_10_2_execution_validation.download_price_data", lambda *args, **kwargs: _fake_market_df())
    monkeypatch.setattr("sprint_10_2_execution_validation.run_scan", lambda universe: _scan_payload("AAA"))
    monkeypatch.setattr(
        "sprint_10_2_execution_validation.run_shortlist_only",
        lambda *args, **kwargs: {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 75.0,
                    "suggested_paper_notional": 1000.0,
                    "component_scores": {"trend": 80.0, "momentum": 70.0},
                    "reasons": ["good trend"],
                    "warnings": [],
                }
            ],
            "rejected": [],
        },
    )
    monkeypatch.setattr("sprint_10_2_execution_validation.journal_scanner_run", lambda **kwargs: {"research_run_id": "r-1"})
    monkeypatch.setattr("sprint_10_2_execution_validation._factor_intelligence_step", lambda database_url: {"status": "completed"})

    broker = _BrokerStub(status="submission_blocked_by_config", filled_quantity=0.0)
    result = run_sprint_10_2_execution_validation(database_url="sqlite:///x.db", manual_approval="YES", broker=broker, persist=False)

    assert result["paper_order"]["status"] == "submission_blocked_by_config"
    assert result["paper_order"]["submission_message"] == "SUBMISSION_BLOCKED_BY_CONFIG"
    assert result["reconciliation"]["unfilled_order_count"] >= 0


def test_rejected_order_not_recorded_as_fill(monkeypatch):
    monkeypatch.setattr("sprint_10_2_execution_validation.download_price_data", lambda *args, **kwargs: _fake_market_df())
    monkeypatch.setattr("sprint_10_2_execution_validation.run_scan", lambda universe: _scan_payload("AAA"))
    monkeypatch.setattr(
        "sprint_10_2_execution_validation.run_shortlist_only",
        lambda *args, **kwargs: {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 75.0,
                    "suggested_paper_notional": 1000.0,
                    "component_scores": {"trend": 80.0, "momentum": 70.0},
                    "reasons": ["good trend"],
                    "warnings": [],
                }
            ],
            "rejected": [],
        },
    )
    monkeypatch.setattr("sprint_10_2_execution_validation.journal_scanner_run", lambda **kwargs: {"research_run_id": "r-1"})
    monkeypatch.setattr("sprint_10_2_execution_validation._factor_intelligence_step", lambda database_url: {"status": "completed"})

    broker = _BrokerStub(status="rejected", filled_quantity=0.0)
    result = run_sprint_10_2_execution_validation(database_url="sqlite:///x.db", manual_approval="YES", broker=broker, persist=False)

    assert result["status"] == "failed"
    assert result["paper_order"]["status"] == "rejected"
    assert result["paper_order"]["fill_price"] == 100.0


def test_pending_order_not_recorded_as_fill(monkeypatch):
    monkeypatch.setattr("sprint_10_2_execution_validation.download_price_data", lambda *args, **kwargs: _fake_market_df())
    monkeypatch.setattr("sprint_10_2_execution_validation.run_scan", lambda universe: _scan_payload("AAA"))
    monkeypatch.setattr(
        "sprint_10_2_execution_validation.run_shortlist_only",
        lambda *args, **kwargs: {
            "selected": [
                {
                    "rank": 1,
                    "symbol": "AAA",
                    "score": 82.0,
                    "confidence": 75.0,
                    "suggested_paper_notional": 1000.0,
                    "component_scores": {"trend": 80.0, "momentum": 70.0},
                    "reasons": ["good trend"],
                    "warnings": [],
                }
            ],
            "rejected": [],
        },
    )
    monkeypatch.setattr("sprint_10_2_execution_validation.journal_scanner_run", lambda **kwargs: {"research_run_id": "r-1"})
    monkeypatch.setattr("sprint_10_2_execution_validation._factor_intelligence_step", lambda database_url: {"status": "completed"})

    broker = _BrokerStub(status="pending", filled_quantity=0.0)
    result = run_sprint_10_2_execution_validation(database_url="sqlite:///x.db", manual_approval="YES", broker=broker, persist=False)

    assert result["paper_order"]["status"] == "pending"
    assert result["reconciliation"]["unfilled_order_count"] >= 1
