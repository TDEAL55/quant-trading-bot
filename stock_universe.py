from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from config import (
    SCANNER_ADDITIONAL_SYMBOLS,
    SCANNER_EXCLUDED_SYMBOLS,
    SCANNER_INCLUDE_ETFS,
    SCANNER_MAX_UNIVERSE_SIZE,
    SCANNER_UNIVERSES,
)


SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


LEVERAGED_OR_INVERSE_ETFS = {
    "SQQQ",
    "TQQQ",
    "SPXU",
    "SPXS",
    "SOXS",
    "SOXL",
    "UVXY",
    "VXX",
    "LABU",
    "LABD",
    "TZA",
    "TNA",
}


DEFAULT_UNIVERSE_GROUPS = [
    "sp500",
    "nasdaq100",
    "midcap_liquid",
    "ai_software",
    "semiconductors",
    "data_center_infra",
    "utilities_power",
    "data_center_reits",
    "benchmarks",
]


GROUP_SYMBOLS: dict[str, list[str]] = {
    "sp500": [
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "BRK.B", "JPM", "V", "MA",
        "LLY", "UNH", "XOM", "JNJ", "PG", "HD", "COST", "ABBV", "AVGO", "BAC",
        "CVX", "MRK", "KO", "PEP", "WMT", "TMO", "MCD", "CSCO", "ABT", "ACN",
        "ADBE", "CRM", "DHR", "LIN", "TXN", "DIS", "AMD", "INTC", "QCOM", "VZ",
        "CMCSA", "PFE", "NFLX", "NKE", "ORCL", "AMAT", "NOW", "INTU", "AMGN", "IBM",
        "HON", "SBUX", "CAT", "GE", "GS", "MS", "SPGI", "BLK", "DE", "ADP",
        "LRCX", "MDT", "ISRG", "BKNG", "SYK", "PLD", "CI", "ELV", "VRTX", "TJX",
        "MO", "GILD", "BA", "AXP", "C", "MMM", "SCHW", "PANW", "ADI", "MU",
        "USB", "CB", "MMC", "SO", "DUK", "NEE", "AEP", "D", "ETN", "PH",
        "UNP", "UPS", "FDX", "COP", "SLB", "EOG", "OXY", "PSX", "MPC", "KMI",
        "WELL", "EQIX", "DLR", "VICI", "O", "EXR", "AON", "ICE", "MCO", "CME",
        "PYPL", "UBER", "ABNB", "SHOP", "SNOW", "CRWD", "DDOG", "MDB", "ANET", "CDNS",
    ],
    "nasdaq100": [
        "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "TSLA", "AVGO", "COST",
        "AMD", "NFLX", "ADBE", "PEP", "CSCO", "TMUS", "CMCSA", "INTC", "QCOM", "TXN",
        "AMGN", "INTU", "ISRG", "BKNG", "ADP", "GILD", "SBUX", "MDLZ", "PYPL", "AMAT",
        "LRCX", "ADI", "MU", "KLAC", "PANW", "CRWD", "SNPS", "CDNS", "ORLY", "MAR",
        "CTAS", "FTNT", "ADSK", "WDAY", "NXPI", "ABNB", "MELI", "MRVL", "ASML", "AZN",
        "REGN", "VRTX", "CSX", "MNST", "KDP", "AEP", "XEL", "ROST", "PAYX", "IDXX",
        "CPRT", "FAST", "PCAR", "EA", "CTSH", "TTWO", "EXC", "ODFL", "LULU", "BKR",
        "FANG", "KHC", "DXCM", "BIIB", "CHTR", "CCEP", "VRSK", "CEG", "GEHC", "TEAM",
        "ZS", "OKTA", "MDB", "DDOG", "DOCU", "SPLK", "ARM", "APP", "ON", "MCHP",
        "CSGP", "ANSS", "ILMN", "DLTR", "LCID", "RIVN", "JD", "PDD", "BIDU", "NTES",
    ],
    "midcap_liquid": [
        "VRT", "SMCI", "ANF", "DECK", "HUBB", "PWR", "EME", "WSO", "JBL", "FSLR",
        "CELH", "CAVA", "APPF", "DKNG", "TTD", "RBLX", "U", "ESTC", "NET", "FROG",
        "GTLB", "ZI", "SNAP", "PINS", "ROKU", "PSTG", "HPE", "ANET", "VEEV", "HIMS",
        "DUOL", "PATH", "AFRM", "SOFI", "HOOD", "MNDY", "PAYC", "GEN", "CHKP", "ZS",
        "ONON", "ELF", "ULTA", "KMX", "CVNA", "WING", "CMG", "RCL", "NCLH", "CCL",
    ],
    "ai_software": [
        "MSFT", "GOOGL", "META", "AMZN", "ORCL", "CRM", "NOW", "ADBE", "SNOW", "MDB",
        "DDOG", "CRWD", "PANW", "NET", "TEAM", "ZS", "OKTA", "ESTC", "PLTR", "SOUN",
        "AI", "PATH", "GTLB", "FROG", "HUBS", "INTU", "SAP", "SHOP", "U", "DOCN",
    ],
    "semiconductors": [
        "NVDA", "AMD", "AVGO", "QCOM", "TXN", "INTC", "AMAT", "LRCX", "KLAC", "MRVL",
        "MU", "MCHP", "NXPI", "ON", "ADI", "ASML", "TSM", "ARM", "SMCI", "TER",
        "MPWR", "SWKS", "QRVO", "WOLF", "ENTG", "LSCC", "COHR", "ACLS", "ALGM", "FORM",
    ],
    "data_center_infra": [
        "VRT", "SMCI", "ANET", "DELL", "HPE", "CSCO", "JCI", "ETN", "PH", "PWR",
        "EMR", "ROK", "HUBB", "NVT", "ABB", "SI", "MOD", "TT", "HWM", "GE",
    ],
    "utilities_power": [
        "NEE", "DUK", "SO", "AEP", "D", "EXC", "XEL", "SRE", "PEG", "ED",
        "EIX", "AWK", "WEC", "ES", "EQR", "AEE", "PNW", "CMS", "DTE", "CEG",
        "VST", "NRG", "PCG", "PPL", "EVRG", "FE", "LNT", "NI", "ATO", "BKH",
    ],
    "data_center_reits": ["EQIX", "DLR", "AMT", "CCI", "SBAC", "WELL", "PLD", "REXR", "FR", "CUBE"],
    "benchmarks": [
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV", "RSP", "XLF", "XLK",
        "XLE", "XLI", "XLV", "XLP", "XLY", "XLU", "XLRE", "SMH", "SOXX", "IGV",
    ],
}


