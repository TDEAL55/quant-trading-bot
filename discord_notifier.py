import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


DEFAULT_CLOUD_ALERT_STATE_PATH = Path("/app/state/discord_alert_state.json")
DEFAULT_LOCAL_ALERT_STATE_PATH = Path(__file__).resolve().parent / ".discord_alert_state.json"
FINAL_ORDER_STATUSES = {"filled", "rejected", "canceled", "cancelled"}


def _emit_log(event, **fields):
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


def _safe_text(value, limit=300):
    text = str(value or "")
    if not text:
        return ""

    patterns = [
        r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(api[_-]?secret\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(authorization\s*[=:]\s*)(?:bearer\s+)?([^\s,;]+)",
        r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
        r"(?i)(account(?:[_-]?(?:id|number))?\s*[=:]\s*)([^\s,;]+)",
    ]
    safe = text
    for pattern in patterns:
        safe = re.sub(pattern, r"\1[REDACTED]", safe)

    safe = re.sub(r"\b\d{8,}\b", "[REDACTED]", safe)
    return safe[:limit]


def mask_identifier(value):
    raw = str(value or "").strip()
    if not raw:
        return "N/A"
    if len(raw) <= 4:
        return "****"
    return f"{raw[:2]}***{raw[-2:]}"


def _alert_state_path():
    configured = os.getenv("DISCORD_ALERT_STATE_PATH")
    if configured:
        return Path(configured)

    running_in_cloud = any(
        os.getenv(name)
        for name in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")
    ) or Path("/app").exists()
    return DEFAULT_CLOUD_ALERT_STATE_PATH if running_in_cloud else DEFAULT_LOCAL_ALERT_STATE_PATH


def _load_state(path: Path):
    if not path.exists():
        return {"sent_event_ids": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"sent_event_ids": []}
    if not isinstance(payload, dict):
        return {"sent_event_ids": []}
    ids = payload.get("sent_event_ids")
    if not isinstance(ids, list):
        return {"sent_event_ids": []}
    return {"sent_event_ids": [str(item) for item in ids[-2000:]]}


def _write_state(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _build_discord_payload(event, fields):
    title = f"DEAL QUANT ALERT: {event.replace('_', ' ').upper()}"
    lines = [f"**{title}**"]
    for key, value in fields.items():
        safe_key = _safe_text(key, limit=80)
        safe_value = _safe_text(value, limit=180)
        if not safe_key or not safe_value:
            continue
        lines.append(f"- {safe_key}: {safe_value}")
    return {"content": "\n".join(lines)}


def _post_webhook(url, body, timeout_seconds):
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 (known URL from env)
        _ = resp.read()


class DiscordNotifier:
    def __init__(self, webhook_url=None, timeout_seconds=3, print_fn=print, http_post=None, state_path=None):
        self.webhook_url = str(webhook_url or "").strip()
        self.timeout_seconds = int(timeout_seconds)
        self.print_fn = print_fn
        self.http_post = http_post or _post_webhook
        self.state_path = Path(state_path) if state_path else _alert_state_path()
        self.state = _load_state(self.state_path)

    @classmethod
    def from_env(cls, print_fn=print):
        return cls(webhook_url=os.getenv("DISCORD_WEBHOOK_URL"), print_fn=print_fn)

    def _log(self, event, **fields):
        _emit_log(event, **fields)

    def _already_sent(self, event_id):
        return str(event_id) in set(self.state.get("sent_event_ids", []))

    def _remember_sent(self, event_id):
        ids = self.state.setdefault("sent_event_ids", [])
        ids.append(str(event_id))
        self.state["sent_event_ids"] = ids[-2000:]
        _write_state(self.state_path, self.state)

    def send_alert(self, event, event_id, **fields):
        event = _safe_text(event, limit=80)
        event_id = _safe_text(event_id, limit=160)
        if not self.webhook_url:
            self._log("DISCORD_ALERT_SKIPPED", reason="not_configured", alert_event=event)
            return False

        if not event_id:
            self._log("DISCORD_ALERT_SKIPPED", reason="missing_event_id", alert_event=event)
            return False

        if self._already_sent(event_id):
            self._log("DISCORD_ALERT_SKIPPED", reason="duplicate_event", alert_event=event)
            return False

        try:
            payload = _build_discord_payload(event, fields)
            self.http_post(self.webhook_url, payload, self.timeout_seconds)
            self._remember_sent(event_id)
            self._log("DISCORD_ALERT_SENT", alert_event=event)
            return True
        except Exception as exc:
            self._log("DISCORD_ALERT_FAILED", type=type(exc).__name__, alert_event=event)
            return False

    def notify_order_status_if_final(self, event_id, status, **fields):
        normalized = str(status or "").strip().lower()
        if normalized not in FINAL_ORDER_STATUSES:
            self._log("DISCORD_ALERT_SKIPPED", reason="non_final_status", alert_event="order_status")
            return False
        return self.send_alert("paper_order_status", event_id, status=normalized, **fields)

    def notify_error(self, event_id, error_type, error_message, **fields):
        return self.send_alert(
            "bot_error",
            event_id,
            error_type=_safe_text(error_type, limit=80),
            error_message=_safe_text(error_message, limit=180),
            **fields,
        )

    def notify_database_error(self, event_id, stage, error_type, error_message):
        return self.send_alert(
            "database_error",
            event_id,
            stage=_safe_text(stage, limit=80),
            error_type=_safe_text(error_type, limit=80),
            error_message=_safe_text(error_message, limit=180),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
