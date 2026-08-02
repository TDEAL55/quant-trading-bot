from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

import pandas as pd

from config import (
    BENCHMARK_SYMBOL,
    SCANNER_ALLOWED_SIGNALS,
    SCANNER_BATCH_SIZE,
    SCANNER_BLOCKED_REGIMES,
    SCANNER_MAX_MISSING_PERCENT,
    SCANNER_MAX_RETRIES,
    SCANNER_MAX_STALE_BUSINESS_DAYS,
    SCANNER_MAX_WORKERS,
    SCANNER_MIN_AVG_DOLLAR_VOLUME,
    SCANNER_MIN_CONFIDENCE,
    SCANNER_MIN_HISTORY_DAYS,
    SCANNER_MIN_PRICE,
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
from quantum_score_engine import calculate_quantum_score, compute_strategy_specific_scores, rank_scored_candidates
from scanner_filters import validate_symbol_data
from stock_universe import normalize_symbol
from strategy import generate_strategy_result


_SCAN_CACHE_LOCK = Lock()
_SCAN_HISTORY_CACHE: dict[tuple[str, str, str, str], tuple[float, pd.DataFrame]] = {}
_DEFAULT_CACHE_TTL_SECONDS = 240.0


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_window(lookback_days: int = 1000) -> tuple[str, str]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=max(int(lookback_days), 30))
    return start_date.isoformat(), end_date.isoformat()


def _build_error_result(symbol_record: dict[str, Any], message: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol_record.get("symbol", ""))
    return {
        "symbol": symbol,
        "company_name": symbol_record.get("company_name", symbol),
        "sector": symbol_record.get("sector", "Unknown"),
        "industry": symbol_record.get("industry", "Unknown"),
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
        "rejection_reasons": [message],
        "rank": None,
        "ranking_score": None,
        "status": "error",
        "error": message,
    }


def _build_rejected_result(symbol_record: dict[str, Any], reasons: list[str], *, status: str = "rejected") -> dict[str, Any]:
    symbol = normalize_symbol(symbol_record.get("symbol", ""))
    return {
        "symbol": symbol,
        "company_name": symbol_record.get("company_name", symbol),
        "sector": symbol_record.get("sector", "Unknown"),
        "industry": symbol_record.get("industry", "Unknown"),
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
        "rejection_reasons": list(reasons),
        "rank": None,
        "ranking_score": None,
        "status": status,
    }


def _is_rate_limit_error(message: str) -> bool:
    text = str(message or "").lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _canonical_reason(reason: str) -> str:
    text = str(reason or "").lower()
    mapping = [
        ("inactive", "inactive"),
        ("tradable", "tradable_false_or_invalid"),
        ("halt", "halted_or_unavailable"),
        ("insufficient history", "missing_enough_price_history"),
        ("stale", "stale_market_data"),
        ("price below minimum", "price_below_configured_minimum"),
        ("average dollar volume", "average_dollar_volume_below_configured_minimum"),
        ("invalid ohlc", "invalid_bid_ask_or_tradability_data"),
        ("impossible ohlc", "invalid_bid_ask_or_tradability_data"),
        ("order-size", "unsupported_order_size_requirements"),
        ("duplicate symbol", "duplicate_symbol"),
        ("existing position", "existing_position_or_open_entry_order"),
        ("open entry order", "existing_position_or_open_entry_order"),
        ("no market data", "missing_enough_price_history"),
    ]
    for token, label in mapping:
        if token in text:
            return label
    return str(reason or "unknown_filter_reason")


def _count_reasons(counter: dict[str, int], reasons: list[str]) -> None:
    for reason in reasons:
        label = _canonical_reason(reason)
        counter[label] = int(counter.get(label, 0)) + 1


