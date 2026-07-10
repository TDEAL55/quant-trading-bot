import json
import math
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from overnight_cost_sensitivity import (
    DEFAULT_COST_SCENARIOS_BPS,
    assert_cost_monotonicity,
    break_even_round_trip_cost_bps,
    evaluate_cost_scenarios,
)
from overnight_hold_strategy import OvernightConfig, run_overnight_hold_backtest


REPORT_PATH = Path(__file__).resolve().parent / "OVERNIGHT_COST_SENSITIVITY_2023.md"
STATE_DIR = Path(__file__).resolve().parent / ".overnight_cache" / "cost_sensitivity_2023"
STATE_PATH = STATE_DIR / "progress_2023.json"
MONTHLY_CACHE_ROOT = STATE_DIR / "alpaca_1m"
TARGET_YEAR = 2023
MONTH_KEYS = [f"{TARGET_YEAR}-{month:02d}" for month in range(1, 13)]


def _write_atomic(path, content):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise


def _write_json_atomic(path, payload):
    _write_atomic(path, json.dumps(payload, indent=2, sort_keys=True))


def _month_bounds(year, month):
    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    month_end = next_month.fromordinal(next_month.toordinal() - 1)
    extended_end = next_month.fromordinal(next_month.toordinal() + 7)
    return start.isoformat(), month_end.isoformat(), extended_end.isoformat()


def _load_state():
    if not STATE_PATH.exists():
        return {
            "year": TARGET_YEAR,
            "status": "in_progress",
            "completed_months": {},
            "gross_returns": [],
            "timing": {
                "entry": "15:58 America/New_York",
                "exit": "09:32 America/New_York next trading day",
            },
        }

    payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if payload.get("year") != TARGET_YEAR:
        raise RuntimeError("State file year mismatch")
    return payload


def _is_fixed_trade_timing(trade):
    entry = trade["entry_date_time"]
    exit_ts = trade["exit_date_time"]
    return ("T15:58:00" in entry) and ("T09:32:00" in exit_ts)


def _run_one_month(month_key):
    year, month = month_key.split("-")
    year = int(year)
    month = int(month)
    month_start, month_end, extended_end = _month_bounds(year, month)

    monthly_cache_dir = MONTHLY_CACHE_ROOT / month_key
    config = OvernightConfig(
        symbol="SPY",
        entry_minutes_before_close=2,
        exit_minutes_after_open=2,
        slippage_rate=0.0,
        transaction_cost_rate=0.0,
        intraday_feed="sip",
        allow_iex=False,
        cache_dir=str(monthly_cache_dir),
        chunk_days=7,
    )

    result = run_overnight_hold_backtest(
        start_date=month_start,
        end_date=extended_end,
        config=config,
        strict_data=True,
        mode="SIMULATION",
    )

    month_trades = []
    skipped_nonfixed_timing = 0
    for trade in result.get("trades", []):
        if not _is_fixed_trade_timing(trade):
            skipped_nonfixed_timing += 1
            continue
        if not trade["entry_date_time"].startswith(month_key):
            continue

        gross_return = float(trade["gross_return"])
        if not math.isfinite(gross_return):
            raise RuntimeError(f"Non-finite gross return detected for {month_key}")

        month_trades.append({
            "entry_date_time": trade["entry_date_time"],
            "exit_date_time": trade["exit_date_time"],
            "gross_return": gross_return,
            "entry_price": float(trade["entry_price"]),
            "exit_price": float(trade["exit_price"]),
        })

    if not month_trades:
        raise RuntimeError(f"No complete overnight trades found for {month_key}")

    return {
        "month": month_key,
        "period_requested": {
            "start": month_start,
            "end": month_end,
            "extended_end": extended_end,
        },
        "cache_dir": str(monthly_cache_dir),
        "feed_used": result["data_source"]["feed_used"],
        "earliest_bar": result["earliest_bar"],
        "latest_bar": result["latest_bar"],
        "trade_count": len(month_trades),
        "skipped_nonfixed_timing": skipped_nonfixed_timing,
        "trades": month_trades,
    }


