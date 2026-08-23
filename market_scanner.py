from __future__ import annotations

import inspect
import math
import random
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

import pandas as pd

from config import (
    BENCHMARK_SYMBOL,
    SCANNER_ALLOWED_SIGNALS,
    SCANNER_BATCH_SIZE,
    SCANNER_BLOCKED_REGIMES,
    SCANNER_MAX_COARSE_CANDIDATES,
    SCANNER_DATA_REQUEST_TIMEOUT_SECONDS,
    SCANNER_LIGHTWEIGHT_BATCH_SIZE,
    SCANNER_MAX_DEEP_SCORE_SYMBOLS,
    SCANNER_PROGRESS_EVERY,
    SCANNER_MAX_SCAN_SECONDS,
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
from market_data import download_price_data, download_price_data_batch
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


def _memory_snapshot_mb() -> tuple[float, float]:
    try:
        current, peak = tracemalloc.get_traced_memory()
        return round(float(current) / (1024 * 1024), 4), round(float(peak) / (1024 * 1024), 4)
    except Exception:
        return 0.0, 0.0


def _invoke_loader_with_timeout(loader: Callable[..., Any], *args: Any, timeout_seconds: float) -> Any:
    try:
        params = inspect.signature(loader).parameters
    except Exception:
        params = {}

    if "timeout_seconds" in params:
        return loader(*args, timeout_seconds=float(timeout_seconds))
    return loader(*args)


def _rotating_budget_window(records: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if limit <= 0 or len(records) <= limit:
        return list(records), []
    # Rotate the scan window each cycle to preserve broad discovery over time.
    slot = int(time.time() // 300)
    offset = slot % len(records)
    rotated = records[offset:] + records[:offset]
    return rotated[:limit], rotated[limit:]


def _coarse_metrics(symbol: str, history: pd.DataFrame, benchmark_history: pd.DataFrame) -> dict[str, float] | None:
    close_series = pd.to_numeric(history.get("close", pd.Series(dtype=float)), errors="coerce").dropna()
    volume_series = pd.to_numeric(history.get("volume", pd.Series(dtype=float)), errors="coerce").dropna()
    benchmark_close = pd.to_numeric(benchmark_history.get("close", pd.Series(dtype=float)), errors="coerce").dropna()
    if len(close_series) < 21 or len(volume_series) < 21 or len(benchmark_close) < 21:
        return None

    latest_price = float(close_series.iloc[-1])
    ret5 = float((close_series.iloc[-1] / close_series.iloc[-6]) - 1.0)
    ret20 = float((close_series.iloc[-1] / close_series.iloc[-21]) - 1.0)
    benchmark_ret20 = float((benchmark_close.iloc[-1] / benchmark_close.iloc[-21]) - 1.0)
    rel_strength = ret20 - benchmark_ret20
    avg_volume_20 = float(volume_series.tail(20).mean())
    volume_ratio = float(volume_series.iloc[-1] / max(avg_volume_20, 1.0))
    volatility = float(close_series.pct_change().tail(20).std(ddof=0) * math.sqrt(252))
    recent_high = float(close_series.tail(20).max())
    distance_from_recent_high = float((latest_price / max(recent_high, 0.0001)) - 1.0)
    avg_dollar_volume_20 = float((close_series.tail(20) * volume_series.tail(20)).mean())

    # Coarse score is cheap and monotonic, then refined by full quantum scoring.
    coarse_score = (
        (ret5 * 100.0) * 0.20
        + (ret20 * 100.0) * 0.35
        + (rel_strength * 100.0) * 0.25
        + (min(max(volume_ratio, 0.0), 3.0) * 10.0) * 0.10
        + (max(0.0, 1.0 - min(abs(distance_from_recent_high), 0.30) / 0.30) * 100.0) * 0.10
        - (min(max(volatility, 0.0), 1.0) * 10.0)
    )
    return {
        "symbol": str(symbol),
        "coarse_score": float(round(coarse_score, 6)),
        "ret5": float(round(ret5, 6)),
        "ret20": float(round(ret20, 6)),
        "rel_strength_vs_spy": float(round(rel_strength, 6)),
        "volume_ratio": float(round(volume_ratio, 6)),
        "volatility": float(round(volatility, 6)),
        "distance_from_recent_high": float(round(distance_from_recent_high, 6)),
        "avg_dollar_volume_20": float(round(avg_dollar_volume_20, 4)),
    }


def _frame_to_close_rows(history: pd.DataFrame, *, max_points: int = 260) -> list[dict[str, Any]]:
    if history is None or history.empty:
        return []

    close_series = pd.to_numeric(history.get("close", pd.Series(dtype=float)), errors="coerce").dropna()
    if close_series.empty:
        return []

    trimmed = close_series.tail(max(int(max_points), 2))
    rows: list[dict[str, Any]] = []
    for idx, value in trimmed.items():
        try:
            date_key = pd.Timestamp(idx).date().isoformat()
        except Exception:
            date_key = str(idx)
        rows.append({"date": date_key, "close": float(value)})
    return rows


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
    data_loader_batch: Callable[[list[str], str, str], dict[str, pd.DataFrame]] = download_price_data_batch,
    max_workers: int = SCANNER_MAX_WORKERS,
    symbol_timeout_seconds: int = SCANNER_SYMBOL_TIMEOUT_SECONDS,
    request_timeout_seconds: int = SCANNER_DATA_REQUEST_TIMEOUT_SECONDS,
    max_retries: int = SCANNER_MAX_RETRIES,
    batch_size: int = SCANNER_BATCH_SIZE,
    lightweight_batch_size: int = SCANNER_LIGHTWEIGHT_BATCH_SIZE,
    deep_score_limit: int = SCANNER_MAX_DEEP_SCORE_SYMBOLS,
    coarse_candidate_limit: int = SCANNER_MAX_COARSE_CANDIDATES,
    progress_every: int = SCANNER_PROGRESS_EVERY,
    max_scan_seconds: int = SCANNER_MAX_SCAN_SECONDS,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if data_loader_batch is download_price_data_batch and data_loader is not download_price_data:
        def _batch_from_single(symbols: list[str], start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
            mapped: dict[str, pd.DataFrame] = {}
            errors: dict[str, Exception] = {}
            for symbol in symbols:
                try:
                    mapped[str(symbol).upper()] = data_loader(symbol, start_date, end_date)
                except Exception as exc:
                    errors[str(symbol).upper()] = exc
                    continue
            setattr(_batch_from_single, "_last_errors", errors)
            return mapped

        data_loader_batch = _batch_from_single

    started = time.perf_counter()
    deadline = started + float(max_scan_seconds if int(max_scan_seconds) > 0 else 365 * 24 * 3600)

    def _remaining_timeout_seconds(default_timeout: float) -> float:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return 0.0
        return max(min(float(default_timeout), remaining), 0.1)

    tracemalloc.start()

    stage_timers: dict[str, float] = {}
    stage_counts: dict[str, dict[str, int]] = {}
    filtered_count_by_reason: dict[str, int] = {}
    scan_results: list[dict[str, Any]] = []
    price_history_by_symbol: dict[str, list[dict[str, Any]]] = {}
    timed_out_symbols: list[str] = []
    slow_symbols: list[tuple[float, str, str]] = []
    retries = 0
    rate_limit_retries = 0
    cache_stats = {"cache_hits": 0}
    deadline_hit = False
    budget_deferred_count = 0
    budget_deferred_symbols: list[str] = []
    batch_telemetry = {"attempted": 0, "succeeded": 0, "failed": 0, "timed_out": 0}

    universe_total_count = len(symbol_records)

    stage_started = time.perf_counter()
    metadata_pass_records: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
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
    stage_timers["metadata_filter_seconds"] = round(max(time.perf_counter() - stage_started, 0.0), 4)
    stage_counts["metadata"] = {
        "entered": int(universe_total_count),
        "exited": int(len(metadata_pass_records)),
    }

    if progress_callback:
        progress_callback(
            {
                "event": "metadata_filter_complete",
                "universe_total_count": int(universe_total_count),
                "metadata_pass_count": int(len(metadata_pass_records)),
                "filtered_count_by_reason": dict(filtered_count_by_reason),
            }
        )

    stage_started = time.perf_counter()
    start_date, end_date = _history_window(lookback_days=max(1000, int(SCANNER_MIN_HISTORY_DAYS) * 3))
    light_start, light_end = _history_window(lookback_days=max(90, min(180, int(SCANNER_MIN_HISTORY_DAYS))))
    lightweight_history_min = max(40, min(120, int(SCANNER_MIN_HISTORY_DAYS)))
    lightweight_request_timeout = max(5.0, min(float(request_timeout_seconds), 15.0))
    full_score_request_timeout = max(5.0, min(float(request_timeout_seconds), 20.0))
    benchmark_history = pd.DataFrame()
    benchmark_light_history = pd.DataFrame()
    benchmark_history = _invoke_loader_with_timeout(
        data_loader,
        benchmark_symbol,
        start_date,
        end_date,
        timeout_seconds=max(float(request_timeout_seconds), 1.0),
    )
    benchmark_light_history = benchmark_history.tail(max(40, lightweight_history_min)).copy()
    stage_timers["benchmark_fetch_seconds"] = round(max(time.perf_counter() - stage_started, 0.0), 4)

    lightweight_survivors: list[dict[str, Any]] = []
    lightweight_features: dict[str, dict[str, float]] = {}
    lightweight_processed = 0

    metadata_for_lightweight = list(metadata_pass_records)
    # This stage performs network history fetches. Keep it within the configured
    # coarse budget and rotate the window across cycles for broad coverage.
    stage_a_soft_limit = max(1, int(max(coarse_candidate_limit, 1)))
    timeout_bound_limit = max(
        int(max(lightweight_batch_size, 1)),
        int((max(float(max_scan_seconds), 1.0) / max(lightweight_request_timeout, 1.0)) * max(lightweight_batch_size, 1) * 0.85),
    )
    effective_stage_a_limit = min(len(metadata_for_lightweight), stage_a_soft_limit, timeout_bound_limit)
    metadata_for_lightweight, deferred_records = _rotating_budget_window(metadata_for_lightweight, effective_stage_a_limit)
    budget_deferred_count = int(len(deferred_records))
    budget_deferred_symbols = [normalize_symbol(item.get("symbol", "")) for item in deferred_records[:200]]
    if budget_deferred_count:
        filtered_count_by_reason["deferred_by_scan_budget_window"] = int(
            filtered_count_by_reason.get("deferred_by_scan_budget_window", 0)
        ) + int(budget_deferred_count)

    if progress_callback:
        progress_callback(
            {
                "event": "lightweight_scan_start",
                "total": int(len(metadata_for_lightweight)),
                "budget_deferred_count": int(budget_deferred_count),
                "batch_size": int(max(lightweight_batch_size, 1)),
            }
        )

    stage_started = time.perf_counter()
    for batch_start in range(0, len(metadata_for_lightweight), max(lightweight_batch_size, 1)):
        if time.perf_counter() >= deadline:
            deadline_hit = True
            for item in metadata_for_lightweight[batch_start:]:
                symbol = normalize_symbol(item.get("symbol", ""))
                timed_out_symbols.append(symbol)
                scan_results.append(_build_error_result(item, "scan timeout: lightweight stage exceeded SCANNER_MAX_SCAN_SECONDS"))
            break

        batch = metadata_for_lightweight[batch_start : batch_start + max(lightweight_batch_size, 1)]
        symbols = [normalize_symbol(item.get("symbol", "")) for item in batch]
        batch_frames: dict[str, pd.DataFrame] = {}
        batch_exception: Exception | None = None
        batch_symbol_errors: dict[str, Exception] = {}

        for attempt in range(min(max(int(max_retries), 0), 1) + 1):
            remaining_timeout = _remaining_timeout_seconds(lightweight_request_timeout)
            if remaining_timeout <= 0:
                deadline_hit = True
                batch_exception = TimeoutError("lightweight stage exceeded SCANNER_MAX_SCAN_SECONDS")
                break
            try:
                batch_telemetry["attempted"] += 1
                batch_frames = dict(
                    _invoke_loader_with_timeout(
                        data_loader_batch,
                        symbols,
                        light_start,
                        light_end,
                        timeout_seconds=remaining_timeout,
                    )
                    or {}
                )
                batch_symbol_errors = dict(getattr(data_loader_batch, "_last_errors", {}) or {})
                if batch_frames:
                    batch_telemetry["succeeded"] += 1
                else:
                    batch_telemetry["failed"] += 1
                break
            except FutureTimeoutError:
                batch_telemetry["failed"] += 1
                batch_telemetry["timed_out"] += 1
                batch_exception = TimeoutError(f"lightweight batch timeout after {lightweight_request_timeout:.1f}s")
                if attempt >= int(max_retries):
                    break
                retries += 1
                timed_out_symbols.extend(symbols)
                time.sleep((2 ** attempt) * 0.6 + random.uniform(0.0, 0.2))
            except Exception as exc:
                batch_telemetry["failed"] += 1
                batch_exception = exc
                if attempt >= int(max_retries):
                    break
                retries += 1
                if _is_rate_limit_error(f"{type(exc).__name__}: {exc}"):
                    rate_limit_retries += 1
                    time.sleep((2 ** attempt) * 1.2 + random.uniform(0.0, 0.2))
                else:
                    time.sleep((2 ** attempt) * 0.5 + random.uniform(0.0, 0.2))

        for idx, item in enumerate(batch):
            if time.perf_counter() >= deadline:
                deadline_hit = True
                remaining_items = batch[idx:]
                for pending in remaining_items:
                    pending_symbol = normalize_symbol(pending.get("symbol", ""))
                    timed_out_symbols.append(pending_symbol)
                    scan_results.append(_build_error_result(pending, "scan timeout: lightweight stage exceeded SCANNER_MAX_SCAN_SECONDS"))
                break
            symbol = normalize_symbol(item.get("symbol", ""))
            symbol_started = time.perf_counter()
            history = batch_frames.get(symbol)
            if history is None or history.empty:
                symbol_error = batch_symbol_errors.get(symbol)
                if batch_exception is not None:
                    scan_results.append(_build_error_result(item, f"lightweight data error: {type(batch_exception).__name__}: {batch_exception}"))
                elif symbol_error is not None:
                    scan_results.append(_build_error_result(item, f"lightweight data error: {type(symbol_error).__name__}: {symbol_error}"))
                else:
                    reasons = ["no market data returned"]
                    _count_reasons(filtered_count_by_reason, reasons)
                    scan_results.append(_build_rejected_result(item, reasons))
            else:
                if symbol not in price_history_by_symbol:
                    rows = _frame_to_close_rows(history)
                    if rows:
                        price_history_by_symbol[symbol] = rows
                lightweight_filter = validate_symbol_data(
                    symbol,
                    history,
                    min_price=SCANNER_MIN_PRICE,
                    min_avg_dollar_volume=SCANNER_MIN_AVG_DOLLAR_VOLUME,
                    min_history_days=lightweight_history_min,
                    max_missing_percent=SCANNER_MAX_MISSING_PERCENT,
                    max_stale_business_days=SCANNER_MAX_STALE_BUSINESS_DAYS,
                )
                if not lightweight_filter.get("passed"):
                    reasons = list(lightweight_filter.get("reasons") or [])
                    _count_reasons(filtered_count_by_reason, reasons)
                    scan_results.append(_build_rejected_result(item, reasons))
                else:
                    coarse = _coarse_metrics(symbol, history, benchmark_light_history)
                    if coarse is None:
                        reasons = ["insufficient history for coarse ranking"]
                        _count_reasons(filtered_count_by_reason, reasons)
                        scan_results.append(_build_rejected_result(item, reasons))
                    else:
                        lightweight_survivors.append(item)
                        lightweight_features[symbol] = coarse
            symbol_elapsed_ms = (time.perf_counter() - symbol_started) * 1000.0
            slow_symbols.append((symbol_elapsed_ms, symbol, "lightweight_filter"))
            lightweight_processed += 1
            if progress_callback and (
                lightweight_processed % max(int(progress_every), 1) == 0
                or lightweight_processed == len(metadata_for_lightweight)
            ):
                progress_callback(
                    {
                        "event": "scan_progress",
                        "stage": "lightweight",
                        "completed": int(lightweight_processed),
                        "total": int(len(metadata_for_lightweight)),
                        "symbol": symbol,
                        "elapsed_seconds": round(max(time.perf_counter() - started, 0.0), 4),
                    }
                )
    stage_timers["lightweight_pipeline_seconds"] = round(max(time.perf_counter() - stage_started, 0.0), 4)
    stage_counts["lightweight"] = {
        "entered": int(len(metadata_for_lightweight)),
        "exited": int(len(lightweight_survivors)),
    }

    stage_started = time.perf_counter()
    coarse_ranked = sorted(
        lightweight_survivors,
        key=lambda item: (
            -float((lightweight_features.get(normalize_symbol(item.get("symbol", ""))) or {}).get("coarse_score") or 0.0),
            normalize_symbol(item.get("symbol", "")),
        ),
    )
    effective_coarse_limit = int(coarse_candidate_limit) if int(coarse_candidate_limit) > 0 else len(coarse_ranked)
    stage_c_candidates = coarse_ranked[:effective_coarse_limit]
    for item in coarse_ranked[effective_coarse_limit:]:
        scan_results.append(_build_rejected_result(item, ["coarse ranking below candidate cutoff"]))
        _count_reasons(filtered_count_by_reason, ["coarse ranking below candidate cutoff"])

    effective_deep_limit = int(deep_score_limit) if int(deep_score_limit) > 0 else len(stage_c_candidates)
    deep_candidates = stage_c_candidates[:effective_deep_limit]
    for item in stage_c_candidates[effective_deep_limit:]:
        scan_results.append(_build_rejected_result(item, ["coarse ranking below deep score limit"]))
        _count_reasons(filtered_count_by_reason, ["coarse ranking below deep score limit"])
    stage_timers["coarse_ranking_seconds"] = round(max(time.perf_counter() - stage_started, 0.0), 4)
    stage_counts["coarse_ranking"] = {
        "entered": int(len(lightweight_survivors)),
        "exited": int(len(stage_c_candidates)),
    }

    if progress_callback:
        progress_callback(
            {
                "event": "full_score_stage_start",
                "total": int(len(deep_candidates)),
                "batch_size": int(max(batch_size, 1)),
            }
        )

    stage_started = time.perf_counter()
    deep_scored_count = 0
    for batch_start in range(0, len(deep_candidates), max(batch_size, 1)):
        if time.perf_counter() >= deadline:
            deadline_hit = True
            for item in deep_candidates[batch_start:]:
                symbol = normalize_symbol(item.get("symbol", ""))
                timed_out_symbols.append(symbol)
                scan_results.append(_build_error_result(item, "scan timeout: full scoring stage exceeded SCANNER_MAX_SCAN_SECONDS"))
            break

        batch = deep_candidates[batch_start : batch_start + max(batch_size, 1)]
        symbols = [normalize_symbol(item.get("symbol", "")) for item in batch]
        batch_frames: dict[str, pd.DataFrame] = {}
        batch_exception: Exception | None = None
        batch_symbol_errors: dict[str, Exception] = {}
        for attempt in range(min(max(int(max_retries), 0), 1) + 1):
            remaining_timeout = _remaining_timeout_seconds(full_score_request_timeout)
            if remaining_timeout <= 0:
                deadline_hit = True
                batch_exception = TimeoutError("full scoring stage exceeded SCANNER_MAX_SCAN_SECONDS")
                break
            try:
                batch_telemetry["attempted"] += 1
                batch_frames = dict(
                    _invoke_loader_with_timeout(
                        data_loader_batch,
                        symbols,
                        start_date,
                        end_date,
                        timeout_seconds=remaining_timeout,
                    )
                    or {}
                )
                batch_symbol_errors = dict(getattr(data_loader_batch, "_last_errors", {}) or {})
                if batch_frames:
                    batch_telemetry["succeeded"] += 1
                else:
                    batch_telemetry["failed"] += 1
                break
            except FutureTimeoutError:
                batch_telemetry["failed"] += 1
                batch_telemetry["timed_out"] += 1
                batch_exception = TimeoutError(f"full scoring batch timeout after {full_score_request_timeout:.1f}s")
                if attempt >= int(max_retries):
                    break
                retries += 1
                timed_out_symbols.extend(symbols)
                time.sleep((2 ** attempt) * 0.6 + random.uniform(0.0, 0.2))
            except Exception as exc:
                batch_telemetry["failed"] += 1
                batch_exception = exc
                if attempt >= int(max_retries):
                    break
                retries += 1
                if _is_rate_limit_error(f"{type(exc).__name__}: {exc}"):
                    rate_limit_retries += 1
                    time.sleep((2 ** attempt) * 1.2 + random.uniform(0.0, 0.2))
                else:
                    time.sleep((2 ** attempt) * 0.5 + random.uniform(0.0, 0.2))

        for idx, item in enumerate(batch):
            if time.perf_counter() >= deadline:
                deadline_hit = True
                remaining_items = batch[idx:]
                for pending in remaining_items:
                    pending_symbol = normalize_symbol(pending.get("symbol", ""))
                    timed_out_symbols.append(pending_symbol)
                    scan_results.append(_build_error_result(pending, "scan timeout: full scoring stage exceeded SCANNER_MAX_SCAN_SECONDS"))
                break
            symbol = normalize_symbol(item.get("symbol", ""))
            symbol_started = time.perf_counter()
            history = batch_frames.get(symbol)
            if history is None or history.empty:
                symbol_error = batch_symbol_errors.get(symbol)
                if batch_exception is not None:
                    result = _build_error_result(item, f"full scoring data error: {type(batch_exception).__name__}: {batch_exception}")
                elif symbol_error is not None:
                    result = _build_error_result(item, f"full scoring data error: {type(symbol_error).__name__}: {symbol_error}")
                else:
                    result = _build_error_result(item, "no full-history data returned")
            else:
                rows = _frame_to_close_rows(history)
                if rows:
                    price_history_by_symbol[symbol] = rows
                result, retry_count = _scan_with_retry(
                    symbol_record=item,
                    benchmark_history=benchmark_history,
                    data_loader=data_loader,
                    max_retries=max_retries,
                    retry_jitter_seconds=0.2,
                    history=history,
                )
                retries += int(retry_count)
                feature = lightweight_features.get(symbol) or {}
                if feature:
                    result["coarse_metrics"] = feature
            if result.get("status") == "rejected":
                _count_reasons(filtered_count_by_reason, list(result.get("rejection_reasons") or []))
            scan_results.append(result)
            deep_scored_count += 1
            symbol_elapsed_ms = (time.perf_counter() - symbol_started) * 1000.0
            slow_symbols.append((symbol_elapsed_ms, symbol, "full_scoring"))

            if progress_callback and (
                deep_scored_count % max(int(progress_every), 1) == 0
                or deep_scored_count == len(deep_candidates)
            ):
                progress_callback(
                    {
                        "event": "scan_progress",
                        "stage": "full_scoring",
                        "completed": int(deep_scored_count),
                        "total": int(len(deep_candidates)),
                        "symbol": symbol,
                        "elapsed_seconds": round(max(time.perf_counter() - started, 0.0), 4),
                    }
                )
    stage_timers["full_scoring_seconds"] = round(max(time.perf_counter() - stage_started, 0.0), 4)
    stage_counts["full_scoring"] = {
        "entered": int(len(deep_candidates)),
        "exited": int(deep_scored_count),
    }

    ranking_started = time.perf_counter()
    ranked = rank_scan_results(scan_results)
    stage_timers["ranking_seconds"] = round(max(time.perf_counter() - ranking_started, 0.0), 4)

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

    current_mb, peak_mb = _memory_snapshot_mb()
    summary["benchmark_symbol"] = benchmark_symbol
    summary["benchmark_rows"] = int(len(benchmark_history))
    summary["benchmark_reused"] = True
    summary["rate_limit_retry_count"] = int(rate_limit_retries)
    summary["timeout_count"] = int(len(timed_out_symbols))
    summary["timed_out_symbols"] = list(dict.fromkeys(timed_out_symbols))[:200]
    summary["max_workers"] = int(max_workers)
    summary["batch_size"] = int(max(batch_size, 1))
    summary["lightweight_batch_size"] = int(max(lightweight_batch_size, 1))
    summary["lightweight_request_timeout_seconds"] = float(round(lightweight_request_timeout, 3))
    summary["full_score_request_timeout_seconds"] = float(round(full_score_request_timeout, 3))
    summary["coarse_candidate_limit"] = int(effective_coarse_limit)
    summary["deep_score_limit"] = int(effective_deep_limit)
    summary["max_scan_seconds"] = int(max_scan_seconds)
    summary["progress_every"] = int(max(progress_every, 1))
    summary["stage_counts"] = dict(stage_counts)
    summary["stage_timings_seconds"] = dict(stage_timers)
    summary["memory_current_mb"] = float(current_mb)
    summary["memory_peak_mb"] = float(peak_mb)
    summary["slowest_20_symbols"] = [
        {"symbol": symbol, "stage": stage, "elapsed_ms": round(ms, 3)}
        for ms, symbol, stage in sorted(slow_symbols, key=lambda item: item[0], reverse=True)[:20]
    ]

    ranking_completed = True
    infrastructure_failed = bool(universe_total_count <= 0 or (len(metadata_pass_records) > 0 and lightweight_processed == 0))
    stage_b_survivors = len(lightweight_survivors)
    stage_c_survivors = len(stage_c_candidates)
    deep_threshold = max(1, int(max(stage_c_survivors, 1) * 0.50))
    partial_success_acceptable = bool(ranking_completed and deep_scored_count >= deep_threshold)

    if not ranking_completed or infrastructure_failed:
        summary_status = "failed"
    elif deadline_hit and not partial_success_acceptable:
        summary_status = "timed_out"
    elif deadline_hit and partial_success_acceptable:
        summary_status = "partial_success"
    elif int(summary.get("error_count") or 0) > 0 and partial_success_acceptable:
        summary_status = "partial_success"
    elif stage_b_survivors == 0 and universe_total_count > 0:
        summary_status = "failed"
    else:
        summary_status = "success"

    summary["status"] = summary_status
    summary["partial_success_thresholds"] = {
        "deep_scored_minimum": int(deep_threshold),
        "partial_success_acceptable": bool(partial_success_acceptable),
    }
    summary["stage_a_total"] = int(universe_total_count)
    summary["stage_a_budget_window_selected"] = int(len(metadata_for_lightweight))
    summary["stage_a_budget_window_deferred"] = int(budget_deferred_count)
    summary["stage_a_budget_deferred_symbols"] = list(dict.fromkeys(budget_deferred_symbols))[:200]
    summary["batch_count"] = dict(batch_telemetry)
    summary["symbols_skipped_due_budget"] = int(budget_deferred_count)
    summary["stage_b_survivors"] = int(stage_b_survivors)
    summary["stage_c_survivors"] = int(stage_c_survivors)
    summary["deep_scored_count"] = int(deep_scored_count)
    summary["partial_scan"] = bool(deadline_hit or budget_deferred_count > 0)

    top_ranked = []
    for item in ranked[:10]:
        strategy_scores = item.get("strategy_specific_scores") or {}
        top_ranked.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "rank": int(item.get("rank") or 0),
                "quantum_score": float((item.get("quantum_score") or {}).get("final_score") or item.get("overall_score") or 0.0),
                "strategy_score": float(item.get("ranking_score") or 0.0),
                "strategy_ids": sorted([str(key) for key in strategy_scores.keys()]),
                "risk_reward": float((item.get("quantum_score") or {}).get("reward_risk_ratio") or 0.0),
                "liquidity": float(item.get("liquidity_score") or 0.0),
                "data_quality": str(((item.get("data_quality") or {}).get("quantum") or {}).get("status") or "unknown"),
                "rejection_reasons": list(item.get("rejection_reasons") or []),
            }
        )

    if progress_callback:
        progress_callback(
            {
                "event": "ranking_complete",
                "eligible_count": int(summary.get("eligible_count") or 0),
                "failed_symbol_count": int(summary.get("failed_symbol_count") or 0),
                "top_ranked_candidates": top_ranked,
            }
        )
        progress_callback(
            {
                "event": "full_universe_scan_complete",
                "total_universe": int(universe_total_count),
                "stage_b_survivors": int(stage_b_survivors),
                "stage_c_survivors": int(stage_c_survivors),
                "deep_scored_count": int(deep_scored_count),
                "eligible_candidates": int(summary.get("eligible_count") or 0),
                "failed_symbols": int(summary.get("failed_symbol_count") or 0),
                "timeout_count": int(summary.get("timeout_count") or 0),
                "rate_limit_count": int(summary.get("rate_limit_retry_count") or 0),
                "top_10_candidates": top_ranked,
                "total_duration": float(summary.get("duration_seconds") or 0.0),
                "orders_recommended": 0,
                "orders_submission_requested": 0,
                "orders_attempted": 0,
                "orders_submitted": 0,
                "orders_filled": 0,
                "orders_rejected": 0,
                "exit_status": str(summary_status),
            }
        )

    benchmark_price_history = _frame_to_close_rows(benchmark_history)

    return {
        "scan_results": scan_results,
        "ranked_candidates": ranked,
        "summary": summary,
        "price_history_by_symbol": price_history_by_symbol,
        "benchmark_price_history": benchmark_price_history,
    }
