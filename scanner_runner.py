from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import BENCHMARK_SYMBOL
from market_data import download_price_data
from market_scanner import scan_universe
from portfolio_selector import build_portfolio_shortlist, review_existing_positions
from research_journal import journal_scanner_run
from scanner_repository import save_scan_results
from stock_universe import load_stock_universe
from strategy import generate_strategy_result


STARTUP_BANNER = "RESEARCH SCANNER ONLY - NO ORDERS WILL BE SUBMITTED"

SAMPLE_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "AVGO",
    "VRT", "CEG", "VST", "DLR", "EQIX", "JPM", "XOM", "COST", "LLY", "UNH",
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_window() -> tuple[str, str]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=1000)
    return start_date.isoformat(), end_date.isoformat()


def _load_paper_positions() -> tuple[list[dict[str, Any]], float, float]:
    # This runner intentionally uses environment-provided snapshots only.
    symbols_raw = os.getenv("PAPER_POSITIONS_SYMBOLS", "")
    symbols = [item.strip().upper() for item in symbols_raw.split(",") if item.strip()]
    positions = []
    for symbol in symbols:
        positions.append(
            {
                "symbol": symbol,
                "quantity": float(os.getenv(f"PAPER_POSITION_{symbol}_QTY", "0") or 0),
                "entry_price": float(os.getenv(f"PAPER_POSITION_{symbol}_ENTRY", "0") or 0),
                "market_price": float(os.getenv(f"PAPER_POSITION_{symbol}_MARKET", "0") or 0),
                "holding_days": int(os.getenv(f"PAPER_POSITION_{symbol}_DAYS", "0") or 0),
            }
        )
    current_cash = float(os.getenv("PAPER_CASH", "0") or 0)
    portfolio_value = float(os.getenv("PAPER_PORTFOLIO_VALUE", str(current_cash)) or current_cash)
    return positions, current_cash, portfolio_value


def _symbol_records_from_list(symbols: list[str]) -> list[dict[str, Any]]:
    return [{"symbol": symbol, "company_name": symbol, "sector": "Unknown", "industry": "Unknown"} for symbol in symbols]


