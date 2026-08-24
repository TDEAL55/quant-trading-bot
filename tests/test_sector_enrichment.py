from __future__ import annotations

import json

import sector_enrichment
from sector_enrichment import enrich_sector_records, fetch_yahoo_sector


def test_fetch_yahoo_sector_uses_exact_symbol_and_classifies_etf(monkeypatch):
    class _Search:
        def __init__(self, query, **kwargs):
            assert query == "SPY"
            assert kwargs["timeout"] == 4.0
            self.quotes = [
                {"symbol": "SPYD", "quoteType": "ETF"},
                {"symbol": "SPY", "quoteType": "ETF"},
            ]

    monkeypatch.setattr(sector_enrichment.yf, "Search", _Search)

    result = fetch_yahoo_sector("spy", 4.0)

    assert result["sector"] == "ETF"
    assert result["industry"] == "Exchange Traded Fund"


def test_enrichment_is_bounded_cached_and_applies_to_duplicate_records(tmp_path):
    calls: list[str] = []

    def _fetch(symbol, timeout_seconds):
        calls.append(symbol)
        assert timeout_seconds == 3.0
        return {
            "sector": "Financial Services" if symbol == "JPM" else "Technology",
            "industry": "Banks" if symbol == "JPM" else "Software",
            "source": "test",
            "fetched_at": "2026-08-23T00:00:00+00:00",
        }

    cache_path = tmp_path / "sector-cache.json"
    records = [
        {"symbol": "JPM", "sector": "Unknown"},
        {"symbol": "JPM", "sector": "Unknown", "group": "candidate"},
        {"symbol": "MSFT", "sector": "Unknown"},
        {"symbol": "AAPL", "sector": "Unknown"},
        {"symbol": "XOM", "sector": "Energy", "industry": "Oil & Gas"},
    ]

    enriched, metadata = enrich_sector_records(
        records,
        cache_path=cache_path,
        max_symbols=2,
        timeout_seconds=3,
        total_timeout_seconds=5,
        max_workers=2,
        cache_ttl_days=30,
        fetcher=_fetch,
        now_fn=lambda: 1787443200.0,
    )

    assert sorted(calls) == ["JPM", "MSFT"]
    assert enriched[0]["sector"] == "Financial Services"
    assert enriched[1]["sector"] == "Financial Services"
    assert enriched[2]["sector"] == "Technology"
    assert enriched[3]["sector"] == "Unknown"
    assert enriched[4]["sector"] == "Energy"
    assert metadata["symbols_considered"] == 2
    assert metadata["symbols_skipped_by_limit"] == 1
    assert metadata["network_resolved"] == 2
    assert json.loads(cache_path.read_text(encoding="utf-8"))["symbols"]["JPM"]["sector"] == "Financial Services"

    calls.clear()
    enriched_again, second_metadata = enrich_sector_records(
        records,
        cache_path=cache_path,
        max_symbols=2,
        fetcher=_fetch,
        now_fn=lambda: 1787443201.0,
    )
    assert calls == []
    assert enriched_again[0]["sector"] == "Financial Services"
    assert second_metadata["cache_hits"] == 2


def test_enrichment_failure_is_safe_and_does_not_cache_unknown(tmp_path):
    def _fetch(symbol, timeout_seconds):
        if symbol == "BAD":
            raise TimeoutError("provider timeout")
        return None

    cache_path = tmp_path / "sector-cache.json"
    enriched, metadata = enrich_sector_records(
        [{"symbol": "BAD", "sector": "Unknown"}, {"symbol": "NONE", "sector": "Unknown"}],
        cache_path=cache_path,
        fetcher=_fetch,
    )

    assert all(item["sector"] == "Unknown" for item in enriched)
    assert metadata["unresolved_count"] == 2
    assert metadata["failure_types"]["BAD"] == "TimeoutError"
    assert metadata["failure_types"]["NONE"] == "metadata_not_available"
    assert not cache_path.exists()
