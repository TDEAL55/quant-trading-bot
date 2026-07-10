import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

from logger_setup import logger
from market_data import download_price_data
from paper_order import create_paper_order_manager
from strategy import generate_signal


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _write_daily_summary(summary, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{summary['date']}.md"
    content = [
        f"# Daily Summary {summary['date']}",
        "",
        f"- date: {summary['date']}",
        f"- account status: {summary['account_status']}",
        f"- cash: {summary['cash']}",
        f"- buying power: {summary['buying_power']}",
        f"- portfolio value: {summary['portfolio_value']}",
        f"- positions: {summary['positions']}",
        f"- signal: {summary['signal']}",
        f"- decision: {summary['decision']}",
        f"- order submitted or skipped: {summary['order_submitted_or_skipped']}",
        f"- reason: {summary['reason']}",
        f"- daily P/L if available: {summary['daily_pl']}",
        f"- total P/L if available: {summary['total_pl']}",
        f"- errors if any: {summary['errors']}",
    ]
    file_path.write_text("\n".join(content) + "\n", encoding="utf-8")
    _emit_runner_log("DAILY_SUMMARY_CREATED", date=summary["date"], path=file_path)


def _write_final_report(report_path, summaries, review_required, stop_reason):
    submitted_count = sum(1 for item in summaries if item["order_submitted_or_skipped"] == "submitted")
    skipped_count = sum(1 for item in summaries if item["order_submitted_or_skipped"] != "submitted")
    final_total_pl = summaries[-1]["total_pl"] if summaries else "N/A"
    content = [
        "# TWO_WEEK_REPORT",
        "",
        f"- days processed: {len(summaries)}",
        f"- orders submitted: {submitted_count}",
        f"- orders skipped: {skipped_count}",
        f"- review required: {review_required}",
        f"- stop reason: {stop_reason}",
        f"- final total P/L: {final_total_pl}",
    ]
    report_path.write_text("\n".join(content) + "\n", encoding="utf-8")


def _emit_runner_log(event, **fields):
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


def _safe_error_message(exc):
    text = str(exc)
    if not text:
        return ""

    # Remove common secret-bearing key/value fragments.
    patterns = [
        r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(api[_-]?secret\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(authorization\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(account(?:[_-]?(?:id|number))?\s*[=:]\s*)([^\s,;]+)",
    ]
    safe = text
    for pattern in patterns:
        safe = re.sub(pattern, r"\1[REDACTED]", safe)

    # Mask standalone long numeric identifiers.
    safe = re.sub(r"\b\d{8,}\b", "[REDACTED]", safe)

    return safe[:200]


def _emit_paper_run_error(stage, exc):
    _emit_runner_log(
        "PAPER_RUN_ERROR",
        PAPER_RUN_STAGE=stage,
        PAPER_RUN_ERROR_TYPE=type(exc).__name__,
        PAPER_RUN_ERROR_MESSAGE=_safe_error_message(exc),
    )


def run_two_week_paper_runner(
    start_day=None,
    env_path=None,
    load_env_file=True,
    days=14,
    output_dir=None,
    report_path=None,
    trading_client_factory=TradingClient,
    market_data_loader=download_price_data,
    signal_generator=generate_signal,
    order_manager_factory=create_paper_order_manager,
    dry_run=True,
    submit_enabled=False,
):
    """Run a 14-calendar-day paper-trading dry-run with strict safety rules."""
    if days < 1:
        raise ValueError("days must be at least 1")

    if load_env_file:
        dotenv_path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=False)

    mode = os.getenv("TRADING_MODE", "SIMULATION").upper()
    if mode == "LIVE":
        raise RuntimeError("LIVE mode detected; stopping immediately")
    if mode != "PAPER":
        raise RuntimeError("two_week_paper_runner requires TRADING_MODE=PAPER")

    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("Missing required Alpaca credentials")

    client = trading_client_factory(api_key=api_key, secret_key=api_secret, paper=True)
    summaries_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parent / "daily_summaries"
    final_report = Path(report_path) if report_path else Path(__file__).resolve().parent / "TWO_WEEK_REPORT.md"

    if submit_enabled:
        _emit_runner_log("PAPER_ORDER_SUBMISSION_ENABLED")

    start = start_day or date.today()
    end = start + timedelta(days=days - 1)

    # Fetch enough history to generate moving-average signals without auto-optimization.
    history_start = (start - timedelta(days=120)).isoformat()
    history_end = (end + timedelta(days=1)).isoformat()
    prices = market_data_loader("SPY", history_start, history_end)

    summaries = []
    review_required = False
    stop_reason = "completed"
    previous_portfolio_value = None
    baseline_portfolio_value = None
    submitted_order_days = set()

    for day_offset in range(days):
        current_day = start + timedelta(days=day_offset)
        paper_run_stage = "day_started"
        summary = {
            "date": current_day.isoformat(),
            "account_status": "unavailable",
            "cash": "N/A",
            "buying_power": "N/A",
            "portfolio_value": "N/A",
            "positions": "N/A",
            "signal": "N/A",
            "decision": "skip",
            "order_submitted_or_skipped": "skipped",
            "reason": "not evaluated",
            "daily_pl": "N/A",
            "total_pl": "N/A",
            "errors": "",
        }

        try:
            if mode == "LIVE":
                raise RuntimeError("LIVE mode detected; stopping immediately")

            paper_run_stage = "account_fetch"
            account = client.get_account()
            paper_run_stage = "clock_fetch"
            clock = client.get_clock()
            paper_run_stage = "positions_fetch"
            positions = client.get_all_positions()

            account_status = str(getattr(account, "status", "unknown"))
            cash = _safe_float(getattr(account, "cash", None))
            buying_power = _safe_float(getattr(account, "buying_power", None))
            portfolio_value = _safe_float(getattr(account, "portfolio_value", None))
            market_open = bool(getattr(clock, "is_open", False))

            summary["account_status"] = account_status
            summary["cash"] = cash if cash is not None else "N/A"
            summary["buying_power"] = buying_power if buying_power is not None else "N/A"
            summary["portfolio_value"] = portfolio_value if portfolio_value is not None else "N/A"
            summary["positions"] = [
                {
                    "symbol": str(getattr(position, "symbol", "")),
                    "qty": str(getattr(position, "qty", "0")),
                }
                for position in positions
            ]

            if portfolio_value is not None:
                if baseline_portfolio_value is None:
                    baseline_portfolio_value = portfolio_value
                    summary["daily_pl"] = 0.0
                    summary["total_pl"] = 0.0
                else:
                    daily_pl = portfolio_value - (previous_portfolio_value if previous_portfolio_value is not None else portfolio_value)
                    total_pl = portfolio_value - baseline_portfolio_value
                    summary["daily_pl"] = round(daily_pl, 4)
                    summary["total_pl"] = round(total_pl, 4)

                previous_portfolio_value = portfolio_value

            # Loss controls.
            if isinstance(summary["daily_pl"], float) and summary["daily_pl"] <= -25.0:
                review_required = True
                summary["decision"] = "stop"
                summary["reason"] = "daily loss limit hit"
                summary["errors"] = "REVIEW_REQUIRED"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                stop_reason = "daily loss limit hit"
                break
            if isinstance(summary["total_pl"], float) and summary["total_pl"] <= -100.0:
                review_required = True
                summary["decision"] = "stop"
                summary["reason"] = "total loss limit hit"
                summary["errors"] = "REVIEW_REQUIRED"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                stop_reason = "total loss limit hit"
                break

            if not market_open:
                summary["decision"] = "skip"
                summary["reason"] = "market closed"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                logger.info("two_week_paper_runner day=%s skipped reason=market closed", summary["date"])
                continue

            if not hasattr(prices.index, "date"):
                summary["decision"] = "skip"
                summary["reason"] = "data missing"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                logger.info("two_week_paper_runner day=%s skipped reason=data missing", summary["date"])
                continue

            day_prices = prices[prices.index.date <= current_day]
            if day_prices.empty or "close" not in day_prices.columns:
                summary["decision"] = "skip"
                summary["reason"] = "data missing"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                logger.info("two_week_paper_runner day=%s skipped reason=data missing", summary["date"])
                continue

            paper_run_stage = "signal_generation"
            signal = signal_generator(day_prices["close"], 20, 50)
            summary["signal"] = signal

            if signal != "buy":
                summary["decision"] = "skip"
                summary["reason"] = "signal not buy"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                logger.info("two_week_paper_runner day=%s skipped reason=signal not buy", summary["date"])
                continue

            if current_day in submitted_order_days:
                summary["decision"] = "skip"
                summary["reason"] = "duplicate order detected"
                summaries.append(summary)
                _write_daily_summary(summary, summaries_dir)
                logger.info("two_week_paper_runner day=%s skipped reason=duplicate order detected", summary["date"])
                continue

            paper_run_stage = "order_manager_init"
            manager = order_manager_factory(
                mode="PAPER",
                dry_run=dry_run,
                submit_enabled=submit_enabled,
                trading_client=client,
            )
            if submit_enabled:
                _emit_runner_log("PAPER_ORDER_SUBMIT_STARTED", date=summary["date"], symbol="SPY", notional=10.0)
            try:
                paper_run_stage = "order_submit"
                order_result = manager.place_order(command="BUY $10 of SPY", order_type="market")
            except Exception:
                order_result = {
                    "approved": False,
                    "reason": "submission failed",
                    "submitted": False,
                    "status": "error",
                }

            if submit_enabled:
                submitted = bool(order_result.get("submitted"))
                result_fields = {
                    "submitted": submitted,
                    "status": order_result.get("status", order_result.get("reason", "unknown")),
                }
                if submitted:
                    result_fields["order_id"] = order_result.get("order_id", "N/A")
                _emit_runner_log("PAPER_ORDER_SUBMIT_RESULT", **result_fields)

            summary["decision"] = "buy" if order_result.get("approved") else "skip"
            summary["order_submitted_or_skipped"] = "submitted" if order_result.get("approved") else "skipped"
            summary["reason"] = order_result.get("reason", "unknown")
            if summary["reason"] == "duplicate order rejected":
                summary["reason"] = "duplicate order detected"

            if order_result.get("approved"):
                submitted_order_days.add(current_day)

            summaries.append(summary)
            _write_daily_summary(summary, summaries_dir)
            logger.info(
                "two_week_paper_runner day=%s signal=%s decision=%s reason=%s submitted=%s",
                summary["date"],
                summary["signal"],
                summary["decision"],
                summary["reason"],
                summary["order_submitted_or_skipped"],
            )

        except Exception as exc:
            _emit_paper_run_error(paper_run_stage, exc)
            summary["decision"] = "skip"
            summary["order_submitted_or_skipped"] = "skipped"
            summary["reason"] = "error"
            summary["errors"] = f"{type(exc).__name__}: {_safe_error_message(exc)}"
            summaries.append(summary)
            _write_daily_summary(summary, summaries_dir)
            logger.error(
                "two_week_paper_runner day=%s failed stage=%s type=%s",
                summary["date"],
                paper_run_stage,
                type(exc).__name__,
            )

    if review_required:
        os.environ["REVIEW_REQUIRED"] = "true"

    _write_final_report(final_report, summaries, review_required, stop_reason)
    return {
        "review_required": review_required,
        "stop_reason": stop_reason,
        "days_processed": len(summaries),
        "report_path": str(final_report),
    }


def main():
    run_two_week_paper_runner()


if __name__ == "__main__":
    main()