METADATA: dict[str, dict[str, Any]] = {
    "SPY": {"company_name": "SPDR S&P 500 ETF Trust", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "QQQ": {"company_name": "Invesco QQQ Trust", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "IWM": {"company_name": "iShares Russell 2000 ETF", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "DIA": {"company_name": "SPDR Dow Jones Industrial Average ETF", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "VTI": {"company_name": "Vanguard Total Stock Market ETF", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "VOO": {"company_name": "Vanguard S&P 500 ETF", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "IVV": {"company_name": "iShares Core S&P 500 ETF", "sector": "ETF", "industry": "Index ETF", "is_etf": True, "benchmark_only": True},
    "SMH": {"company_name": "VanEck Semiconductor ETF", "sector": "ETF", "industry": "Sector ETF", "is_etf": True, "benchmark_only": False},
    "SOXX": {"company_name": "iShares Semiconductor ETF", "sector": "ETF", "industry": "Sector ETF", "is_etf": True, "benchmark_only": False},
    "IGV": {"company_name": "iShares Expanded Tech-Software Sector ETF", "sector": "ETF", "industry": "Sector ETF", "is_etf": True, "benchmark_only": False},
    "VRT": {"company_name": "Vertiv Holdings Co.", "sector": "Industrials", "industry": "Electrical Equipment", "is_etf": False, "benchmark_only": False},
    "NVDA": {"company_name": "NVIDIA Corporation", "sector": "Technology", "industry": "Semiconductors", "is_etf": False, "benchmark_only": False},
    "AVGO": {"company_name": "Broadcom Inc.", "sector": "Technology", "industry": "Semiconductors", "is_etf": False, "benchmark_only": False},
    "EQIX": {"company_name": "Equinix, Inc.", "sector": "Real Estate", "industry": "Data Center REIT", "is_etf": False, "benchmark_only": False},
    "DLR": {"company_name": "Digital Realty Trust", "sector": "Real Estate", "industry": "Data Center REIT", "is_etf": False, "benchmark_only": False},
    "CEG": {"company_name": "Constellation Energy", "sector": "Utilities", "industry": "Independent Power", "is_etf": False, "benchmark_only": False},
    "VST": {"company_name": "Vistra Corp.", "sector": "Utilities", "industry": "Independent Power", "is_etf": False, "benchmark_only": False},
}


@dataclass(frozen=True)
class UniverseSymbol:
    symbol: str
    company_name: str
    sector: str
    industry: str
    universe_groups: list[str]
    is_etf: bool
    benchmark_only: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "sector": self.sector,
            "industry": self.industry,
            "universe_groups": list(self.universe_groups),
            "is_etf": self.is_etf,
            "benchmark_only": self.benchmark_only,
        }


def _parse_csv_values(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        parts = [part.strip() for part in raw_value.split(",")]
        return [part for part in parts if part]
    if isinstance(raw_value, (list, tuple, set)):
        return [str(value).strip() for value in raw_value if str(value).strip()]
    return []


def normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper().replace(" ", "")
    text = text.replace("/", "-")
    if "." in text:
        text = text.replace(".", "-")
    return text


def is_supported_symbol_format(symbol: str) -> bool:
    return bool(SYMBOL_PATTERN.match(symbol))


def _is_valid_tradeable_symbol(symbol: str) -> bool:
    if not is_supported_symbol_format(symbol):
        return False
    if symbol.startswith("$"):
        return False
    if symbol.endswith("W") and len(symbol) > 5:
        return False
    return True


def _metadata_for_symbol(symbol: str, groups: list[str]) -> UniverseSymbol:
    info = dict(METADATA.get(symbol, {}))
    is_etf = bool(info.get("is_etf", symbol in GROUP_SYMBOLS.get("benchmarks", [])))
    benchmark_only = bool(info.get("benchmark_only", symbol in {"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV"}))
    return UniverseSymbol(
        symbol=symbol,
        company_name=str(info.get("company_name") or symbol),
        sector=str(info.get("sector") or "Unknown"),
        industry=str(info.get("industry") or "Unknown"),
        universe_groups=sorted(set(groups)),
        is_etf=is_etf,
        benchmark_only=benchmark_only,
    )


def _resolve_groups(selected_universes: list[str] | None = None) -> list[str]:
    raw = selected_universes if selected_universes is not None else _parse_csv_values(SCANNER_UNIVERSES)
    groups = [normalize_symbol(group).lower().replace("-", "_") for group in raw]
    if not groups:
        groups = list(DEFAULT_UNIVERSE_GROUPS)
    valid = [group for group in groups if group in GROUP_SYMBOLS]
    return sorted(set(valid))


def build_stock_universe(
    selected_universes: list[str] | None = None,
    include_etfs: bool | None = None,
    max_universe_size: int | None = None,
    excluded_symbols: list[str] | None = None,
    additional_symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    groups = _resolve_groups(selected_universes)
    include_etf_flag = SCANNER_INCLUDE_ETFS if include_etfs is None else bool(include_etfs)
    limit = int(max_universe_size or SCANNER_MAX_UNIVERSE_SIZE)
    excluded = {normalize_symbol(value) for value in _parse_csv_values(excluded_symbols or SCANNER_EXCLUDED_SYMBOLS)}
    additional = [normalize_symbol(value) for value in _parse_csv_values(additional_symbols or SCANNER_ADDITIONAL_SYMBOLS)]

    group_map: dict[str, list[str]] = {}
    for group in groups:
        for raw_symbol in GROUP_SYMBOLS.get(group, []):
            symbol = normalize_symbol(raw_symbol)
            if not _is_valid_tradeable_symbol(symbol):
                continue
            if symbol in excluded:
                continue
            if not include_etf_flag and symbol in METADATA and bool(METADATA[symbol].get("is_etf")):
                continue
            if symbol in LEVERAGED_OR_INVERSE_ETFS:
                continue
            group_map.setdefault(symbol, []).append(group)

    for symbol in additional:
        if not _is_valid_tradeable_symbol(symbol):
            continue
        if symbol in excluded:
            continue
        if symbol in LEVERAGED_OR_INVERSE_ETFS:
            continue
        group_map.setdefault(symbol, []).append("additional")

    results: list[UniverseSymbol] = []
    for symbol in sorted(group_map):
        universe_symbol = _metadata_for_symbol(symbol, group_map[symbol])
        if universe_symbol.is_etf and not include_etf_flag and "benchmarks" not in universe_symbol.universe_groups:
            continue
        results.append(universe_symbol)

    if limit > 0:
        results = results[:limit]
    return [item.as_dict() for item in results]


def load_stock_universe(**kwargs: Any) -> list[dict[str, Any]]:
    return build_stock_universe(**kwargs)
