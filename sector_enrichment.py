from __future__ import annotations

import json
import os
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yfinance as yf


UNKNOWN_LABELS = {"", "unknown", "n/a", "na", "none", "unclassified"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_known(value: Any) -> bool:
    return str(value or "").strip().lower() not in UNKNOWN_LABELS


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _valid_metadata(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    sector = str(value.get("sector") or "").strip()
    industry = str(value.get("industry") or "").strip()
    if not _is_known(sector):
        return None
    return {
        "sector": sector,
        "industry": industry if _is_known(industry) else "Unknown",
        "source": str(value.get("source") or "cache"),
        "fetched_at": str(value.get("fetched_at") or ""),
    }


def fetch_yahoo_sector(symbol: str, timeout_seconds: float) -> dict[str, str] | None:
    """Fetch bounded sector metadata from Yahoo's exact-symbol search result."""
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return None

    search = yf.Search(
        normalized,
        max_results=5,
        news_count=0,
        lists_count=0,
        include_cb=False,
        include_nav_links=False,
        include_research=False,
        include_cultural_assets=False,
        timeout=max(float(timeout_seconds), 1.0),
        raise_errors=True,
    )
    for quote in list(search.quotes or []):
        if _normalize_symbol((quote or {}).get("symbol")) != normalized:
            continue
        quote_type = str((quote or {}).get("quoteType") or "").strip().upper()
        if quote_type == "ETF":
            return {
                "sector": "ETF",
                "industry": "Exchange Traded Fund",
                "source": "yahoo_search",
                "fetched_at": _utc_iso(),
            }
        sector = str((quote or {}).get("sector") or (quote or {}).get("sectorDisp") or "").strip()
        industry = str((quote or {}).get("industry") or (quote or {}).get("industryDisp") or "").strip()
        if _is_known(sector):
            return {
                "sector": sector,
                "industry": industry if _is_known(industry) else "Unknown",
                "source": "yahoo_search",
                "fetched_at": _utc_iso(),
            }
    return None


def _load_cache(path: Path, ttl_days: int, now_seconds: float) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    maximum_age = max(int(ttl_days), 1) * 86400
    valid: dict[str, dict[str, str]] = {}
    for raw_symbol, raw_metadata in dict((payload or {}).get("symbols") or {}).items():
        symbol = _normalize_symbol(raw_symbol)
        metadata = _valid_metadata(raw_metadata)
        if not symbol or metadata is None:
            continue
        try:
            fetched_at = datetime.fromisoformat(metadata["fetched_at"].replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age_seconds = max(now_seconds - fetched_at.timestamp(), 0.0)
        except Exception:
            continue
        if age_seconds <= maximum_age:
            valid[symbol] = metadata
    return valid


def _save_cache(path: Path, values: dict[str, dict[str, str]]) -> bool:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _utc_iso(),
            "symbols": {key: values[key] for key in sorted(values)},
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        return True
    except Exception:
        return False
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except Exception:
                pass


def enrich_sector_records(
    records: list[dict[str, Any]],
    *,
    cache_path: str | Path,
    max_symbols: int = 30,
    timeout_seconds: float = 6.0,
    total_timeout_seconds: float = 25.0,
    max_workers: int = 6,
    cache_ttl_days: int = 30,
    fetcher: Callable[[str, float], dict[str, str] | None] = fetch_yahoo_sector,
    now_fn: Callable[[], float] = time.time,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enrich a bounded record set without allowing metadata lookup to block a scan."""
    started = time.monotonic()
    output = [dict(item or {}) for item in list(records or [])]
    ordered_symbols = list(
        dict.fromkeys(
            _normalize_symbol(item.get("symbol"))
            for item in output
            if _normalize_symbol(item.get("symbol")) and not _is_known(item.get("sector"))
        )
    )
    bounded_symbols = ordered_symbols[: max(int(max_symbols), 0)]
    cache_file = Path(cache_path)
    cached = _load_cache(cache_file, ttl_days=cache_ttl_days, now_seconds=float(now_fn()))
    resolved = {symbol: cached[symbol] for symbol in bounded_symbols if symbol in cached}
    requested = [symbol for symbol in bounded_symbols if symbol not in resolved]
    failures: dict[str, str] = {}

    executor: ThreadPoolExecutor | None = None
    futures: dict[Future[dict[str, str] | None], str] = {}
    try:
        if requested:
            executor = ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(requested))))
            futures = {
                executor.submit(fetcher, symbol, max(float(timeout_seconds), 1.0)): symbol
                for symbol in requested
            }
            completed, pending = wait(
                futures,
                timeout=max(float(total_timeout_seconds), 1.0),
            )
            for future in completed:
                symbol = futures[future]
                try:
                    metadata = _valid_metadata(future.result())
                    if metadata is None:
                        failures[symbol] = "metadata_not_available"
                    else:
                        resolved[symbol] = metadata
                        cached[symbol] = metadata
                except Exception as exc:
                    failures[symbol] = type(exc).__name__
            for future in pending:
                symbol = futures[future]
                failures[symbol] = "total_timeout"
                future.cancel()
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    cache_saved = _save_cache(cache_file, cached) if any(symbol in resolved for symbol in requested) else False
    for item in output:
        symbol = _normalize_symbol(item.get("symbol"))
        metadata = resolved.get(symbol)
        if metadata is None or _is_known(item.get("sector")):
            continue
        item["sector"] = metadata["sector"]
        item["industry"] = metadata["industry"]
        item["sector_source"] = metadata["source"]

    unresolved = [symbol for symbol in bounded_symbols if symbol not in resolved]
    metadata = {
        "enabled": True,
        "record_count": len(output),
        "unknown_symbols_seen": len(ordered_symbols),
        "symbols_considered": len(bounded_symbols),
        "symbols_skipped_by_limit": max(len(ordered_symbols) - len(bounded_symbols), 0),
        "cache_hits": sum(1 for symbol in bounded_symbols if symbol in resolved and symbol not in requested),
        "network_resolved": sum(1 for symbol in requested if symbol in resolved),
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "unresolved_symbols": unresolved,
        "failure_types": {key: failures[key] for key in sorted(failures)},
        "cache_saved": bool(cache_saved),
        "elapsed_seconds": round(max(time.monotonic() - started, 0.0), 4),
    }
    return output, metadata
