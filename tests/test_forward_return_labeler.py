from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from evaluation_repository import MonitoringEvaluationRepository
from forward_return_labeler import label_research_candidates
from research_journal import journal_scanner_run
from research_repository import MonitoringResearchRepository


REPO_ROOT = Path(__file__).resolve().parents[1]


def _frame(dates, closes):
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.DataFrame(
        {
            "open": [value * 0.995 for value in closes],
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "adj_close": closes,
            "volume": [1_000_000] * len(closes),
        },
        index=index,
    )


def _business_dates(start: str, periods: int):
    return list(pd.bdate_range(start=start, periods=periods, tz="UTC"))


def _scan_row(symbol: str, created_at: str, latest_price: float, overall_score: float = 80.0, confidence: float = 70.0, signal: str = "BUY", regime: str = "strong_bull", sector: str = "Technology", industry: str = "Software"):
    return {
        "symbol": symbol,
        "company_name": symbol,
        "sector": sector,
        "industry": industry,
        "scan_timestamp": created_at,
        "latest_price": latest_price,
        "average_dollar_volume": 5_000_000,
        "overall_score": overall_score,
        "confidence": confidence,
        "signal": signal,
        "regime": regime,
        "component_scores": {"trend": 80.0, "momentum": 70.0, "volume": 60.0, "volatility": 50.0, "market_regime": 65.0, "risk_quality": 60.0},
        "reasons": ["trend confirmed"],
        "warnings": [],
        "data_quality": {"history_sufficient": True, "factor": {}},
        "eligible": True,
        "rejection_reasons": [],
        "rank": 1,
        "ranking_score": 90.0,
        "status": "scored",
    }


def _seed_research_run(tmp_path, symbol: str, created_at: str, latest_price: float = 100.0, **row_kwargs):
    db_url = f"sqlite:///{tmp_path / 'research.db'}"
    payload = {
        "summary": {
            "started_at": created_at,
            "scan_started_at": created_at,
            "completed_at": created_at,
            "benchmark_symbol": "SPY",
            "market_regime": row_kwargs.get("regime", "strong_bull"),
            "symbol_count": 1,
            "eligible_count": 1,
            "rejection_count": 0,
            "error_count": 0,
            "duration_seconds": 0.1,
            "status": "completed",
        },
        "scan_results": [_scan_row(symbol, created_at, latest_price, **row_kwargs)],
        "ranked_candidates": [_scan_row(symbol, created_at, latest_price, **row_kwargs)],
    }
    journal_scanner_run(payload, research_run_id="research-1", database_url=db_url, data_source="synthetic", data_mode="research")
    return db_url


def test_labels_exact_forward_returns_and_benchmark_returns(tmp_path):
    created_at = "2024-01-02T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    dates = _business_dates("2024-01-02", 25)
    symbol_frame = _frame(dates, [100 + i for i in range(25)])
    benchmark_frame = _frame(dates, [100.0] * 25)

    def loader(symbol, start, end):
        return benchmark_frame if symbol == "SPY" else symbol_frame

    result = label_research_candidates(database_url=db_url, data_loader=loader)
    assert result["candidates_processed"] == 1
    record = result["records"][0]
    assert record["label_status"] == "complete"
    assert record["forward_1d_return"] == pytest.approx(0.01, rel=1e-6)
    assert record["forward_5d_return"] == pytest.approx(0.05, rel=1e-6)
    assert record["forward_10d_return"] == pytest.approx(0.10, rel=1e-6)
    assert record["forward_20d_return"] == pytest.approx(0.20, rel=1e-6)
    assert record["forward_1d_benchmark_return"] == pytest.approx(0.0, abs=1e-9)
    assert record["forward_20d_excess_return"] == pytest.approx(0.20, rel=1e-6)


def test_weekend_and_missing_session_handling(tmp_path):
    created_at = "2024-01-06T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    dates = [
        pd.Timestamp("2024-01-05", tz="UTC"),
        pd.Timestamp("2024-01-08", tz="UTC"),
        pd.Timestamp("2024-01-09", tz="UTC"),
    ]
    symbol_frame = _frame(dates, [100.0, 102.0, 103.0])
    benchmark_frame = _frame(dates, [100.0, 100.0, 100.0])

    def loader(symbol, start, end):
        return benchmark_frame if symbol == "SPY" else symbol_frame

    result = label_research_candidates(database_url=db_url, data_loader=loader)
    record = result["records"][0]
    assert record["observation_date"] == "2024-01-06"
    assert record["forward_1d_target_date"] == "2024-01-08"
    assert record["forward_1d_return"] == pytest.approx(0.02, rel=1e-6)


def test_unsorted_and_duplicate_price_data_are_normalized(tmp_path):
    created_at = "2024-01-01T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    symbol_frame = _frame(
        ["2024-01-03", "2024-01-01", "2024-01-01", "2024-01-02"],
        [102.0, 100.0, 100.0, 101.0],
    )
    benchmark_frame = _frame(
        ["2024-01-03", "2024-01-01", "2024-01-02"],
        [100.0, 100.0, 100.0],
    )

    def loader(symbol, start, end):
        return benchmark_frame if symbol == "SPY" else symbol_frame

    result = label_research_candidates(database_url=db_url, data_loader=loader)
    record = result["records"][0]
    assert record["forward_1d_return"] == pytest.approx(0.01, rel=1e-6)
    assert record["forward_1d_target_date"] == "2024-01-02"


