from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import request

from logger_setup import logger
from monitoring_db import MonitoringDatabase


SEVERITY_LEVELS = {
    "INFO": 10,
    "SUCCESS": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

EVENT_TYPES = {
    "bot_started",
    "bot_stopped",
    "bot_crashed",
    "market_closed_wait",
    "scan_started",
    "scan_completed",
    "scan_failed",
    "candidate_selected",
    "trade_recommended",
    "dry_run_trade_skipped",
    "paper_order_submission_requested",
    "paper_order_submitted",
    "paper_order_filled",
    "paper_order_partially_filled",
    "paper_order_rejected",
    "paper_order_cancelled",
    "position_opened",
    "position_closed",
    "risk_limit_triggered",
    "portfolio_recommendation_generated",
    "broker_connection_failed",
    "daily_summary",
    "weekly_summary",
}

SUMMARY_FIELDS_DAILY = (
    "date",
    "bot_status",
    "account_equity",
    "cash",
    "buying_power",
    "daily_realized_pl",
    "daily_unrealized_pl",
    "scans_completed",
    "symbols_evaluated",
    "eligible_candidates",
    "orders_submission_requested",
    "orders_attempted",
    "orders_submitted",
    "filled_orders",
    "closed_trades",
    "win_rate",
    "best_trade",
    "worst_trade",
    "open_positions",
    "risk_stops",
    "failed_scans",
    "top_strategies",
    "top_quantum_score_candidates",
)

SUMMARY_FIELDS_WEEKLY = (
    "account_return",
    "portfolio_return",
    "benchmark_return",
    "proposed_vs_actual_allocations",
    "sector_exposure",
    "strategy_exposure",
    "average_correlation",
    "maximum_correlation",
    "cash_reserve",
    "concentration_warnings",
    "strongest_strategy",
    "weakest_strategy",
    "highest_risk_position",
    "diversification_score",
    "portfolio_risk_score",
    "strategy_leaderboard",
    "total_paper_pl",
    "win_rate",
    "profit_factor",
    "maximum_drawdown",
    "best_strategies",
    "worst_strategies",
    "best_sectors",
    "worst_sectors",
    "factor_effectiveness",
    "recommendations",
    "strategies_recommended_for_pause",
    "proposed_weight_changes",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_text(value: Any, default: str = "N/A", limit: int = 600) -> str:
    text = "" if value is None else str(value)
    text = text.strip()
    if not text:
        return default

    patterns = [
        r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(api[_-]?secret\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(password\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(database_url\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(webhook[_-]?url\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(authorization\s*[=:]\s*)(?:bearer\s+)?([^\s,;]+)",
        r"(?i)(alpaca[_-]?api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(alpaca[_-]?api[_-]?secret\s*[=:]\s*)([^\s,;]+)",
    ]
    safe = text
    for pattern in patterns:
        safe = re.sub(pattern, r"\1[REDACTED]", safe)
    safe = re.sub(r"https://discord\.com/api/webhooks/[^\s]+", "[REDACTED_WEBHOOK_URL]", safe, flags=re.IGNORECASE)
    safe = re.sub(r"\b\d{10,}\b", "[REDACTED]", safe)
    return safe[:limit]


def _safe_json(value: Any) -> str:
    def _sanitize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): _sanitize(val) for key, val in item.items()}
        if isinstance(item, list):
            return [_sanitize(val) for val in item]
        if isinstance(item, tuple):
            return [_sanitize(val) for val in item]
        if item is None:
            return None
        if isinstance(item, (int, float, bool)):
            return item
        return _safe_text(item, default="", limit=2000)

    try:
        encoded = json.dumps(_sanitize(value if value is not None else {}), sort_keys=True, default=str)
    except Exception:
        return "{}"

    max_len = 5000
    if len(encoded) <= max_len:
        return encoded

    return json.dumps(
        {
            "truncated": True,
            "preview": encoded[:max_len],
        },
        sort_keys=True,
    )


def _severity_value(value: str) -> int:
    return int(SEVERITY_LEVELS.get(str(value or "INFO").upper(), 10))


@dataclass(frozen=True)
class NotificationResult:
    status: str
    event_type: str
    severity: str
    provider: str
    deduplication_key: str
    delivered: bool
    retry_count: int
    safe_error_message: str


class NotificationHistoryRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def close(self) -> None:
        self.db.close()

    def ensure_schema(self) -> None:
        if not self.db.enabled:
            return
        self.db.ensure_schema()

    def is_duplicate(
        self,
        *,
        deduplication_key: str,
        provider: str,
        dedup_window_seconds: int,
        now_dt: datetime | None = None,
    ) -> bool:
        if not self.db.enabled:
            return False
        self.ensure_schema()
        now_dt = now_dt or datetime.now(timezone.utc)
        rows = self.db.query_all(
            """
            SELECT timestamp, delivery_status
            FROM notification_history
            WHERE deduplication_key = ? AND provider = ?
            ORDER BY timestamp DESC
            LIMIT 25
            """,
            (str(deduplication_key), str(provider)),
        )
        for row in rows:
            if str(row.get("delivery_status") or "") != "sent":
                continue
            timestamp_text = str(row.get("timestamp") or "").strip()
            if not timestamp_text:
                continue
            try:
                recorded_dt = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
                if recorded_dt.tzinfo is None:
                    recorded_dt = recorded_dt.replace(tzinfo=timezone.utc)
                if recorded_dt >= (now_dt - timedelta(seconds=max(int(dedup_window_seconds), 0))):
                    return True
            except Exception:
                continue
        return False

    def save(
        self,
        *,
        event_type: str,
        severity: str,
        timestamp: str,
        deduplication_key: str,
        delivery_status: str,
        retry_count: int,
        provider: str,
        safe_error_message: str,
        related_run_id: str,
        symbol: str,
        strategy_id: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        if not self.db.enabled:
            return ""
        self.ensure_schema()
        notification_id = f"notif-{int(time.time() * 1000)}-{os.getpid()}-{abs(hash((event_type, deduplication_key, timestamp))) % 100000}"
        self.db.execute(
            """
            INSERT INTO notification_history (
                notification_id, event_type, severity, timestamp, deduplication_key,
                delivery_status, retry_count, provider, safe_error_message,
                related_run_id, symbol, strategy_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                str(event_type),
                str(severity),
                str(timestamp),
                str(deduplication_key),
                str(delivery_status),
                int(retry_count),
                str(provider),
                _safe_text(safe_error_message, default="", limit=1000),
                str(related_run_id or ""),
                str(symbol or ""),
                str(strategy_id or ""),
                _safe_json(metadata or {}),
            ),
        )
        return notification_id


class DiscordWebhookTransport:
    provider = "discord"

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_seconds: int,
        max_retries: int,
        sleep_fn: Callable[[float], None] = time.sleep,
        post_fn: Callable[[str, dict[str, Any], int], None] | None = None,
    ):
        self.webhook_url = str(webhook_url or "").strip()
        self.timeout_seconds = max(int(timeout_seconds), 1)
        self.max_retries = max(int(max_retries), 0)
        self.sleep_fn = sleep_fn
        self.post_fn = post_fn or self._post

    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def _post(self, url: str, body: dict[str, Any], timeout_seconds: int) -> None:
        data = json.dumps(body).encode("utf-8")
        req = request.Request(
            url=url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
            _ = resp.read()

    def send(self, content: str) -> tuple[bool, int, str]:
        if not self.enabled():
            return False, 0, "discord transport not configured"

        payload = {"content": content}
        retries_used = 0
        last_error = ""
        max_attempts = 1 + int(self.max_retries)
        for attempt in range(max_attempts):
            try:
                self.post_fn(self.webhook_url, payload, self.timeout_seconds)
                return True, retries_used, ""
            except Exception as exc:
                retries_used = attempt
                last_error = _safe_text(f"{type(exc).__name__}: {exc}", default="send_failed", limit=300)
                if attempt + 1 >= max_attempts:
                    break
                backoff_seconds = min(2 ** attempt, 8)
                self.sleep_fn(float(backoff_seconds))
        return False, retries_used, last_error


def build_daily_summary_payload(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(metrics or {})
    if "orders_submission_requested" not in payload:
        payload["orders_submission_requested"] = payload.get("orders_attempted", "N/A")
    if "orders_attempted" not in payload:
        # Backward-compatible alias for legacy summaries.
        payload["orders_attempted"] = payload.get("orders_submission_requested", "N/A")
    return {field: payload.get(field, "N/A") for field in SUMMARY_FIELDS_DAILY}


def build_weekly_summary_payload(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(metrics or {})
    summary = {field: payload.get(field, "N/A") for field in SUMMARY_FIELDS_WEEKLY}
    summary["note"] = "Recommendations are review-only and do not auto-apply strategy changes."
    return summary


def format_daily_summary_message(metrics: dict[str, Any] | None = None) -> str:
    payload = build_daily_summary_payload(metrics)
    lines = ["Quantum Bot Daily Summary"]
    for field in SUMMARY_FIELDS_DAILY:
        lines.append(f"{field.replace('_', ' ').title()}: {_safe_text(payload.get(field))}")
    return "\n".join(lines)


def format_weekly_summary_message(metrics: dict[str, Any] | None = None) -> str:
    payload = build_weekly_summary_payload(metrics)
    lines = ["Quantum Bot Weekly Intelligence Summary"]
    for field in SUMMARY_FIELDS_WEEKLY:
        lines.append(f"{field.replace('_', ' ').title()}: {_safe_text(payload.get(field))}")
    lines.append(_safe_text(payload.get("note")))
    return "\n".join(lines)


class NotificationService:
    def __init__(
        self,
        *,
        notifications_enabled: bool = False,
        min_severity: str = "INFO",
        dedup_window_seconds: int = 300,
        history_repo: NotificationHistoryRepository | None = None,
        discord_transport: DiscordWebhookTransport | None = None,
        dry_run_label: str = "PAPER DRY RUN",
        output: str | None = None,
        file_path: str | Path | None = None,
    ):
        # Legacy Sprint 13 mode: file sink used by unattended deployment tests.
        self._legacy_output = str(output or "").strip().lower()
        self._legacy_file_path = Path(file_path) if file_path else None
        self.notifications_enabled = bool(notifications_enabled)
        self.min_severity = str(min_severity or "INFO").upper()
        self.dedup_window_seconds = max(int(dedup_window_seconds), 0)
        self.history_repo = history_repo
        self.discord_transport = discord_transport
        self.dry_run_label = str(dry_run_label or "PAPER DRY RUN")

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._legacy_output != "file" or self._legacy_file_path is None:
            return {"status": "skipped"}

        data = dict(payload or {})
        lines = [
            f"run status: {_safe_text(data.get('run_status'))}",
            f"selected symbol: {_safe_text(data.get('selected_symbol'))}",
            f"score: {_safe_text(data.get('score'))}",
            f"confidence: {_safe_text(data.get('confidence'))}",
            f"risk result: {_safe_text(data.get('risk_result'))}",
            f"order fill: {_safe_text(data.get('order_fill'))}",
            f"reconciliation: {_safe_text(data.get('reconciliation'))}",
            f"portfolio value: {_safe_text(data.get('portfolio_value'))}",
            f"dashboard update: {_safe_text(data.get('dashboard_update'))}",
            "",
        ]
        self._legacy_file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._legacy_file_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        return {"status": "sent"}

    @classmethod
    def from_env(
        cls,
        *,
        database_url: str | None = None,
        post_fn: Callable[[str, dict[str, Any], int], None] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> "NotificationService":
        notifications_enabled = _parse_bool(os.getenv("NOTIFICATIONS_ENABLED"), default=False)
        discord_enabled = _parse_bool(os.getenv("DISCORD_NOTIFICATIONS_ENABLED"), default=False)
        min_severity = str(os.getenv("NOTIFICATION_MIN_SEVERITY", "INFO")).strip().upper() or "INFO"
        timeout_seconds = _parse_int(os.getenv("NOTIFICATION_TIMEOUT_SECONDS", "10"), 10)
        max_retries = _parse_int(os.getenv("NOTIFICATION_MAX_RETRIES", "3"), 3)
        dedup_window_seconds = _parse_int(os.getenv("NOTIFICATION_DEDUP_WINDOW_SECONDS", "300"), 300)
        webhook_url = str(os.getenv("DISCORD_WEBHOOK_URL", "")).strip()

        history_repo = NotificationHistoryRepository(database_url=database_url)
        transport = None
        if notifications_enabled and discord_enabled:
            transport = DiscordWebhookTransport(
                webhook_url=webhook_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                post_fn=post_fn,
                sleep_fn=sleep_fn,
            )

        return cls(
            notifications_enabled=notifications_enabled,
            min_severity=min_severity,
            dedup_window_seconds=dedup_window_seconds,
            history_repo=history_repo,
            discord_transport=transport,
        )

    def close(self) -> None:
        if self.history_repo is not None:
            self.history_repo.close()

    def _is_allowed_severity(self, severity: str) -> bool:
        return _severity_value(str(severity or "INFO").upper()) >= _severity_value(self.min_severity)

    def _format_message(
        self,
        *,
        event_type: str,
        title: str,
        message: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> str:
        dry_run = bool(metadata.get("dry_run", False))
        header = f"Quantum Bot - {self.dry_run_label}" if dry_run else f"Quantum Bot - {severity}"
        lines = [header, "", _safe_text(title, default="Notification", limit=160)]
        if message:
            lines.append(_safe_text(message, default="", limit=400))

        preferred_keys = (
            "run_id",
            "symbol",
            "quantum_score",
            "strategy_id",
            "proposed_quantity",
            "proposed_notional",
            "orders_recommended",
            "orders_submission_requested",
            "orders_attempted",
            "orders_submitted",
            "orders_filled",
            "orders_rejected",
            "filled_quantity",
            "average_fill_price",
            "status",
            "reason",
            "safe_error_message",
            "error_type",
            "last_completed_stage",
        )
        for key in preferred_keys:
            if key not in metadata:
                continue
            value = metadata.get(key)
            if value is None or value == "":
                continue
            lines.append(f"{key.replace('_', ' ').title()}: {_safe_text(value)}")

        if event_type == "weekly_summary":
            lines.append("Note: recommendations are review-only and are not auto-applied.")

        return "\n".join(lines)

    def notify(
        self,
        event_type: str,
        title: str,
        message: str,
        severity: str,
        metadata: dict[str, Any] | None = None,
        deduplication_key: str | None = None,
        deduplication_window_seconds: int | None = None,
    ) -> NotificationResult:
        event_type = str(event_type or "").strip()
        severity = str(severity or "INFO").strip().upper()
        metadata = dict(metadata or {})
        dedup_key = str(deduplication_key or f"{event_type}:{metadata.get('run_id', '')}:{metadata.get('symbol', '')}")
        dedup_seconds = self.dedup_window_seconds if deduplication_window_seconds is None else max(int(deduplication_window_seconds), 0)

        if event_type not in EVENT_TYPES:
            return NotificationResult("skipped_invalid_event", event_type, severity, "none", dedup_key, False, 0, "")
        if severity not in SEVERITY_LEVELS:
            severity = "INFO"

        if not self.notifications_enabled:
            return NotificationResult("disabled", event_type, severity, "none", dedup_key, False, 0, "")
        if not self._is_allowed_severity(severity):
            return NotificationResult("below_min_severity", event_type, severity, "none", dedup_key, False, 0, "")

        provider = "discord" if self.discord_transport is not None else "none"
        now_iso = _utc_iso()
        now_dt = datetime.now(timezone.utc)

        if self.history_repo is not None and self.history_repo.is_duplicate(
            deduplication_key=dedup_key,
            provider=provider,
            dedup_window_seconds=dedup_seconds,
            now_dt=now_dt,
        ):
            return NotificationResult("deduplicated", event_type, severity, provider, dedup_key, False, 0, "")

        content = self._format_message(
            event_type=event_type,
            title=title,
            message=message,
            severity=severity,
            metadata=metadata,
        )

        delivered = False
        retries = 0
        safe_error = ""
        if self.discord_transport is None:
            safe_error = "discord transport unavailable"
        else:
            delivered, retries, safe_error = self.discord_transport.send(content)

        delivery_status = "sent" if delivered else "failed"
        related_run_id = str(metadata.get("run_id") or "")
        symbol = str(metadata.get("symbol") or "")
        strategy_id = str(metadata.get("strategy_id") or "")
        if self.history_repo is not None:
            try:
                self.history_repo.save(
                    event_type=event_type,
                    severity=severity,
                    timestamp=now_iso,
                    deduplication_key=dedup_key,
                    delivery_status=delivery_status,
                    retry_count=retries,
                    provider=provider,
                    safe_error_message=safe_error,
                    related_run_id=related_run_id,
                    symbol=symbol,
                    strategy_id=strategy_id,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning("notification_history_persist_failed type=%s", type(exc).__name__)

        if not delivered and safe_error:
            logger.warning("notification_delivery_failed event_type=%s severity=%s error=%s", event_type, severity, _safe_text(safe_error, default="failed", limit=180))

        return NotificationResult(
            status=("sent" if delivered else "failed"),
            event_type=event_type,
            severity=severity,
            provider=provider,
            deduplication_key=dedup_key,
            delivered=delivered,
            retry_count=retries,
            safe_error_message=_safe_text(safe_error, default="", limit=400),
        )
