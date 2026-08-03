from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any

from alpaca_client import AlpacaClient
from config import (
    ALPACA_ASSET_REQUEST_TIMEOUT_SECONDS,
    SCANNER_ADDITIONAL_SYMBOLS,
    SCANNER_EXCLUDED_SYMBOLS,
    SCANNER_INCLUDE_ETFS,
    SCANNER_MAX_UNIVERSE_SIZE,
    SCANNER_MAX_RETRIES,
    SCANNER_UNIVERSE_CACHE_TTL_SECONDS,
)


STOCK_UNIVERSE = []  # Legacy compatibility only. Runtime universe source is Alpaca Assets API.


_UNIVERSE_CACHE: dict[str, Any] = {
    "market_date": "",
    "fetched_at": 0.0,
    "records": [],
    "stats": {},
}


def _utc_market_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _asset_value(asset: Any, key: str, default: Any = None) -> Any:
    if isinstance(asset, dict):
        return asset.get(key, default)
    return getattr(asset, key, default)


def _apply_max_size(rows: list[dict[str, Any]], max_universe_size: int) -> list[dict[str, Any]]:
    limit = int(max_universe_size)
    if limit <= 0:
        return rows
    return rows[:limit]


def _is_etf_asset(asset: Any) -> bool:
    symbol = normalize_symbol(_asset_value(asset, "symbol", ""))
    exchange = str(_asset_value(asset, "exchange", "") or "").upper()
    name = str(_asset_value(asset, "name", "") or "").lower()
    return symbol.endswith("ETF") or " ETF" in name or exchange in {"ARCA", "BATS"}


def _asset_to_record(asset: Any) -> dict[str, Any]:
    symbol = normalize_symbol(_asset_value(asset, "symbol", ""))
    name = str(_asset_value(asset, "name", symbol) or symbol)
    is_etf = _is_etf_asset(asset)
    return {
        "symbol": symbol,
        "company_name": name,
        "sector": "Unknown",
        "industry": "Unknown",
        "universe_groups": ["alpaca_assets", "etf" if is_etf else "equity"],
        "is_etf": bool(is_etf),
        "benchmark_only": False,
        "asset_class": str(_asset_value(asset, "asset_class", "") or ""),
        "status": str(_asset_value(asset, "status", "") or ""),
        "tradable": bool(_as_bool(_asset_value(asset, "tradable", False))),
        "fractionable": bool(_as_bool(_asset_value(asset, "fractionable", False))),
    }


def _fetch_alpaca_assets() -> list[Any]:
    client = AlpacaClient(mode="PAPER")
    attempts = 0
    max_retries = max(int(SCANNER_MAX_RETRIES), 0)
    timeout_seconds = max(float(ALPACA_ASSET_REQUEST_TIMEOUT_SECONDS), 1.0)

    while True:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(client.get_assets)
                rows = future.result(timeout=timeout_seconds)
            return list(rows or [])
        except FutureTimeoutError as exc:
            if attempts >= max_retries:
                raise TimeoutError(f"alpaca assets request timed out after {timeout_seconds:.1f}s") from exc
            attempts += 1
        except Exception as exc:
            if attempts >= max_retries:
                raise
            attempts += 1
            message = str(exc).lower()
            if "429" in message or "rate limit" in message or "too many requests" in message:
                time.sleep((2 ** (attempts - 1)) * 1.2 + random.uniform(0.0, 0.2))
            else:
                time.sleep((2 ** (attempts - 1)) * 0.4 + random.uniform(0.0, 0.2))


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace(".", "-")


def is_supported_symbol_format(symbol: str) -> bool:
    value = normalize_symbol(symbol)
    if not value:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ.-")
    return all(ch in allowed for ch in value)


