import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from monitoring_db import MonitoringDatabase


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object, limit: int = 200) -> str:
    text = str(value or "")
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
    safe = re.sub(r"\b\d{8,}\b", "[REDACTED]", safe)
    return safe[:limit]


def mask_identifier(value: object) -> str:
    raw = str(value or "")
    if not raw:
        return "N/A"
    compact = raw.strip()
    if len(compact) <= 4:
        return "****"
    return f"{compact[:2]}***{compact[-2:]}"


class MonitoringRecorder:
    def __init__(self, database_url: str | None = None, print_fn=print):
        self.print_fn = print_fn
        self.db = MonitoringDatabase(database_url=database_url)
        self.run_id = os.getenv("BOT_RUN_ID") or f"run-{uuid4().hex}"

    @classmethod
    def from_env(cls, print_fn=print):
        return cls(database_url=os.getenv("DATABASE_URL"), print_fn=print_fn)

    @property
    def enabled(self) -> bool:
        return self.db.enabled

    def _warn(self, stage: str, exc: Exception):
        self.print_fn(
            f"MONITORING_DB_WARNING stage={stage} type={type(exc).__name__} message={_safe_text(exc)}",
            flush=True,
        )

    def ensure_schema(self):
        if not self.enabled:
            return
        try:
            self.db.ensure_schema()
        except Exception as exc:  # pragma: no cover - defensive logging path
            self._warn("ensure_schema", exc)

    def record_signal_snapshot(self, payload: dict):
        if not self.enabled:
            return
        data = dict(payload)
        data["run_id"] = self.run_id
        data.setdefault("snapshot_timestamp", _utc_now_iso())
        data["trade_or_skip_reason"] = _safe_text(data.get("trade_or_skip_reason", ""))
        try:
            self.db.insert_signal_snapshot(data)
        except Exception as exc:
            self._warn("insert_signal_snapshot", exc)

    def record_account_snapshot(self, payload: dict):
        if not self.enabled:
            return
        data = dict(payload)
        data["run_id"] = self.run_id
        data.setdefault("snapshot_timestamp", _utc_now_iso())
        try:
            self.db.insert_account_snapshot(data)
        except Exception as exc:
            self._warn("insert_account_snapshot", exc)

    def record_order_event(self, payload: dict):
        if not self.enabled:
            return
        data = dict(payload)
        data["run_id"] = self.run_id
        data.setdefault("event_timestamp", _utc_now_iso())
        data["safe_error_message"] = _safe_text(data.get("safe_error_message", ""))
        data["stop_reason"] = _safe_text(data.get("stop_reason", ""))
        data["order_id_masked"] = mask_identifier(data.get("order_id_masked") or data.get("order_id"))
        data.pop("order_id", None)
        try:
            self.db.insert_order_event(data)
        except Exception as exc:
            self._warn("insert_order_event", exc)

    def finalize_run(self, payload: dict):
        if not self.enabled:
            return
        data = dict(payload)
        data["run_id"] = self.run_id
        data.setdefault("run_timestamp", _utc_now_iso())
        data["safe_error_message"] = _safe_text(data.get("safe_error_message", ""))
        data["stop_reason"] = _safe_text(data.get("stop_reason", ""))
        try:
            self.db.insert_bot_run(data)
        except Exception as exc:
            self._warn("insert_bot_run", exc)


class FailingMonitoringRecorder:
    def __init__(self, run_id: str = "failed-run"):
        self.run_id = run_id
        self.enabled = True

    def ensure_schema(self):
        raise RuntimeError("forced monitoring schema failure")

    def record_signal_snapshot(self, payload: dict):
        raise RuntimeError("forced signal snapshot failure")

    def record_account_snapshot(self, payload: dict):
        raise RuntimeError("forced account snapshot failure")

    def record_order_event(self, payload: dict):
        raise RuntimeError("forced order event failure")

    def finalize_run(self, payload: dict):
        raise RuntimeError("forced bot run failure")
