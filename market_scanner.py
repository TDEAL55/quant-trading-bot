from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from config import (
    BENCHMARK_SYMBOL,
    SCANNER_ALLOWED_SIGNALS,
    SCANNER_BATCH_SIZE,
    SCANNER_BLOCKED_REGIMES,
    SCANNER_MAX_RETRIES,
    SCANNER_MAX_WORKERS,
    SCANNER_MIN_CONFIDENCE,
    SCANNER_MIN_RISK_QUALITY,
    SCANNER_MIN_SCORE,
    SCANNER_MIN_VOLATILITY_SCORE,
    SCANNER_RANK_WEIGHT_CONFIDENCE,
    SCANNER_RANK_WEIGHT_LIQUIDITY,
    SCANNER_RANK_WEIGHT_OVERALL,
    SCANNER_RANK_WEIGHT_RISK_QUALITY,
    SCANNER_RANK_WEIGHT_TREND,
    SCANNER_SYMBOL_TIMEOUT_SECONDS,
)
from market_data import download_price_data
from scanner_filters import validate_symbol_data
from stock_universe import normalize_symbol
from strategy import generate_strategy_result


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_window() -> tuple[str, str]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=1000)
    return start_date.isoformat(), end_date.isoformat()


def _liquidity_score(avg_dollar_volume: float) -> float:
    if avg_dollar_volume <= 0:
        return 0.0
    score = 20.0 * math.log10(max(avg_dollar_volume, 1.0)) - 100.0
    return max(0.0, min(100.0, score))


def _extension_penalty(result: dict[str, Any]) -> float:
    factors = result.get("factors") or {}
    trend_raw = ((factors.get("trend") or {}).get("raw_values") or {})
    distance = float(trend_raw.get("distance_from_ema200_pct") or 0.0)
    if abs(distance) <= 15.0:
        return 0.0
    return min(abs(distance) - 15.0, 15.0)


def _eligible_reasons(
    signal_result: dict[str, Any],
    filter_result: dict[str, Any],
    min_score: float,
    min_confidence: float,
    min_risk_quality: float,
    min_volatility_score: float,
    allowed_signals: list[str],
    blocked_regimes: list[str],
) -> list[str]:
    reasons: list[str] = []
    if not filter_result.get("passed"):
        reasons.extend(filter_result.get("reasons", []))
    signal = str(signal_result.get("signal", "HOLD")).upper()
    if signal not in {value.upper() for value in allowed_signals}:
        reasons.append("signal is not in allowed scanner signals")
    score = float(signal_result.get("overall_score") or 0.0)
    if score < min_score:
        reasons.append(f"overall score below minimum ({score:.2f} < {min_score:.2f})")
    confidence = float(signal_result.get("confidence") or 0.0)
    if confidence < min_confidence:
        reasons.append(f"confidence below minimum ({confidence:.2f} < {min_confidence:.2f})")
    regime = str(signal_result.get("regime") or "unknown").lower()
    if regime in {value.lower() for value in blocked_regimes}:
        reasons.append(f"blocked market regime: {regime}")
    components = signal_result.get("component_scores") or {}
    risk_quality = float(components.get("risk_quality") or 0.0)
    if risk_quality < min_risk_quality:
        reasons.append(f"risk-quality score below minimum ({risk_quality:.2f} < {min_risk_quality:.2f})")
    volatility_score = float(components.get("volatility") or 0.0)
    if volatility_score < min_volatility_score:
        reasons.append(f"volatility factor below minimum ({volatility_score:.2f} < {min_volatility_score:.2f})")
    if not bool((signal_result.get("data_quality") or {}).get("history_sufficient", True)):
        reasons.append("factor-engine history quality check failed")
    return reasons