def _report_markdown(analysis, break_even_bps, regulatory_fee_bps, state):
    lines = [
        "# OVERNIGHT_COST_SENSITIVITY_2023",
        "",
        "Research/backtest-only SPY overnight cost sensitivity analysis.",
        "",
        "## Scope",
        "",
        "- Strategy timing fixed: Buy 3:58 PM ET, sell 9:32 AM ET next trading day.",
        "- Symbol: SPY only.",
        "- Data: Alpaca SIP minute bars.",
        "- Execution: no order submission, no Railway integration, LIVE mode blocked.",
        "- Calendar year: 2023.",
        "- Processing model: month-by-month with resumable progress and per-month cache directories.",
        "",
        "## Cost Model",
        "",
        "- No Alpaca stock commissions added by default.",
        f"- Regulatory fees modeled separately: {regulatory_fee_bps:.4f} bps round trip.",
        "- Remaining scenario cost is modeled as slippage.",
        "",
        "## Gross Metrics (No Modeled Costs)",
        "",
        f"- trade count: {analysis['gross']['trade_count']}",
        f"- gross compounded return: {analysis['gross']['compounded_return']:.6%}",
        f"- average gross trade: {analysis['gross']['average_trade']:.6%}",
        f"- win rate: {analysis['gross']['win_rate']:.4%}",
        f"- maximum drawdown: {analysis['gross']['maximum_drawdown']:.6%}",
        f"- Sharpe ratio: {analysis['gross']['sharpe_ratio']:.6f}",
        f"- break-even round-trip cost: {break_even_bps:.6f} bps",
        "",
        "## Net Metrics by Cost Scenario",
        "",
        "| Total Round-Trip Cost (bps) | Slippage (bps) | Regulatory Fees (bps) | Net Compounded Return | Average Net Trade | Win Rate | Maximum Drawdown | Sharpe Ratio |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for item in analysis["net_scenarios"]:
        lines.append(
            "| "
            f"{item['total_round_trip_bps']:.4f} | "
            f"{item['slippage_bps']:.4f} | "
            f"{item['regulatory_fee_bps']:.4f} | "
            f"{item['compounded_return']:.6%} | "
            f"{item['average_trade']:.6%} | "
            f"{item['win_rate']:.4%} | "
            f"{item['maximum_drawdown']:.6%} | "
            f"{item['sharpe_ratio']:.6f} |"
        )

    lines.extend(["", "## Monthly Completion", ""])
    for month_key in MONTH_KEYS:
        item = state["completed_months"][month_key]
        lines.append(
            f"- {month_key}: trades={item['trade_count']} skipped_nonfixed_timing={item['skipped_nonfixed_timing']} feed={item['feed_used']} "
            f"cache={item['cache_dir']}"
        )

    lines.append("")
    return "\n".join(lines)


def _compute_month_compounded(month_trades):
    compounded = 1.0
    for trade in month_trades:
        compounded *= 1.0 + float(trade["gross_return"])
    return float(compounded - 1.0)


def main():
    dotenv_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    regulatory_fee_bps = float(os.getenv("OVERNIGHT_REGULATORY_FEE_BPS", "0"))
    if regulatory_fee_bps < 0:
        raise RuntimeError("OVERNIGHT_REGULATORY_FEE_BPS must be non-negative")

    state = _load_state()

    for month_key in MONTH_KEYS:
        if month_key in state["completed_months"]:
            continue

        result = _run_one_month(month_key)
        result["gross_compounded_return"] = _compute_month_compounded(result["trades"])
        state["completed_months"][month_key] = {
            "trade_count": result["trade_count"],
            "skipped_nonfixed_timing": result["skipped_nonfixed_timing"],
            "gross_compounded_return": result["gross_compounded_return"],
            "feed_used": result["feed_used"],
            "cache_dir": result["cache_dir"],
            "period_requested": result["period_requested"],
            "earliest_bar": result["earliest_bar"],
            "latest_bar": result["latest_bar"],
        }
        state["gross_returns"].extend([item["gross_return"] for item in result["trades"]])
        state["status"] = "in_progress"
        _write_json_atomic(STATE_PATH, state)
        print(f"completed month {month_key}: {result['trade_count']} trades")

    missing_months = [month for month in MONTH_KEYS if month not in state["completed_months"]]
    if missing_months:
        print("run incomplete; report not created")
        print("missing months: " + ", ".join(missing_months))
        return

    if not state["gross_returns"]:
        raise RuntimeError("No gross returns collected for 2023")

    analysis = evaluate_cost_scenarios(
        state["gross_returns"],
        scenarios_bps=DEFAULT_COST_SCENARIOS_BPS,
        regulatory_fee_bps=regulatory_fee_bps,
    )
    assert_cost_monotonicity(
        state["gross_returns"],
        scenarios_bps=DEFAULT_COST_SCENARIOS_BPS,
        regulatory_fee_bps=regulatory_fee_bps,
    )
    break_even_bps = break_even_round_trip_cost_bps(state["gross_returns"])

    markdown = _report_markdown(analysis, break_even_bps, regulatory_fee_bps, state)
    _write_atomic(REPORT_PATH, markdown)

    state["status"] = "completed"
    state["report_path"] = str(REPORT_PATH)
    state["report_generated"] = True
    state["break_even_round_trip_cost_bps"] = break_even_bps
    state["cost_scenarios_bps"] = DEFAULT_COST_SCENARIOS_BPS
    _write_json_atomic(STATE_PATH, state)

    print("completed 2023 cost-sensitivity run")
    print(f"report: {REPORT_PATH}")
    print(f"break-even round-trip cost: {break_even_bps:.6f} bps")
    for item in analysis["net_scenarios"]:
        print(
            f"cost={item['total_round_trip_bps']:.2f}bps "
            f"net_compounded={item['compounded_return']:.6%} "
            f"avg_net={item['average_trade']:.6%} "
            f"win_rate={item['win_rate']:.4%} "
            f"max_dd={item['maximum_drawdown']:.6%} "
            f"sharpe={item['sharpe_ratio']:.6f}"
        )


if __name__ == "__main__":
    main()