def _metadata_filter(symbol_record: dict[str, Any], seen_symbols: set[str]) -> list[str]:
    reasons: list[str] = []
    symbol = normalize_symbol(symbol_record.get("symbol", ""))
    if not symbol:
        reasons.append("invalid tradability data")
        return reasons
    if symbol in seen_symbols:
        reasons.append("duplicate symbol")

    status = str(symbol_record.get("status") or "ACTIVE").upper()
    if status != "ACTIVE":
        reasons.append("inactive")

    tradable_raw = symbol_record.get("tradable", True)
    if tradable_raw is None:
        reasons.append("invalid tradability data")
    elif not bool(tradable_raw):
        reasons.append("tradable is false")

    if bool(symbol_record.get("halted", False)):
        reasons.append("halted or unavailable")
    if str(symbol_record.get("availability") or "").lower() in {"halted", "suspended", "unavailable"}:
        reasons.append("halted or unavailable")

    if bool(symbol_record.get("has_existing_position", False)) and not bool(symbol_record.get("scaling_allowed", False)):
        reasons.append("existing position when scaling is not allowed")
    if bool(symbol_record.get("has_open_entry_order", False)) and not bool(symbol_record.get("scaling_allowed", False)):
        reasons.append("open entry order when scaling is not allowed")

    if bool(symbol_record.get("order_size_unsupported", False)):
        reasons.append("unsupported order-size requirements")
    return reasons


def _cached_download(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    bucket: str,
    data_loader: Callable[[str, str, str], pd.DataFrame],
    cache_ttl_seconds: float,
    cache_stats: dict[str, int],
) -> pd.DataFrame:
    key = (bucket, str(symbol).upper(), str(start_date), str(end_date))
    now = time.time()
    with _SCAN_CACHE_LOCK:
        cached = _SCAN_HISTORY_CACHE.get(key)
        if cached and (now - float(cached[0])) <= float(cache_ttl_seconds):
            cache_stats["cache_hits"] = int(cache_stats.get("cache_hits", 0)) + 1
            return cached[1]

    frame = data_loader(symbol, start_date, end_date)
    with _SCAN_CACHE_LOCK:
        _SCAN_HISTORY_CACHE[key] = (now, frame)
    return frame