def scan_symbol(
    symbol_record: dict[str, Any],
    benchmark_history: pd.DataFrame,
    data_loader: Callable[[str, str, str], pd.DataFrame] = download_price_data,
    min_score: float = SCANNER_MIN_SCORE,
    min_confidence: float = SCANNER_MIN_CONFIDENCE,
    min_risk_quality: float = SCANNER_MIN_RISK_QUALITY,
    min_volatility_score: float = SCANNER_MIN_VOLATILITY_SCORE,
    allowed_signals: list[str] | None = None,
    blocked_regimes: list[str] | None = None,
) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol_record.get("symbol", ""))
    company_name = symbol_record.get("company_name", normalized_symbol)
    sector = symbol_record.get("sector", "Unknown")
    industry = symbol_record.get("industry", "Unknown")
    start_date, end_date = _history_window()

    scan_timestamp = _utc_iso()
    try:
        history = data_loader(normalized_symbol, start_date, end_date)
        filter_result = validate_symbol_data(normalized_symbol, history)
        metrics = filter_result.get("metrics", {})

        result = {
            "symbol": normalized_symbol,
            "company_name": company_name,
            "sector": sector,
            "industry": industry,
            "scan_timestamp": scan_timestamp,
            "latest_price": float(metrics.get("latest_price", 0.0)),
            "average_dollar_volume": float(metrics.get("average_dollar_volume_20", 0.0)),
            "overall_score": 0.0,
            "confidence": 0.0,
            "signal": "HOLD",
            "regime": "unknown",
            "component_scores": {},
            "reasons": [],
            "warnings": list(filter_result.get("warnings", [])),
            "data_quality": {
                "filter": filter_result,
                "factor": {},
            },
            "eligible": False,
            "rejection_reasons": [],
            "rank": None,
            "ranking_score": None,
            "status": "rejected",
        }

        if not filter_result.get("passed"):
            result["rejection_reasons"] = list(filter_result.get("reasons", []))
            return result

        strategy_result = generate_strategy_result(
            prices=history,
            strategy_mode="MULTI_FACTOR",
            symbol=normalized_symbol,
            benchmark_prices=benchmark_history,
        )
        result.update(
            {
                "overall_score": float(strategy_result.get("overall_score") or 0.0),
                "confidence": float(strategy_result.get("confidence") or 0.0),
                "signal": str(strategy_result.get("signal") or "HOLD"),
                "regime": str(strategy_result.get("regime") or "unknown"),
                "component_scores": dict(strategy_result.get("component_scores") or {}),
                "reasons": list(strategy_result.get("reasons") or []),
                "warnings": list(dict.fromkeys(result["warnings"] + list(strategy_result.get("warnings") or []))),
            }
        )
        result["data_quality"]["factor"] = dict(strategy_result.get("data_quality") or {})

        rejection_reasons = _eligible_reasons(
            strategy_result,
            filter_result,
            min_score=min_score,
            min_confidence=min_confidence,
            min_risk_quality=min_risk_quality,
            min_volatility_score=min_volatility_score,
            allowed_signals=allowed_signals or list(SCANNER_ALLOWED_SIGNALS),
            blocked_regimes=blocked_regimes or list(SCANNER_BLOCKED_REGIMES),
        )
        result["eligible"] = not rejection_reasons
        result["rejection_reasons"] = rejection_reasons
        result["status"] = "scored" if result["eligible"] else "rejected"
        return result
    except Exception as exc:
        return {
            "symbol": normalized_symbol,
            "company_name": company_name,
            "sector": sector,
            "industry": industry,
            "scan_timestamp": scan_timestamp,
            "latest_price": 0.0,
            "average_dollar_volume": 0.0,
            "overall_score": 0.0,
            "confidence": 0.0,
            "signal": "HOLD",
            "regime": "unknown",
            "component_scores": {},
            "reasons": [],
            "warnings": [],
            "data_quality": {},
            "eligible": False,
            "rejection_reasons": [f"scan error: {type(exc).__name__}: {exc}"],
            "rank": None,
            "ranking_score": None,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _scan_with_retry(
    symbol_record: dict[str, Any],
    benchmark_history: pd.DataFrame,
    data_loader: Callable[[str, str, str], pd.DataFrame],
    max_retries: int,
    retry_jitter_seconds: float,
) -> tuple[dict[str, Any], int]:
    attempts = 0
    last_result: dict[str, Any] | None = None
    while attempts <= max_retries:
        attempts += 1
        result = scan_symbol(symbol_record, benchmark_history, data_loader=data_loader)
        last_result = result
        if result.get("status") != "error":
            return result, attempts - 1
        if attempts <= max_retries:
            backoff = (2 ** (attempts - 1)) * 0.5
            time.sleep(backoff + random.uniform(0.0, retry_jitter_seconds))
    return last_result or {}, max_retries


def rank_scan_results(
    scan_results: list[dict[str, Any]],
    weight_overall: float = SCANNER_RANK_WEIGHT_OVERALL,
    weight_confidence: float = SCANNER_RANK_WEIGHT_CONFIDENCE,
    weight_risk_quality: float = SCANNER_RANK_WEIGHT_RISK_QUALITY,
    weight_trend: float = SCANNER_RANK_WEIGHT_TREND,
    weight_liquidity: float = SCANNER_RANK_WEIGHT_LIQUIDITY,
) -> list[dict[str, Any]]:
    eligible = [dict(item) for item in scan_results if item.get("eligible")]
    sector_counts: dict[str, int] = {}
    for item in sorted(eligible, key=lambda value: value.get("symbol", "")):
        sector = str(item.get("sector") or "Unknown")
        sector_counts.setdefault(sector, 0)

    scored: list[dict[str, Any]] = []
    for item in eligible:
        components = item.get("component_scores") or {}
        risk_quality = float(components.get("risk_quality") or 0.0)
        trend = float(components.get("trend") or 0.0)
        liquidity = _liquidity_score(float(item.get("average_dollar_volume") or 0.0))
        extension_penalty = _extension_penalty(item)
        sector = str(item.get("sector") or "Unknown")
        diversification_penalty = max(sector_counts.get(sector, 0) - 2, 0) * 0.5
        ranking_score = (
            float(item.get("overall_score") or 0.0) * weight_overall
            + float(item.get("confidence") or 0.0) * weight_confidence
            + risk_quality * weight_risk_quality
            + trend * weight_trend
            + liquidity * weight_liquidity
            - extension_penalty
            - diversification_penalty
        )
        item["ranking_score"] = round(ranking_score, 4)
        item["liquidity_score"] = round(liquidity, 4)
        item["extension_penalty"] = round(extension_penalty, 4)
        item["diversification_penalty"] = round(diversification_penalty, 4)
        scored.append(item)

    scored.sort(
        key=lambda item: (
            -float(item.get("ranking_score") or 0.0),
            -float(item.get("confidence") or 0.0),
            -float(item.get("average_dollar_volume") or 0.0),
            str(item.get("symbol") or ""),
        )
    )
    for index, item in enumerate(scored, start=1):
        item["rank"] = index
    return scored


def summarize_scan(scan_results: list[dict[str, Any]], started_at: float, retries: int = 0, cache_hits: int = 0) -> dict[str, Any]:
    total = len(scan_results)
    success_count = len([item for item in scan_results if item.get("status") == "scored"])
    rejection_count = len([item for item in scan_results if item.get("status") == "rejected"])
    error_count = len([item for item in scan_results if item.get("status") == "error"])
    eligible_count = len([item for item in scan_results if item.get("eligible")])
    duration = max(time.perf_counter() - started_at, 0.0)
    return {
        "symbol_count": total,
        "success_count": success_count,
        "rejection_count": rejection_count,
        "error_count": error_count,
        "eligible_count": eligible_count,
        "duration_seconds": round(duration, 4),
        "avg_symbol_seconds": round(duration / total, 4) if total else 0.0,
        "retry_count": retries,
        "cache_hits": cache_hits,
    }


def scan_universe(
    symbol_records: list[dict[str, Any]],
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    data_loader: Callable[[str, str, str], pd.DataFrame] = download_price_data,
    max_workers: int = SCANNER_MAX_WORKERS,
    symbol_timeout_seconds: int = SCANNER_SYMBOL_TIMEOUT_SECONDS,
    max_retries: int = SCANNER_MAX_RETRIES,
    batch_size: int = SCANNER_BATCH_SIZE,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    deduped: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for record in symbol_records:
        symbol = normalize_symbol(record.get("symbol", ""))
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        deduped.append({**record, "symbol": symbol})

    start_date, end_date = _history_window()
    benchmark_history = data_loader(benchmark_symbol, start_date, end_date)

    scan_results: list[dict[str, Any]] = []
    retries = 0
    completed = 0
    cache_hits = max(len(symbol_records) - len(deduped), 0)

    for batch_start in range(0, len(deduped), max(batch_size, 1)):
        batch = deduped[batch_start : batch_start + max(batch_size, 1)]
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            future_items = [
                (
                    executor.submit(
                    _scan_with_retry,
                    symbol_record=item,
                    benchmark_history=benchmark_history,
                    data_loader=data_loader,
                    max_retries=max_retries,
                    retry_jitter_seconds=0.2,
                    ),
                    item,
                )
                for item in batch
            ]
            for future, item in future_items:
                try:
                    result, retry_count = future.result(timeout=symbol_timeout_seconds)
                    retries += retry_count
                except Exception as exc:
                    result = {
                        "symbol": item.get("symbol"),
                        "company_name": item.get("company_name", item.get("symbol")),
                        "sector": item.get("sector", "Unknown"),
                        "industry": item.get("industry", "Unknown"),
                        "scan_timestamp": _utc_iso(),
                        "latest_price": 0.0,
                        "average_dollar_volume": 0.0,
                        "overall_score": 0.0,
                        "confidence": 0.0,
                        "signal": "HOLD",
                        "regime": "unknown",
                        "component_scores": {},
                        "reasons": [],
                        "warnings": [],
                        "data_quality": {},
                        "eligible": False,
                        "rejection_reasons": [f"symbol timeout/error: {type(exc).__name__}: {exc}"],
                        "rank": None,
                        "ranking_score": None,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                scan_results.append(result)
                completed += 1
                if progress_callback:
                    progress_callback({"completed": completed, "total": len(deduped), "symbol": result.get("symbol")})
        if batch_start + max(batch_size, 1) < len(deduped):
            time.sleep(random.uniform(0.05, 0.25))

    ranked = rank_scan_results(scan_results)
    summary = summarize_scan(scan_results, started_at=started, retries=retries, cache_hits=cache_hits)
    summary["benchmark_symbol"] = benchmark_symbol
    summary["benchmark_rows"] = int(len(benchmark_history))
    summary["benchmark_reused"] = True
    return {
        "scan_results": scan_results,
        "ranked_candidates": ranked,
        "summary": summary,
    }
