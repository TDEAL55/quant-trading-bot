import time

import pandas as pd

import market_scanner


def _frame(rows=260, base=100.0, step=0.2, volume=1_500_000):
    end = pd.Timestamp.now(tz="UTC").normalize()
    index = pd.bdate_range(end=end, periods=rows)
    close = pd.Series([base + (i * step) for i in range(rows)], index=index)
    return pd.DataFrame(
        {
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def test_scan_symbol_success(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "STRONG_BUY",
        "regime": "strong_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": ["trend strength"],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 8.0}}},
    })
    result = market_scanner.scan_symbol(
        {"symbol": "NVDA", "company_name": "NVIDIA", "sector": "Technology", "industry": "Semiconductors"},
        benchmark_history=_frame(),
        data_loader=lambda *args, **kwargs: _frame(),
    )
    assert result["status"] == "scored"
    assert result["eligible"]


def test_scan_symbol_rejected(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 50.0,
        "confidence": 40.0,
        "signal": "HOLD",
        "regime": "strong_bear",
        "component_scores": {"risk_quality": 30.0, "volatility": 20.0, "trend": 30.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
    })
    result = market_scanner.scan_symbol(
        {"symbol": "XYZ", "company_name": "XYZ", "sector": "Unknown", "industry": "Unknown"},
        benchmark_history=_frame(),
        data_loader=lambda *args, **kwargs: _frame(),
    )
    assert result["status"] == "rejected"
    assert not result["eligible"]


def test_scan_universe_continues_after_symbol_failure(monkeypatch):
    calls = {"count": 0}

    def loader(symbol, start, end):
        calls["count"] += 1
        if symbol == "BAD":
            raise RuntimeError("download failed")
        return _frame()

    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "weak_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 5.0}}},
    })

    payload = market_scanner.scan_universe(
        [{"symbol": "GOOD"}, {"symbol": "BAD"}, {"symbol": "GOOD"}],
        benchmark_symbol="SPY",
        data_loader=loader,
        max_workers=2,
        max_retries=0,
        batch_size=10,
    )
    assert len(payload["scan_results"]) == 3
    assert payload["summary"]["error_count"] == 1
    assert payload["summary"]["filtered_count_by_reason"].get("duplicate_symbol", 0) >= 1
    assert payload["summary"]["failed_symbol_count"] == 1


def test_rank_scan_results_deterministic_tie_breakers():
    rows = [
        {"symbol": "BBB", "eligible": True, "overall_score": 80, "confidence": 70, "average_dollar_volume": 40_000_000, "component_scores": {"risk_quality": 60, "trend": 70}},
        {"symbol": "AAA", "eligible": True, "overall_score": 80, "confidence": 70, "average_dollar_volume": 40_000_000, "component_scores": {"risk_quality": 60, "trend": 70}},
    ]
    ranked = market_scanner.rank_scan_results(rows)
    assert ranked[0]["symbol"] == "AAA"


def test_scan_universe_timeout_handling(monkeypatch):
    def _slow_loader(symbol, start, end):
        time.sleep(0.2)
        return _frame()

    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}],
        benchmark_symbol="SPY",
        data_loader=_slow_loader,
        max_scan_seconds=1,
        max_workers=1,
        batch_size=1,
    )
    assert payload["summary"]["error_count"] >= 1
    assert payload["summary"]["status"] in {"timed_out", "partial_success"}


def test_scan_universe_respects_batch_and_worker_summary(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "weak_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 5.0}}},
    })
    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        max_workers=2,
        max_retries=1,
        batch_size=2,
    )
    summary = payload["summary"]
    assert summary["max_workers"] == 2
    assert summary["batch_size"] == 2
    assert summary["universe_total_count"] == 3


def test_rate_limit_retries_are_bounded(monkeypatch):
    market_scanner._SCAN_HISTORY_CACHE.clear()
    calls = {"n": 0}

    def _loader(symbol, start, end):
        if symbol == "SPY":
            return _frame()
        calls["n"] += 1
        raise RuntimeError("429 rate limit")

    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}],
        benchmark_symbol="SPY",
        data_loader=_loader,
        max_workers=1,
        max_retries=2,
        batch_size=1,
    )
    assert payload["summary"]["error_count"] == 1
    assert payload["summary"]["rate_limit_retry_count"] <= 2
    assert payload["summary"]["retry_count"] <= 4
    assert calls["n"] <= 3