def _position_score_map(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    start_date, end_date = _history_window()
    benchmark = download_price_data(BENCHMARK_SYMBOL, start_date, end_date)
    scored: dict[str, dict[str, Any]] = {}
    for position in positions:
        symbol = str(position.get("symbol", "")).upper()
        if not symbol:
            continue
        try:
            history = download_price_data(symbol, start_date, end_date)
            scored[symbol] = generate_strategy_result(
                prices=history,
                strategy_mode="MULTI_FACTOR",
                symbol=symbol,
                benchmark_prices=benchmark,
            )
        except Exception as exc:
            scored[symbol] = {
                "symbol": symbol,
                "overall_score": 0.0,
                "confidence": 0.0,
                "signal": "HOLD",
                "regime": "unknown",
                "component_scores": {},
                "data_quality": {"history_sufficient": False},
                "warnings": [f"position review scoring failed: {type(exc).__name__}: {exc}"],
            }
    return scored


def run_scan(symbol_records: list[dict[str, Any]]) -> dict[str, Any]:
    return scan_universe(symbol_records=symbol_records, benchmark_symbol=BENCHMARK_SYMBOL)


def run_shortlist_only(scan_payload: dict[str, Any], positions: list[dict[str, Any]], cash: float, portfolio_value: float) -> dict[str, Any]:
    return build_portfolio_shortlist(
        ranked_candidates=scan_payload.get("ranked_candidates", []),
        current_positions=positions,
        pending_order_symbols=[item.strip().upper() for item in os.getenv("PAPER_PENDING_SYMBOLS", "").split(",") if item.strip()],
        cooldown_symbols=[item.strip().upper() for item in os.getenv("PAPER_COOLDOWN_SYMBOLS", "").split(",") if item.strip()],
        current_cash=cash,
        portfolio_value=portfolio_value,
        risk_state={
            "daily_loss_stop_active": os.getenv("PAPER_DAILY_LOSS_STOP_ACTIVE", "false").lower() == "true",
            "portfolio_loss_stop_active": os.getenv("PAPER_PORTFOLIO_LOSS_STOP_ACTIVE", "false").lower() == "true",
        },
    )


def run_position_review_only(positions: list[dict[str, Any]]) -> dict[str, Any]:
    score_map = _position_score_map(positions)
    return review_existing_positions(
        held_positions=positions,
        score_results_by_symbol=score_map,
        atr_trailing_stop_hits={item["symbol"]: False for item in positions if item.get("symbol")},
    )


def print_terminal_summary(
    universe_count: int,
    scan_payload: dict[str, Any],
    shortlist_payload: dict[str, Any],
    position_payload: dict[str, Any],
):
    summary = scan_payload.get("summary", {})
    print("\nSCAN COMPLETE\n")
    print(f"Universe: {universe_count}")
    print(f"Successfully scored: {summary.get('success_count', 0)}")
    print(f"Rejected for data/liquidity: {summary.get('rejection_count', 0)}")
    print(f"Errors: {summary.get('error_count', 0)}")
    print(f"Eligible BUY candidates: {summary.get('eligible_count', 0)}")

    print("\nTOP CANDIDATES\n")
    print("Rank  Symbol  Score  Confidence  Sector          Signal")
    for item in scan_payload.get("ranked_candidates", [])[:10]:
        print(
            f"{int(item.get('rank', 0)):>4}  {item.get('symbol', ''):<6}  {float(item.get('overall_score', 0.0)):>5.1f}"
            f"  {float(item.get('confidence', 0.0)):>10.1f}  {str(item.get('sector', 'Unknown'))[:14]:<14}  {item.get('signal', 'HOLD')}"
        )

    print("\nPORTFOLIO SHORTLIST\n")
    print(f"Selected: {len(shortlist_payload.get('selected', []))}")
    rejected = shortlist_payload.get("rejected", [])
    sector_limit = len([item for item in rejected if "sector" in str(item.get("reason", "")).lower()])
    duplicates = len([item for item in rejected if "current positions" in str(item.get("reason", "")).lower()])
    risk_exclusions = len([item for item in rejected if "risk" in str(item.get("reason", "")).lower()])
    print(f"Sector limit exclusions: {sector_limit}")
    print(f"Position duplicates: {duplicates}")
    print(f"Risk exclusions: {risk_exclusions}")

    print("\nPOSITION REVIEW\n")
    counts = ((position_payload.get("summary") or {}).get("counts") or {})
    print(
        f"HOLD={counts.get('HOLD', 0)} WATCH={counts.get('WATCH', 0)} "
        f"REDUCE={counts.get('REDUCE', 0)} EXIT={counts.get('EXIT', 0)}"
    )

    print("\nNo orders submitted.")
    print("LIVE trading remains blocked.")


def main():
    parser = argparse.ArgumentParser(description="Research scanner and candidate selector runner")
    parser.add_argument("--mode", choices=["scan", "shortlist", "positions", "full"], default="full")
    parser.add_argument("--sample", action="store_true", help="Use controlled sample symbols")
    parser.add_argument("--persist", action="store_true", help="Persist scan output to DB or JSON fallback")
    args = parser.parse_args()

    print(STARTUP_BANNER)
    started = time.perf_counter()
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    scanner_run_id = f"scan-{run_stamp}"
    research_run_id = f"research-{run_stamp}"

    symbols = SAMPLE_SYMBOLS if args.sample else []
    if not symbols:
        universe_records = load_stock_universe()
    else:
        universe_records = _symbol_records_from_list(symbols)

    positions, current_cash, portfolio_value = _load_paper_positions()

    scan_payload = {"scan_results": [], "ranked_candidates": [], "summary": {}}
    shortlist_payload = {"selected": [], "rejected": [], "portfolio_warnings": [], "selection_summary": {}}
    position_payload = {"reviews": [], "summary": {"counts": {"HOLD": 0, "WATCH": 0, "REDUCE": 0, "EXIT": 0}}}

    if args.mode in {"scan", "shortlist", "full"}:
        scan_payload = run_scan(universe_records)

    if args.mode in {"shortlist", "full"}:
        shortlist_payload = run_shortlist_only(scan_payload, positions, current_cash, portfolio_value)

    if args.mode in {"positions", "full"}:
        position_payload = run_position_review_only(positions)

    if args.persist and args.mode in {"scan", "shortlist", "full"}:
        summary = dict(scan_payload.get("summary") or {})
        run_payload = {
            "run_id": scanner_run_id,
            "started_at": _utc_iso(),
            "completed_at": _utc_iso(),
            "universe_name": "sample" if args.sample else "configured",
            "symbol_count": int(summary.get("symbol_count", len(universe_records))),
            "success_count": int(summary.get("success_count", 0)),
            "rejection_count": int(summary.get("rejection_count", 0)),
            "error_count": int(summary.get("error_count", 0)),
            "eligible_count": int(summary.get("eligible_count", 0)),
            "status": "completed",
            "duration_seconds": float(summary.get("duration_seconds", 0.0)),
        }
        try:
            persist_result = save_scan_results(
                run_payload=run_payload,
                scan_results=scan_payload.get("scan_results", []),
                candidates=shortlist_payload.get("selected", []),
                position_reviews=position_payload.get("reviews", []),
                database_url=os.getenv("DATABASE_URL"),
            )
            print(f"Persistence: {persist_result}")
        except Exception as exc:
            print(f"SCANNER_PERSISTENCE_FAILED type={type(exc).__name__} message={exc}")

    if args.mode in {"scan", "shortlist", "full"}:
        try:
            research_result = journal_scanner_run(
                scanner_payload=scan_payload,
                research_run_id=research_run_id,
                database_url=os.getenv("DATABASE_URL"),
                data_source="synthetic" if args.sample else "live",
                data_mode="research",
            )
            print(f"Research journal: {research_result.get('research_run_id')} stored={research_result.get('status')}")
        except Exception as exc:
            print(f"RESEARCH_PERSISTENCE_FAILED type={type(exc).__name__} message={exc}")

    if args.mode == "scan":
        print_terminal_summary(len(universe_records), scan_payload, shortlist_payload, position_payload)
    elif args.mode == "shortlist":
        print_terminal_summary(len(universe_records), scan_payload, shortlist_payload, position_payload)
    elif args.mode == "positions":
        print("POSITION REVIEW ONLY")
        counts = (position_payload.get("summary") or {}).get("counts") or {}
        print(f"HOLD={counts.get('HOLD', 0)} WATCH={counts.get('WATCH', 0)} REDUCE={counts.get('REDUCE', 0)} EXIT={counts.get('EXIT', 0)}")
    else:
        print_terminal_summary(len(universe_records), scan_payload, shortlist_payload, position_payload)

    elapsed = time.perf_counter() - started
    print(f"\nScan duration seconds: {elapsed:.2f}")


if __name__ == "__main__":
    main()