def _download_with_retry(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    bucket: str,
    data_loader: Callable[[str, str, str], pd.DataFrame],
    cache_ttl_seconds: float,
    cache_stats: dict[str, int],
    max_retries: int,
) -> tuple[pd.DataFrame, int, int]:
    attempts = 0
    retries_used = 0
    rate_limit_retries = 0
    while True:
        try:
            frame = _cached_download(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                bucket=bucket,
                data_loader=data_loader,
                cache_ttl_seconds=cache_ttl_seconds,
                cache_stats=cache_stats,
            )
            return frame, retries_used, rate_limit_retries
        except Exception as exc:
            if attempts >= int(max_retries):
                raise
            attempts += 1
            retries_used += 1
            message = f"{type(exc).__name__}: {exc}"
            is_rate_limit = _is_rate_limit_error(message)
            if is_rate_limit:
                rate_limit_retries += 1
            sleep_s = (2 ** (attempts - 1)) * (1.2 if is_rate_limit else 0.5)
            time.sleep(sleep_s + random.uniform(0.0, 0.2))


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
    risk_quality = float(components.get("risk_quality", components.get("risk_reward_quality", 0.0)) or 0.0)
    if risk_quality < min_risk_quality:
        reasons.append(f"risk-quality score below minimum ({risk_quality:.2f} < {min_risk_quality:.2f})")
    volatility_score = float(components.get("volatility_quality", components.get("volatility", 0.0)) or 0.0)
    if volatility_score < min_volatility_score:
        reasons.append(f"volatility factor below minimum ({volatility_score:.2f} < {min_volatility_score:.2f})")
    if not bool((signal_result.get("data_quality") or {}).get("history_sufficient", True)):
        reasons.append("factor-engine history quality check failed")
    for reason in list((signal_result.get("quantum_score") or {}).get("rejection_reasons") or []):
        if reason in {
            "stale_data",
            "minimum_price_check_failed",
            "average_dollar_volume_below_minimum",
            "invalid_reward_risk_structure",
            "reward_risk_below_minimum",
        }:
            reasons.append(f"quantum guardrail: {reason}")
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
    history: pd.DataFrame | None = None,
    filter_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol_record.get("symbol", ""))
    company_name = symbol_record.get("company_name", normalized_symbol)
    sector = symbol_record.get("sector", "Unknown")
    industry = symbol_record.get("industry", "Unknown")
    start_date, end_date = _history_window()

    scan_timestamp = _utc_iso()
    try:
        price_history = history if history is not None else data_loader(normalized_symbol, start_date, end_date)
        selected_filter = filter_result or validate_symbol_data(normalized_symbol, price_history)
        metrics = selected_filter.get("metrics", {})

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
            "warnings": list(selected_filter.get("warnings", [])),
            "data_quality": {
                "filter": selected_filter,
                "factor": {},
            },
            "eligible": False,
            "rejection_reasons": [],
            "rank": None,
            "ranking_score": None,
            "status": "rejected",
        }

        if not selected_filter.get("passed"):
            result["rejection_reasons"] = list(selected_filter.get("reasons", []))
            return result

        strategy_result = generate_strategy_result(
            prices=price_history,
            strategy_mode="MULTI_FACTOR",
            symbol=normalized_symbol,
            benchmark_prices=benchmark_history,
        )
        quantum_score = calculate_quantum_score(
            symbol=normalized_symbol,
            prices=price_history,
            benchmark_prices=benchmark_history,
        )
        strategy_specific_scores = compute_strategy_specific_scores(quantum_score)
        quantum_components = dict(quantum_score.get("normalized_component_scores") or {})
        strategy_components = dict(strategy_result.get("component_scores") or {})
        compatibility_components = {
            "trend": float(strategy_components.get("trend", quantum_components.get("trend_strength", 0.0)) or 0.0),
            "momentum": float(strategy_components.get("momentum", quantum_components.get("momentum_quality", 0.0)) or 0.0),
            "volume": float(strategy_components.get("volume", quantum_components.get("volume_confirmation", 0.0)) or 0.0),
            "volatility": float(strategy_components.get("volatility", quantum_components.get("volatility_quality", 0.0)) or 0.0),
            "market_regime": float(strategy_components.get("market_regime", quantum_components.get("market_regime_alignment", 0.0)) or 0.0),
            "risk_quality": float(strategy_components.get("risk_quality", quantum_components.get("risk_reward_quality", 0.0)) or 0.0),
            **quantum_components,
        }
        result.update(
            {
                "overall_score": float(quantum_score.get("final_score") or 0.0),
                "confidence": float(strategy_result.get("confidence") or 0.0),
                "signal": str(strategy_result.get("signal") or "HOLD"),
                "regime": str(quantum_score.get("market_regime") or strategy_result.get("regime") or "unknown"),
                "component_scores": compatibility_components,
                "reasons": list(strategy_result.get("reasons") or []),
                "warnings": list(dict.fromkeys(result["warnings"] + list(strategy_result.get("warnings") or []) + list(quantum_score.get("warnings") or []))),
                "quantum_score": quantum_score,
                "strategy_specific_scores": strategy_specific_scores,
                "score_version": str(quantum_score.get("score_version") or "quantum_v1"),
            }
        )
        result["data_quality"]["factor"] = dict(strategy_result.get("data_quality") or {})
        result["data_quality"]["quantum"] = {
            "status": quantum_score.get("data_quality_status"),
            "missing_data_penalties": list(quantum_score.get("missing_data_penalties") or []),
            "stale_business_days": quantum_score.get("stale_business_days"),
        }

        rejection_reasons = _eligible_reasons(
            {**strategy_result, "component_scores": compatibility_components, "quantum_score": quantum_score},
            selected_filter,
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
    history: pd.DataFrame | None = None,
    filter_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    attempts = 0
    last_result: dict[str, Any] | None = None
    while attempts <= max_retries:
        attempts += 1
        result = scan_symbol(
            symbol_record,
            benchmark_history,
            data_loader=data_loader,
            history=history,
            filter_result=filter_result,
        )
        last_result = result
        if result.get("status") != "error":
            return result, attempts - 1
        if attempts <= max_retries:
            err_text = str(result.get("error") or "")
            backoff = (2 ** (attempts - 1)) * (1.2 if _is_rate_limit_error(err_text) else 0.5)
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
        risk_quality = float(components.get("risk_reward_quality", components.get("risk_quality", 0.0)) or 0.0)
        trend = float(components.get("trend_strength", components.get("trend", 0.0)) or 0.0)
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

    scored = rank_scored_candidates(scored)

    scored.sort(
        key=lambda item: (
            int(item.get("rank") or 9_999_999),
            -float(item.get("ranking_score") or 0.0),
            str(item.get("symbol") or ""),
        )
    )
    for index, item in enumerate(scored, start=1):
        item["rank"] = index
    return scored


def summarize_scan(
    scan_results: list[dict[str, Any]],
    started_at: float,
    *,
    retries: int = 0,
    cache_hits: int = 0,
    universe_total_count: int = 0,
    metadata_pass_count: int = 0,
    lightweight_pass_count: int = 0,
    filtered_count_by_reason: dict[str, int] | None = None,
) -> dict[str, Any]:
    total = len(scan_results)
    success_count = len([item for item in scan_results if item.get("status") == "scored"])
    rejection_count = len([item for item in scan_results if item.get("status") == "rejected"])
    error_count = len([item for item in scan_results if item.get("status") == "error"])
    eligible_count = len([item for item in scan_results if item.get("eligible")])
    duration = max(time.perf_counter() - started_at, 0.0)
    return {
        "universe_total_count": int(universe_total_count),
        "symbol_count": total,
        "metadata_pass_count": int(metadata_pass_count),
        "lightweight_pass_count": int(lightweight_pass_count),
        "success_count": success_count,
        "rejection_count": rejection_count,
        "error_count": error_count,
        "failed_symbol_count": error_count,
        "eligible_count": eligible_count,
        "duration_seconds": round(duration, 4),
        "avg_symbol_seconds": round(duration / total, 4) if total else 0.0,
        "retry_count": retries,
        "cache_hits": cache_hits,
        "filtered_count_by_reason": dict(filtered_count_by_reason or {}),
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
    metadata_pass_records: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    filtered_count_by_reason: dict[str, int] = {}
    scan_results: list[dict[str, Any]] = []

    universe_total_count = len(symbol_records)
    for record in symbol_records:
        symbol = normalize_symbol(record.get("symbol", ""))
        normalized_record = {**record, "symbol": symbol}
        reasons = _metadata_filter(normalized_record, seen_symbols)
        if reasons:
            _count_reasons(filtered_count_by_reason, reasons)
            scan_results.append(_build_rejected_result(normalized_record, reasons))
            continue
        seen_symbols.add(symbol)
        metadata_pass_records.append(normalized_record)

    start_date, end_date = _history_window(lookback_days=max(1000, int(SCANNER_MIN_HISTORY_DAYS) * 3))
    benchmark_history = data_loader(benchmark_symbol, start_date, end_date)

    retries = 0
    rate_limit_retries = 0
    completed = 0
    cache_stats = {"cache_hits": 0}
    lightweight_survivors: list[dict[str, Any]] = []

    light_start, light_end = _history_window(lookback_days=max(90, min(180, int(SCANNER_MIN_HISTORY_DAYS))))
    lightweight_history_min = max(40, min(120, int(SCANNER_MIN_HISTORY_DAYS)))

    for batch_start in range(0, len(metadata_pass_records), max(batch_size, 1)):
        batch = metadata_pass_records[batch_start : batch_start + max(batch_size, 1)]
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            future_items = [
                (
                    executor.submit(
                        _download_with_retry,
                        symbol=normalize_symbol(item.get("symbol", "")),
                        start_date=light_start,
                        end_date=light_end,
                        bucket="lightweight",
                        data_loader=data_loader,
                        cache_ttl_seconds=_DEFAULT_CACHE_TTL_SECONDS,
                        cache_stats=cache_stats,
                        max_retries=max_retries,
                    ),
                    item,
                )
                for item in batch
            ]
            for future, item in future_items:
                try:
                    light_history, retry_count, rl_retries = future.result(timeout=symbol_timeout_seconds)
                    retries += int(retry_count)
                    rate_limit_retries += int(rl_retries)
                    lightweight_filter = validate_symbol_data(
                        normalize_symbol(item.get("symbol", "")),
                        light_history,
                        min_price=SCANNER_MIN_PRICE,
                        min_avg_dollar_volume=SCANNER_MIN_AVG_DOLLAR_VOLUME,
                        min_history_days=lightweight_history_min,
                        max_missing_percent=SCANNER_MAX_MISSING_PERCENT,
                        max_stale_business_days=SCANNER_MAX_STALE_BUSINESS_DAYS,
                    )
                    if not lightweight_filter.get("passed"):
                        reasons = list(lightweight_filter.get("reasons") or [])
                        _count_reasons(filtered_count_by_reason, reasons)
                        result = _build_rejected_result(item, reasons)
                        scan_results.append(result)
                    else:
                        lightweight_survivors.append(item)
                except Exception as exc:
                    result = _build_error_result(item, f"symbol timeout/error: {type(exc).__name__}: {exc}")
                    scan_results.append(result)
                completed += 1
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "lightweight",
                            "completed": completed,
                            "total": len(metadata_pass_records),
                            "symbol": normalize_symbol(item.get("symbol", "")),
                        }
                    )
        if batch_start + max(batch_size, 1) < len(metadata_pass_records):
            time.sleep(random.uniform(0.05, 0.25))

    completed = 0
    for batch_start in range(0, len(lightweight_survivors), max(batch_size, 1)):
        batch = lightweight_survivors[batch_start : batch_start + max(batch_size, 1)]
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            future_items = []
            for item in batch:
                symbol = normalize_symbol(item.get("symbol", ""))
                future_items.append(
                    (
                        executor.submit(
                            _download_with_retry,
                            symbol=symbol,
                            start_date=start_date,
                            end_date=end_date,
                            bucket="full",
                            data_loader=data_loader,
                            cache_ttl_seconds=_DEFAULT_CACHE_TTL_SECONDS,
                            cache_stats=cache_stats,
                            max_retries=max_retries,
                        ),
                        item,
                    )
                )

            for future, item in future_items:
                try:
                    full_history, retry_count, rl_retries = future.result(timeout=symbol_timeout_seconds)
                    retries += int(retry_count)
                    rate_limit_retries += int(rl_retries)
                    result, retry_count = _scan_with_retry(
                        symbol_record=item,
                        benchmark_history=benchmark_history,
                        data_loader=data_loader,
                        max_retries=max_retries,
                        retry_jitter_seconds=0.2,
                        history=full_history,
                    )
                    retries += retry_count
                except Exception as exc:
                    result = _build_error_result(item, f"symbol timeout/error: {type(exc).__name__}: {exc}")
                if result.get("status") == "rejected":
                    _count_reasons(filtered_count_by_reason, list(result.get("rejection_reasons") or []))
                scan_results.append(result)
                completed += 1
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "full_scoring",
                            "completed": completed,
                            "total": len(lightweight_survivors),
                            "symbol": normalize_symbol(item.get("symbol", "")),
                        }
                    )

        if batch_start + max(batch_size, 1) < len(lightweight_survivors):
            time.sleep(random.uniform(0.05, 0.20))

    ranked = rank_scan_results(scan_results)
    summary = summarize_scan(
        scan_results,
        started_at=started,
        retries=retries,
        cache_hits=int(cache_stats.get("cache_hits", 0)),
        universe_total_count=universe_total_count,
        metadata_pass_count=len(metadata_pass_records),
        lightweight_pass_count=len(lightweight_survivors),
        filtered_count_by_reason=filtered_count_by_reason,
    )
    summary["benchmark_symbol"] = benchmark_symbol
    summary["benchmark_rows"] = int(len(benchmark_history))
    summary["benchmark_reused"] = True
    summary["rate_limit_retry_count"] = int(rate_limit_retries)
    summary["max_workers"] = int(max_workers)
    summary["batch_size"] = int(max(batch_size, 1))
    return {
        "scan_results": scan_results,
        "ranked_candidates": ranked,
        "summary": summary,
    }
