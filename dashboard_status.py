from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_ET = time(9, 30)
MARKET_CLOSE_ET = time(16, 0)


@dataclass(frozen=True)
class StatusResult:
    severity: str
    label: str
    explanation: str
    timestamp: str
    source: str


def _as_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _as_text(value, fallback="Unknown") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _parse_dt(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def format_est(value, fallback="Waiting for the next market-hours update") -> str:
    dt = _parse_dt(value)
    if dt is None:
        return fallback
    return dt.astimezone(EASTERN_TZ).strftime("%Y-%m-%d %I:%M:%S %p ET")


def normalize_mode(mode: str | None) -> str:
    return _as_text(mode, "PAPER").upper()


def classify_market_clock(now_et: datetime | None = None) -> dict[str, object]:
    now_et = now_et or datetime.now(EASTERN_TZ)
    market_open = now_et.weekday() < 5 and MARKET_OPEN_ET <= now_et.time() < MARKET_CLOSE_ET
    if market_open:
        target = now_et.replace(hour=MARKET_CLOSE_ET.hour, minute=MARKET_CLOSE_ET.minute, second=0, microsecond=0)
        countdown_label = "Closes in"
        label = "MARKET OPEN"
        severity = "Healthy"
    else:
        target = now_et
        if now_et.time() >= MARKET_CLOSE_ET or now_et.weekday() >= 5:
            target = now_et + timedelta(days=1)
            while target.weekday() >= 5:
                target += timedelta(days=1)
        target = target.replace(hour=MARKET_OPEN_ET.hour, minute=MARKET_OPEN_ET.minute, second=0, microsecond=0)
        countdown_label = "Opens in"
        label = "MARKET CLOSED"
        severity = "Waiting"
    remaining = max(int((target - now_et).total_seconds()), 0)
    return {
        "is_open": market_open,
        "label": label,
        "severity": severity,
        "countdown_label": countdown_label,
        "countdown_seconds": remaining,
        "countdown_text": f"{remaining // 3600:02d}:{(remaining % 3600) // 60:02d}:{remaining % 60:02d}",
        "timestamp": now_et.astimezone(EASTERN_TZ).strftime("%Y-%m-%d %I:%M:%S %p ET"),
    }


def classify_signal(signal) -> tuple[str, str]:
    normalized = _as_text(signal, "HOLD").upper()
    if normalized == "BUY":
        return "BUY", "Healthy"
    if normalized == "SELL":
        return "SELL", "Critical"
    return "HOLD", "Neutral"


def classify_bot_health(latest_run: dict | None) -> StatusResult:
    latest_run = latest_run or {}
    bot_status = str(latest_run.get("bot_status", "")).strip().lower()
    review_required = _as_bool(latest_run.get("review_required"))
    timestamp = format_est(latest_run.get("run_timestamp"))
    if review_required:
        return StatusResult("Critical", "Review Required", "A manual review is required before the next decision cycle.", timestamp, "worker")
    if bot_status == "error":
        return StatusResult("Critical", "Error", "The worker reported a non-recoverable error.", timestamp, "worker")
    if bot_status == "warning":
        return StatusResult("Warning", "Warning", "The worker completed with a noncritical issue.", timestamp, "worker")
    if bot_status == "healthy":
        return StatusResult("Healthy", "Healthy", "The worker completed successfully.", timestamp, "worker")
    return StatusResult("Waiting", "Waiting", "No worker result is available yet.", timestamp, "worker")


def classify_database_state(db_connected: bool, latest_run: dict | None) -> StatusResult:
    latest_run = latest_run or {}
    timestamp = format_est(latest_run.get("run_timestamp"))
    if db_connected:
        return StatusResult("Healthy", "Healthy", "The monitoring database responded to the latest read-only query.", timestamp, "database")
    return StatusResult("Warning", "Unavailable", "The monitoring database could not be reached, so the dashboard is showing the last known state.", timestamp, "database")


def classify_risk_state(name: str, enabled: bool, triggered: bool = False, warning: bool = False) -> StatusResult:
    if triggered:
        severity = "Critical"
        label = "Triggered"
        explanation = f"{name} has triggered and is currently blocking new action."
    elif warning:
        severity = "Warning"
        label = "Warning"
        explanation = f"{name} needs attention before the next scheduled cycle."
    elif enabled:
        severity = "Healthy"
        label = "Armed"
        explanation = f"{name} is armed and monitoring conditions normally."
    else:
        severity = "Waiting"
        label = "Unavailable"
        explanation = f"{name} has no active state available."
    return StatusResult(severity, label, explanation, format_est(None), name)
