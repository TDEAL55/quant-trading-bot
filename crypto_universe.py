from __future__ import annotations

import os
from typing import Any, Callable

from alpaca_paper_broker import AlpacaPaperBroker


PREFERRED_BASE_ASSETS = (
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "DOGE",
    "AVAX",
    "LINK",
    "LTC",
    "BCH",
    "AAVE",
    "UNI",
)
STABLE_BASE_ASSETS = {"USD", "USDC", "USDT", "DAI", "PYUSD"}


class CryptoUniverseError(RuntimeError):
    pass


def canonical_crypto_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper().replace("-", "/")
    if not symbol:
        return ""
    if "/" not in symbol and symbol.endswith("USD") and len(symbol) > 3:
        symbol = f"{symbol[:-3]}/USD"
    return symbol


def _csv_symbols(value: str | None) -> set[str]:
    return {
        canonical_crypto_symbol(item)
        for item in str(value or "").split(",")
        if canonical_crypto_symbol(item)
    }


def _base_asset(symbol: str) -> str:
    return canonical_crypto_symbol(symbol).split("/", 1)[0]


def build_crypto_universe(
    assets: list[dict[str, Any]],
    *,
    included_symbols: set[str] | None = None,
    excluded_symbols: set[str] | None = None,
    maximum_symbols: int = 50,
) -> list[dict[str, Any]]:
    include = {canonical_crypto_symbol(item) for item in set(included_symbols or set()) if canonical_crypto_symbol(item)}
    exclude = {canonical_crypto_symbol(item) for item in set(excluded_symbols or set()) if canonical_crypto_symbol(item)}
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    for raw in assets or []:
        symbol = canonical_crypto_symbol((raw or {}).get("symbol"))
        if not symbol.endswith("/USD") or _base_asset(symbol) in STABLE_BASE_ASSETS:
            continue
        if include and symbol not in include:
            continue
        if symbol in exclude:
            continue
        if not bool((raw or {}).get("tradable", False)):
            continue
        rows_by_symbol[symbol] = {
            **dict(raw or {}),
            "symbol": symbol,
            "asset_class": "crypto",
            "market": "24/7",
            "quote_currency": "USD",
        }

    preferred_rank = {base: index for index, base in enumerate(PREFERRED_BASE_ASSETS)}
    ranked = sorted(
        rows_by_symbol.values(),
        key=lambda row: (
            preferred_rank.get(_base_asset(str(row.get("symbol") or "")), len(preferred_rank)),
            str(row.get("symbol") or ""),
        ),
    )
    limit = max(int(maximum_symbols or 0), 0)
    return ranked if limit == 0 else ranked[:limit]


def load_crypto_universe(
    *,
    broker_factory: Callable[..., AlpacaPaperBroker] = AlpacaPaperBroker,
    maximum_symbols: int | None = None,
) -> list[dict[str, Any]]:
    requested = _csv_symbols(os.getenv("CRYPTO_SYMBOLS"))
    excluded = _csv_symbols(os.getenv("CRYPTO_EXCLUDE_SYMBOLS", "USDC/USD,USDT/USD"))
    resolved_limit = int(maximum_symbols if maximum_symbols is not None else os.getenv("CRYPTO_MAX_UNIVERSE_SIZE", "50"))
    try:
        broker = broker_factory(mode="PAPER")
        assets = list(broker.get_tradable_crypto_assets() or [])
    except Exception as exc:
        raise CryptoUniverseError(f"Unable to load Alpaca crypto assets: {type(exc).__name__}: {exc}") from exc
    universe = build_crypto_universe(
        assets,
        included_symbols=requested,
        excluded_symbols=excluded,
        maximum_symbols=resolved_limit,
    )
    if not universe:
        raise CryptoUniverseError("Alpaca returned no active tradable USD crypto pairs")
    return universe

