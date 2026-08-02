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
        time.sleep(0.05)
        return _frame()

    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}],
        benchmark_symbol="SPY",
        data_loader=_slow_loader,
        symbol_timeout_seconds=0,
        max_workers=1,
    )
    assert payload["summary"]["error_count"] == 1


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