def test_partial_labeling_and_update_to_complete(tmp_path, monkeypatch):
    partial_dates = list(pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=5))
    created_at = f"{partial_dates[0].date().isoformat()}T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    partial_symbol_frame = _frame(partial_dates, [100.0, 101.0, 102.0, 103.0, 104.0])
    partial_benchmark_frame = _frame(partial_dates, [100.0] * 5)

    def partial_loader(symbol, start, end):
        return partial_benchmark_frame if symbol == "SPY" else partial_symbol_frame

    monkeypatch.setattr("forward_return_labeler._current_date", lambda: partial_dates[-1].date())

    first = label_research_candidates(database_url=db_url, data_loader=partial_loader)
    assert first["label_status_counts"]["partial"] == 1
    repo = MonitoringEvaluationRepository(database_url=db_url)
    row = repo.fetch_evaluation_by_candidate_id(1)
    assert row["label_status"] == "partial"
    assert row["forward_1d_status"] == "complete"
    assert row["forward_20d_status"] == "pending"
    repo.close()

    complete_dates = _business_dates("2026-07-10", 25)
    complete_symbol_frame = _frame(complete_dates, [100.0 + i for i in range(25)])
    complete_benchmark_frame = _frame(complete_dates, [100.0] * 25)

    def complete_loader(symbol, start, end):
        return complete_benchmark_frame if symbol == "SPY" else complete_symbol_frame

    second = label_research_candidates(database_url=db_url, data_loader=complete_loader)
    assert second["label_status_counts"]["complete"] == 1
    repo = MonitoringEvaluationRepository(database_url=db_url)
    row = repo.fetch_evaluation_by_candidate_id(1)
    assert row["label_status"] == "complete"
    assert row["forward_20d_status"] == "complete"
    assert repo.count_total_evaluations() == 1
    repo.close()


def test_unavailable_label_when_observation_missing(tmp_path):
    created_at = "2024-01-01T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    symbol_frame = _frame(["2024-01-10", "2024-01-11", "2024-01-12"], [101.0, 102.0, 103.0])
    benchmark_frame = _frame(["2024-01-10", "2024-01-11", "2024-01-12"], [100.0, 100.0, 100.0])

    def loader(symbol, start, end):
        return benchmark_frame if symbol == "SPY" else symbol_frame

    result = label_research_candidates(database_url=db_url, data_loader=loader)
    record = result["records"][0]
    assert record["label_status"] == "unavailable"
    assert record["forward_1d_status"] == "unavailable"


def test_data_error_handling_for_missing_benchmark_data(tmp_path):
    created_at = "2024-01-02T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    dates = _business_dates("2024-01-02", 10)
    symbol_frame = _frame(dates, [100.0 + i for i in range(10)])

    def loader(symbol, start, end):
        if symbol == "SPY":
            raise RuntimeError("benchmark unavailable")
        return symbol_frame

    result = label_research_candidates(database_url=db_url, data_loader=loader, retry_data_errors=0)
    record = result["records"][0]
    assert record["label_status"] == "data_error"
    assert "benchmark unavailable" in record["error_message"]


def test_idempotent_reruns_do_not_duplicate_labels(tmp_path):
    created_at = "2024-01-02T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    dates = _business_dates("2024-01-02", 25)
    symbol_frame = _frame(dates, [100.0 + i for i in range(25)])
    benchmark_frame = _frame(dates, [100.0] * 25)

    def loader(symbol, start, end):
        return benchmark_frame if symbol == "SPY" else symbol_frame

    first = label_research_candidates(database_url=db_url, data_loader=loader)
    second = label_research_candidates(database_url=db_url, data_loader=loader)
    repo = MonitoringEvaluationRepository(database_url=db_url)
    assert repo.count_total_evaluations() == 1
    assert first["candidates_processed"] == 1
    assert second["candidates_processed"] == 0
    repo.close()


def test_labeler_does_not_modify_original_research_record(tmp_path):
    created_at = "2024-01-02T15:00:00+00:00"
    db_url = _seed_research_run(tmp_path, "AAA", created_at, latest_price=100.0)
    research_repo = MonitoringResearchRepository(database_url=db_url)
    original = deepcopy(research_repo.fetch_research_candidates_for_run("research-1")[0])
    dates = _business_dates("2024-01-02", 25)
    symbol_frame = _frame(dates, [100.0 + i for i in range(25)])
    benchmark_frame = _frame(dates, [100.0] * 25)

    def loader(symbol, start, end):
        return benchmark_frame if symbol == "SPY" else symbol_frame

    label_research_candidates(database_url=db_url, data_loader=loader)
    after = research_repo.fetch_research_candidates_for_run("research-1")[0]
    assert after["latest_price"] == original["latest_price"]
    assert after["symbol"] == original["symbol"]
    research_repo.db.close()