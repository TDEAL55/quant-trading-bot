import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from logger_setup import logger
from two_week_paper_runner import run_two_week_paper_runner


EASTERN_TZ = ZoneInfo("America/New_York")


def _emit_railway_log(event, **fields):
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


def _safe_error_text(exc):
    return type(exc).__name__


def _today_eastern(now=None):
    current = now or datetime.now(EASTERN_TZ)
    return current.astimezone(EASTERN_TZ).date().isoformat()


def _read_last_run(marker_path):
    if not marker_path.exists():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        return data.get("market_date")
    except Exception:
        return None


def _write_last_run(marker_path, market_date):
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"market_date": market_date}, indent=2) + "\n", encoding="utf-8")


def run_railway_job(now=None):
    """Run a single safe paper-trading cycle for Railway scheduled execution."""
    mode = os.getenv("TRADING_MODE", "SIMULATION").upper()
    _emit_railway_log("RAILWAY_JOB_STARTED")
    _emit_railway_log("TRADING_MODE", value=mode)
    if mode == "LIVE":
        raise RuntimeError("LIVE mode detected; blocking Railway job")
    if mode != "PAPER":
        raise RuntimeError("Railway job requires TRADING_MODE=PAPER")

    _emit_railway_log("PAPER_MODE_CONFIRMED")

    market_date = _today_eastern(now)
    marker_path = Path(os.getenv("RAILWAY_RUN_MARKER_PATH", ".railway_last_run.json"))
    last_run_date = _read_last_run(marker_path)

    if last_run_date == market_date:
        logger.info("railway_start skipped: already ran for market_date=%s", market_date)
        _emit_railway_log("RAILWAY_JOB_COMPLETED", market_date=market_date, status="skipped")
        return {
            "ran": False,
            "market_date": market_date,
            "reason": "already ran for market day",
            "report_path": None,
        }

    _emit_railway_log("ACCOUNT_CHECK_STARTED")
    _emit_railway_log("MARKET_CHECK_STARTED")
    _emit_railway_log("ORDER_DRY_RUN_STARTED")

    result = run_two_week_paper_runner(
        start_day=datetime.fromisoformat(market_date).date(),
        load_env_file=False,
        days=1,
    )

    _write_last_run(marker_path, market_date)
    daily_summary_path = Path(__file__).resolve().parent / "daily_summaries" / f"{market_date}.md"
    _emit_railway_log(
        "ORDER_DRY_RUN_RESULT",
        days_processed=result.get("days_processed"),
        review_required=result.get("review_required"),
        stop_reason=result.get("stop_reason"),
    )
    _emit_railway_log("DAILY_SUMMARY_CREATED", path=daily_summary_path)
    logger.info(
        "railway_start completed market_date=%s days_processed=%s review_required=%s",
        market_date,
        result.get("days_processed"),
        result.get("review_required"),
    )
    _emit_railway_log("RAILWAY_JOB_COMPLETED", market_date=market_date, status="completed")

    return {
        "ran": True,
        "market_date": market_date,
        "reason": "completed",
        "report_path": result.get("report_path"),
    }


def main():
    try:
        run_railway_job()
        return 0
    except Exception as exc:
        _emit_railway_log("RAILWAY_JOB_FAILED", error=_safe_error_text(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
