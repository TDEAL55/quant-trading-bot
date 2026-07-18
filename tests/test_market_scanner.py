import time

import pandas as pd

import market_scanner


def _frame(rows=260, base=100.0, volume=1_500_000):
    end = pd.Timestamp.now(tz="UTC").normalize()
    index = pd.bdate_range(end=end, periods=rows)
    close = pd.Series([base + (i * 0.2) for i in range(rows)], index=index)
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
    assert len(payload["scan_results"]) == 2
    assert payload["summary"]["error_count"] == 1
    assert payload["summary"]["cache_hits"] == 1


def test_rank_scan_results_deterministic_tie_breakers():
    rows = [
        {"symbol": "BBB", "eligible": True, "overall_score": 80, "confidence": 70, "average_dollar_volume": 40_000_000, "component_scores": {"risk_quality": 60, "trend": 70}},
        {"symbol": "AAA", "eligible": True, "overall_score": 80, "confidence": 70, "average_dollar_volume": 40_000_000, "component_scores": {"risk_quality": 60, "trend": 70}},
    ]
    ranked = market_scanner.rank_scan_results(rows)
    assert ranked[0]["symbol"] == "AAA"


def test_scan_universe_timeout_handling(monkeypatch):
    monkeypatch.setattr(market_scanner, "_scan_with_retry", lambda **kwargs: (time.sleep(0.05), 0))
    payload = market_scanner.scan_universe(
        [{"symbol": "AAA"}],
        benchmark_symbol="SPY",
        data_loader=lambda *args, **kwargs: _frame(),
        symbol_timeout_seconds=0,
        max_workers=1,
    )
    assert payload["summary"]["error_count"] == 1