def test_scan_summary_reports_filter_counts_and_totals(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    def _loader(symbol, start, end):
        del start, end
        if symbol == "BADP":
            frame = _frame(base=1.0, volume=1000)
            return frame
        return _frame()

    payload = market_scanner.scan_universe(
        [
            {"symbol": "GOOD", "status": "ACTIVE", "tradable": True},
            {"symbol": "GOOD", "status": "ACTIVE", "tradable": True},
            {"symbol": "NOTRD", "status": "ACTIVE", "tradable": False},
            {"symbol": "INACT", "status": "INACTIVE", "tradable": True},
            {"symbol": "BADP", "status": "ACTIVE", "tradable": True},
        ],
        benchmark_symbol="SPY",
        data_loader=_loader,
        max_workers=2,
        max_retries=0,
        batch_size=3,
    )

    summary = payload["summary"]
    assert summary["universe_total_count"] == 5
    assert summary["symbol_count"] == 5
    assert summary["filtered_count_by_reason"].get("duplicate_symbol", 0) == 1
    assert summary["filtered_count_by_reason"].get("tradable_false_or_invalid", 0) >= 1
    assert summary["filtered_count_by_reason"].get("inactive", 0) >= 1
    assert summary["filtered_count_by_reason"].get("average_dollar_volume_below_configured_minimum", 0) >= 1


def test_scan_summary_counts_low_price_rejection(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    def _loader(symbol, start, end):
        del start, end
        if symbol == "LOWP":
            return _frame(base=1.0, step=0.01, volume=30_000_000)
        return _frame()

    payload = market_scanner.scan_universe(
        [{"symbol": "LOWP", "status": "ACTIVE", "tradable": True}],
        benchmark_symbol="SPY",
        data_loader=_loader,
        max_workers=1,
        max_retries=0,
        batch_size=1,
    )

    summary = payload["summary"]
    assert summary["filtered_count_by_reason"].get("price_below_configured_minimum", 0) >= 1
    assert summary["filtered_count_by_reason"].get("average_dollar_volume_below_configured_minimum", 0) == 0


def test_scan_summary_counts_low_dollar_volume_rejection(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    def _loader(symbol, start, end):
        del start, end
        if symbol == "LOWDV":
            return _frame(base=100.0, step=0.0, volume=1000)
        return _frame()

    payload = market_scanner.scan_universe(
        [{"symbol": "LOWDV", "status": "ACTIVE", "tradable": True}],
        benchmark_symbol="SPY",
        data_loader=_loader,
        max_workers=1,
        max_retries=0,
        batch_size=1,
    )

    summary = payload["summary"]
    assert summary["filtered_count_by_reason"].get("average_dollar_volume_below_configured_minimum", 0) >= 1
    assert summary["filtered_count_by_reason"].get("price_below_configured_minimum", 0) == 0


def test_scan_summary_rejection_precedence_when_price_and_liquidity_both_fail(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    def _loader(symbol, start, end):
        del start, end
        if symbol == "BOTHF":
            return _frame(base=1.0, step=0.01, volume=1000)
        return _frame()

    payload = market_scanner.scan_universe(
        [{"symbol": "BOTHF", "status": "ACTIVE", "tradable": True}],
        benchmark_symbol="SPY",
        data_loader=_loader,
        max_workers=1,
        max_retries=0,
        batch_size=1,
    )

    result = payload["scan_results"][0]
    assert result["status"] == "rejected"
    assert result["rejection_reasons"][0].startswith("price below minimum")
    assert any(reason.startswith("average dollar volume below minimum") for reason in result["rejection_reasons"])

    summary = payload["summary"]
    assert summary["filtered_count_by_reason"].get("price_below_configured_minimum", 0) >= 1
    assert summary["filtered_count_by_reason"].get("average_dollar_volume_below_configured_minimum", 0) >= 1


def test_scan_universe_emits_major_lifecycle_events(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "weak_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 5.0}}},
    })

    events = []

    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}, {"symbol": "BBB"}],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        max_workers=1,
        max_retries=0,
        batch_size=1,
        progress_every=1,
        progress_callback=lambda event: events.append(dict(event)),
    )

    names = [item.get("event") for item in events]
    assert "metadata_filter_complete" in names
    assert "lightweight_scan_start" in names
    assert "full_score_stage_start" in names
    assert "scan_progress" in names
    assert "ranking_complete" in names
    assert payload["summary"]["symbol_count"] == 2


def test_scan_universe_max_scan_seconds_marks_remaining_symbols(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "weak_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 5.0}}},
    })

    def _slow_loader(symbol, start, end):
        del symbol, start, end
        time.sleep(0.6)
        return _frame()

    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}],
        benchmark_symbol="SPY",
        data_loader=_slow_loader,
        max_workers=1,
        max_retries=0,
        batch_size=1,
        max_scan_seconds=1,
    )

    assert payload["summary"]["error_count"] >= 1