def load_stock_universe(
    *,
    refresh: bool = False,
    market_date: str | None = None,
    selected_universes: list[str] | None = None,
    excluded_symbols: list[str] | None = None,
    additional_symbols: list[str] | None = None,
    max_universe_size: int | None = None,
    include_etfs: bool | None = None,
) -> list[dict[str, Any]]:
    del selected_universes  # Universe source is Alpaca assets, not static named lists.
    target_date = str(market_date or _utc_market_date())
    now_ts = time.time()
    cache_ttl_seconds = max(float(SCANNER_UNIVERSE_CACHE_TTL_SECONDS), 0.0)
    cache_age_seconds = now_ts - float(_UNIVERSE_CACHE.get("fetched_at") or 0.0)
    cache_valid = cache_ttl_seconds <= 0 or cache_age_seconds <= cache_ttl_seconds

    if (
        not refresh
        and _UNIVERSE_CACHE.get("market_date") == target_date
        and _UNIVERSE_CACHE.get("records")
        and cache_valid
    ):
        return list(_UNIVERSE_CACHE.get("records") or [])

    resolved_max_universe_size = SCANNER_MAX_UNIVERSE_SIZE if max_universe_size is None else max_universe_size
    rows = build_stock_universe(
        selected_universes=None,
        excluded_symbols=excluded_symbols,
        additional_symbols=additional_symbols,
        max_universe_size=resolved_max_universe_size,
        include_etfs=(SCANNER_INCLUDE_ETFS if include_etfs is None else include_etfs),
    )
    _UNIVERSE_CACHE["market_date"] = target_date
    _UNIVERSE_CACHE["fetched_at"] = now_ts
    _UNIVERSE_CACHE["records"] = list(rows)
    stats = dict(_UNIVERSE_CACHE.get("stats") or {})
    stats["cache_ttl_seconds"] = cache_ttl_seconds
    stats["cache_age_seconds"] = 0.0
    stats["max_universe_size"] = int(resolved_max_universe_size)
    stats["max_universe_mode"] = "unlimited" if int(resolved_max_universe_size) <= 0 else "capped"
    stats["universe_source"] = "alpaca_assets_api"
    _UNIVERSE_CACHE["stats"] = stats
    return rows


def build_stock_universe(
    selected_universes: list[str] | None = None,
    excluded_symbols: list[str] | None = None,
    additional_symbols: list[str] | None = None,
    max_universe_size: int = 300,
    include_etfs: bool = True,
) -> list[dict[str, str | bool | list[str]]]:
    del selected_universes
    excluded_set = {
        normalize_symbol(item)
        for item in list(SCANNER_EXCLUDED_SYMBOLS) + list(excluded_symbols or [])
        if str(item).strip()
    }
    additions = [
        normalize_symbol(item)
        for item in list(SCANNER_ADDITIONAL_SYMBOLS) + list(additional_symbols or [])
        if str(item).strip()
    ]

    assets = list(_fetch_alpaca_assets() or [])
    stats = {
        "retrieved_assets": len(assets),
        "excluded_inactive": 0,
        "excluded_non_tradable": 0,
        "excluded_not_us_equity": 0,
        "excluded_etf": 0,
        "excluded_invalid_symbol": 0,
        "excluded_duplicate_symbol": 0,
        "excluded_config": 0,
        "included_count": 0,
        "max_universe_size": int(max_universe_size),
        "max_universe_mode": "unlimited" if int(max_universe_size) <= 0 else "capped",
        "universe_source": "alpaca_assets_api",
    }

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for asset in assets:
        status = str(_asset_value(asset, "status", "") or "").upper()
        asset_class = str(_asset_value(asset, "asset_class", "") or "").upper()
        tradable = bool(_as_bool(_asset_value(asset, "tradable", False)))
        symbol = normalize_symbol(_asset_value(asset, "symbol", ""))

        if asset_class != "US_EQUITY":
            stats["excluded_not_us_equity"] += 1
            continue
        if status != "ACTIVE":
            stats["excluded_inactive"] += 1
            continue
        if not tradable:
            stats["excluded_non_tradable"] += 1
            continue
        if not include_etfs and _is_etf_asset(asset):
            stats["excluded_etf"] += 1
            continue
        if not symbol or not is_supported_symbol_format(symbol):
            stats["excluded_invalid_symbol"] += 1
            continue
        if symbol in seen:
            stats["excluded_duplicate_symbol"] += 1
            continue
        if symbol in excluded_set:
            stats["excluded_config"] += 1
            continue

        seen.add(symbol)
        rows.append(_asset_to_record(asset))

    for symbol in additions:
        if not symbol or not is_supported_symbol_format(symbol):
            continue
        if symbol in excluded_set or symbol in seen:
            continue
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "company_name": symbol,
                "sector": "Unknown",
                "industry": "Unknown",
                "universe_groups": ["manual_addition"],
                "is_etf": False,
                "benchmark_only": False,
                "asset_class": "US_EQUITY",
                "status": "ACTIVE",
                "tradable": True,
                "fractionable": True,
            }
        )

    rows = sorted(rows, key=lambda item: str(item.get("symbol") or ""))
    rows = _apply_max_size(rows, int(max_universe_size))
    stats["included_count"] = len(rows)
    _UNIVERSE_CACHE["stats"] = stats
    return rows


def get_universe_cache_stats() -> dict[str, Any]:
    return dict(_UNIVERSE_CACHE.get("stats") or {})
