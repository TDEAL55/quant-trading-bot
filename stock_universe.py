STOCK_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AMD",
    "AVGO",
    "NFLX",
    "PLTR",
    "CRM",
    "ORCL",
    "JPM",
    "BAC",
    "WMT",
    "COST",
    "HD",
    "LLY",
    "UNH",
    "XOM",
    "CVX",
    "SPY",
    "QQQ",
    "BRK-B",
]


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace(".", "-")


def is_supported_symbol_format(symbol: str) -> bool:
    value = normalize_symbol(symbol)
    if not value:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ.-")
    return all(ch in allowed for ch in value)


def load_stock_universe() -> list[dict[str, str]]:
    return build_stock_universe()


def build_stock_universe(
    selected_universes: list[str] | None = None,
    excluded_symbols: list[str] | None = None,
    additional_symbols: list[str] | None = None,
    max_universe_size: int = 300,
) -> list[dict[str, str | bool | list[str]]]:
    del selected_universes
    excluded = {normalize_symbol(item) for item in list(excluded_symbols or []) if str(item).strip()}
    additions = [normalize_symbol(item) for item in list(additional_symbols or []) if str(item).strip()]

    raw = [normalize_symbol(symbol) for symbol in STOCK_UNIVERSE] + additions
    deduped = sorted({symbol for symbol in raw if is_supported_symbol_format(symbol) and symbol not in excluded})
    limited = deduped[: max(int(max_universe_size), 0)]

    rows = []
    for normalized in limited:
        rows.append(
            {
                "symbol": normalized,
                "company_name": normalized,
                "sector": "Unknown",
                "industry": "Unknown",
                "universe_groups": ["benchmarks" if normalized in {"SPY", "QQQ"} else "core"],
                "is_etf": normalized in {"SPY", "QQQ"},
                "benchmark_only": normalized in {"SPY", "QQQ"},
            }
        )
    return rows
