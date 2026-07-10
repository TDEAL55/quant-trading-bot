import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from logger_setup import logger
import report_checker
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


def _safe_error_message(exc):
    text = str(exc or "")
    if not text:
        return ""

    patterns = [
        r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(api[_-]?secret\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(authorization\s*[=:]\s*)([^,;\n]+)",
        r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(account(?:[_-]?(?:id|number))?\s*[=:]\s*)([^\s,;]+)",
    ]

    safe = text
    for pattern in patterns:
        safe = re.sub(pattern, r"\1[REDACTED]", safe)
    safe = re.sub(r"\b\d{8,}\b", "[REDACTED]", safe)
    return safe[:200]


def _state_directory_check():
    configured = bool(os.getenv("PAPER_DAILY_STATE_PATH"))
    state_path = Path(os.getenv("PAPER_DAILY_STATE_PATH", "/app/state/paper_daily_state.json"))
    state_dir = state_path.parent
    exists = state_dir.exists()
    writable = bool(exists and os.access(state_dir, os.W_OK))
    return {
        "STATE_PATH_CONFIGURED": configured,
        "STATE_DIRECTORY_EXISTS": bool(exists),
        "STATE_DIRECTORY_WRITABLE": bool(writable),
    }


def _today_eastern(now=None):
    current = now or datetime.now(EASTERN_TZ)
    return current.astimezone(EASTERN_TZ).date().isoformat()


def run_railway_job(now=None):
    """Run a single safe paper-trading cycle for Railway scheduled execution."""
    stage = "startup"
    try:
        mode = os.getenv("TRADING_MODE", "SIMULATION").upper()
        _emit_railway_log("RAILWAY_JOB_STARTED")
        _emit_railway_log("TRADING_MODE", value=mode)
        if mode == "LIVE":
            raise RuntimeError("LIVE mode detected; blocking Railway job")
        if mode != "PAPER":
            raise RuntimeError("Railway job requires TRADING_MODE=PAPER")

        _emit_railway_log("PAPER_MODE_CONFIRMED")

        stage = "state_directory_check"
        _emit_railway_log("STATE_CHECK", **_state_directory_check())

        market_date = _today_eastern(now)

        stage = "account_check"
        _emit_railway_log("ACCOUNT_CHECK_STARTED")
        stage = "market_check"
        _emit_railway_log("MARKET_CHECK_STARTED")

        stage = "paper_runner_start"
        result = run_two_week_paper_runner(
            start_day=datetime.fromisoformat(market_date).date(),
            load_env_file=False,
            days=1,
            dry_run=False,
            submit_enabled=True,
        )

        stage = "report_checker"
        _emit_railway_log("REPORT_CHECKER_STARTED")
        try:
            report_checker.check_latest_report(
                summary_dir=Path(__file__).resolve().parent / "daily_summaries",
                print_fn=print,
            )
            _emit_railway_log("REPORT_CHECKER_COMPLETED")
        except Exception:
            _emit_railway_log("REPORT_CHECKER_FAILED")
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
    except Exception as exc:
        _emit_railway_log(
            "RAILWAY_JOB_FAILED",
            RAILWAY_JOB_STAGE=stage,
            RAILWAY_JOB_ERROR_TYPE=type(exc).__name__,
            RAILWAY_JOB_ERROR_MESSAGE=_safe_error_message(exc),
        )
        raise


def main():
    try:
        run_railway_job()
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