def test_stage_a_includes_large_universe_with_no_deep_overflow(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "weak_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 5.0}}},
    })

    symbols = [{"symbol": f"A{i:04d}", "status": "ACTIVE", "tradable": True} for i in range(3_060)]

    shared_frame = _frame(rows=60, base=25.0, step=0.02, volume=3_000_000)

    def _batch_loader(symbols, start, end):
        return {symbol: shared_frame for symbol in symbols}

    payload = market_scanner.scan_universe(
        symbols,
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        data_loader_batch=_batch_loader,
        max_workers=1,
        max_retries=0,
        lightweight_batch_size=200,
        coarse_candidate_limit=500,
        deep_score_limit=300,
    )

    assert payload["summary"]["stage_a_total"] == 3_060
    assert payload["summary"]["stage_c_survivors"] <= 500
    assert payload["summary"]["deep_scored_count"] <= 300


def test_deep_scoring_is_capped_and_deterministic(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 80.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 2.0}}},
    })

    letters = [chr(ord("A") + i) for i in range(20)]
    universe = [{"symbol": f"S{letter}", "status": "ACTIVE", "tradable": True} for letter in letters]
    payload = market_scanner.scan_universe(
        universe,
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        coarse_candidate_limit=20,
        deep_score_limit=5,
        lightweight_batch_size=10,
        max_workers=1,
        max_retries=0,
    )

    summary = payload["summary"]
    assert summary["stage_c_survivors"] == 20
    assert summary["deep_scored_count"] == 5
    rejected_reasons = [reason for row in payload["scan_results"] for reason in (row.get("rejection_reasons") or [])]
    assert "coarse ranking below deep score limit" in rejected_reasons


def test_lightweight_batch_size_is_respected(monkeypatch):
    batch_sizes = []

    def _batch_loader(symbols, start, end):
        batch_sizes.append(len(symbols))
        return {symbol: _frame() for symbol in symbols}

    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 80.0,
        "confidence": 70.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 60.0, "volatility": 60.0, "trend": 70.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 2.0}}},
    })

    payload = market_scanner.scan_universe(
        [{"symbol": f"B{i:02d}", "status": "ACTIVE", "tradable": True} for i in range(11)],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        data_loader_batch=_batch_loader,
        lightweight_batch_size=4,
        deep_score_limit=3,
        max_workers=1,
        max_retries=0,
    )

    assert batch_sizes[:3] == [4, 4, 3]
    assert payload["summary"]["lightweight_batch_size"] == 4


def test_expensive_lightweight_window_is_capped_by_coarse_limit(monkeypatch):
    batch_sizes = []

    def _batch_loader(symbols, start, end):
        batch_sizes.append(len(symbols))
        return {symbol: _frame() for symbol in symbols}

    payload = market_scanner.scan_universe(
        [{"symbol": f"C{i:04d}", "status": "ACTIVE", "tradable": True} for i in range(1_200)],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        data_loader_batch=_batch_loader,
        lightweight_batch_size=200,
        coarse_candidate_limit=500,
        deep_score_limit=0,
        max_retries=0,
    )

    assert sum(batch_sizes) <= 500
    assert payload["summary"]["stage_a_budget_window_selected"] == 500
    assert payload["summary"]["symbols_skipped_due_budget"] == 700
    assert payload["summary"]["partial_scan"] is True
    assert payload["summary"]["batch_count"]["attempted"] == len(batch_sizes)


def test_scan_always_emits_final_completion_event(monkeypatch):
    events = []
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    market_scanner.scan_universe(
        [{"symbol": "AAA", "status": "ACTIVE", "tradable": True}],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        progress_callback=lambda payload: events.append(dict(payload)),
        max_workers=1,
        max_retries=0,
    )

    names = [event.get("event") for event in events]
    assert "full_universe_scan_complete" in names


def test_infrastructure_failure_cannot_report_success(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    def _broken_batch(symbols, start, end):
        raise RuntimeError("upstream down")

    payload = market_scanner.scan_universe(
        [{"symbol": "AAA", "status": "ACTIVE", "tradable": True}],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        data_loader_batch=_broken_batch,
        max_retries=0,
        max_workers=1,
    )

    assert payload["summary"]["status"] in {"failed", "timed_out"}


def test_partial_success_thresholds_are_reported(monkeypatch):
    monkeypatch.setattr(market_scanner, "generate_strategy_result", lambda **kwargs: {
        "overall_score": 82.0,
        "confidence": 72.0,
        "signal": "BUY",
        "regime": "normal_bull",
        "component_scores": {"risk_quality": 70.0, "volatility": 60.0, "trend": 75.0},
        "reasons": [],
        "warnings": [],
        "data_quality": {"history_sufficient": True},
        "factors": {"trend": {"raw_values": {"distance_from_ema200_pct": 4.0}}},
    })

    payload = market_scanner.scan_universe(
        [{"symbol": f"P{i:03d}", "status": "ACTIVE", "tradable": True} for i in range(15)],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        max_workers=1,
        max_retries=0,
        deep_score_limit=10,
    )

    thresholds = payload["summary"]["partial_success_thresholds"]
    assert thresholds["deep_scored_minimum"] >= 1
    assert "partial_success_acceptable" in thresholds
