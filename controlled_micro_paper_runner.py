from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from typing import Any, Mapping

from alpaca_micro_paper_broker import AlpacaMicroPaperBroker, PAPER_MICRO_CONFIRMATION_PHRASE
from controlled_live_runner import run_controlled_live_cycle
from live_risk_policy import LIVE_CONFIRMATION_PHRASE, LiveRiskSettings


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def paper_micro_settings(environ: Mapping[str, str] | None = None) -> LiveRiskSettings:
    env = dict(os.environ if environ is None else environ)
    symbols = tuple(
        dict.fromkeys(
            item.strip().upper()
            for item in str(env.get("PAPER_MICRO_ALLOWED_SYMBOLS", "")).split(",")
            if item.strip()
        )
    )
    confirmation_valid = (
        str(env.get("PAPER_MICRO_TRIAL_CONFIRMATION", "")).strip() == PAPER_MICRO_CONFIRMATION_PHRASE
    )
    settings = LiveRiskSettings(
        enabled=_is_true(env.get("PAPER_MICRO_TRIAL_ENABLED", "false")),
        order_submission_enabled=_is_true(env.get("ALPACA_ORDER_SUBMISSION_ENABLED", "false")),
        kill_switch=_is_true(env.get("PAPER_MICRO_KILL_SWITCH", "true")),
        confirmation=LIVE_CONFIRMATION_PHRASE if confirmation_valid else "",
        private_dashboard_confirmed=True,
        maximum_account_equity=float(env.get("PAPER_MICRO_MAX_ACCOUNT_EQUITY", "500")),
        maximum_position_percent=float(env.get("PAPER_MICRO_MAX_POSITION_PERCENT", "10")),
        maximum_position_notional=float(env.get("PAPER_MICRO_MAX_POSITION_NOTIONAL", "30")),
        maximum_gross_exposure_percent=float(env.get("PAPER_MICRO_MAX_GROSS_EXPOSURE_PERCENT", "30")),
        maximum_open_positions=int(env.get("PAPER_MICRO_MAX_OPEN_POSITIONS", "3")),
        maximum_new_orders_per_day=int(env.get("PAPER_MICRO_MAX_NEW_ORDERS_PER_DAY", "1")),
        daily_loss_stop_percent=float(env.get("PAPER_MICRO_DAILY_LOSS_STOP_PERCENT", "1")),
        daily_loss_stop_dollars=float(env.get("PAPER_MICRO_DAILY_LOSS_STOP_DOLLARS", "3")),
        minimum_cash_reserve_percent=float(env.get("PAPER_MICRO_MINIMUM_CASH_RESERVE_PERCENT", "70")),
        minimum_strategy_score=float(env.get("PAPER_MICRO_MINIMUM_STRATEGY_SCORE", "75")),
        minimum_confidence=float(env.get("PAPER_MICRO_MINIMUM_CONFIDENCE", "70")),
        stop_loss_percent=float(env.get("PAPER_MICRO_STOP_LOSS_PERCENT", "5")),
        take_profit_percent=float(env.get("PAPER_MICRO_TAKE_PROFIT_PERCENT", "10")),
        allowed_symbols=symbols,
    )
    settings.validate()
    return settings


def run_paper_micro_cycle(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    if str(env.get("TRADING_MODE", "")).strip().upper() != "PAPER":
        return {"status": "blocked", "reasons": ["TRADING_MODE_must_be_PAPER"], "submitted": False}
    settings = paper_micro_settings(env)
    if settings.kill_switch:
        return {"status": "blocked", "reasons": ["paper_micro_kill_switch_active"], "submitted": False}
    broker = AlpacaMicroPaperBroker(environ=env)
    internal_env = dict(env)
    internal_env["TRADING_MODE"] = "LIVE"
    internal_env["LIVE_STATE_PATH"] = env.get(
        "PAPER_MICRO_STATE_PATH", "/var/lib/quant-bot/paper-micro-state.json"
    )
    return run_controlled_live_cycle(environ=internal_env, settings=settings, broker=broker)


def check_paper_account(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    broker = AlpacaMicroPaperBroker(environ=env, read_only=True)
    account = dict(broker.get_account() or {})
    account.pop("account_number", None)
    positions = dict(broker.get_positions() or {})
    orders = list(broker.get_open_orders() or [])
    clock = dict(broker.get_market_clock() or {})
    equity = float(account.get("equity") or 0.0)
    return {
        "status": "checked",
        "submission_enabled": False,
        "account": account,
        "starting_equity_matches_300": 299.0 <= equity <= 301.0,
        "position_symbols": sorted(positions),
        "open_order_count": len(orders),
        "market_is_open": bool(clock.get("is_open")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="$300 paper mirror for the controlled live runner")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check-account", action="store_true")
    parser.add_argument("--show-policy", action="store_true")
    args = parser.parse_args()
    if args.check_account:
        print(json.dumps(check_paper_account(), indent=2, sort_keys=True, default=str))
    elif args.show_policy:
        print(json.dumps(asdict(paper_micro_settings()), indent=2, sort_keys=True, default=str))
    elif args.once:
        print(json.dumps(run_paper_micro_cycle(), indent=2, sort_keys=True, default=str))
    else:
        interval = max(int(os.getenv("PAPER_MICRO_SCAN_INTERVAL_SECONDS", "60")), 60)
        while True:
            result = run_paper_micro_cycle()
            print(json.dumps({"event": "controlled_paper_micro_cycle", **result}, default=str), flush=True)
            import time
            time.sleep(interval)


if __name__ == "__main__":
    main()
