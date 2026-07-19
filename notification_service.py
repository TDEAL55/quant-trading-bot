from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def _safe_text(value: Any, default: str = "N/A") -> str:
    text = "" if value is None else str(value)
    return text.strip() or default


@dataclass(frozen=True)
class NotificationPayload:
    run_status: str
    selected_symbol: str
    score: Any
    confidence: Any
    risk_result: Any
    order_fill: Any
    reconciliation: Any
    portfolio_value: Any
    dashboard_update: Any


class NotificationService:
    def __init__(self, output: str = "console", file_path: str | Path | None = None, print_fn: Callable[[str], None] = print):
        self.output = str(output).lower()
        self.file_path = Path(file_path) if file_path else None
        self.print_fn = print_fn

    def build_message(self, payload: dict[str, Any]) -> str:
        lines = [
            "Daily Summary",
            f"run status: {_safe_text(payload.get('run_status'))}",
            f"selected symbol: {_safe_text(payload.get('selected_symbol'))}",
            f"score: {_safe_text(payload.get('score'))}",
            f"confidence: {_safe_text(payload.get('confidence'))}",
            f"risk result: {_safe_text(payload.get('risk_result'))}",
            f"order/fill: {_safe_text(payload.get('order_fill'))}",
            f"reconciliation: {_safe_text(payload.get('reconciliation'))}",
            f"portfolio value: {_safe_text(payload.get('portfolio_value'))}",
            f"dashboard update: {_safe_text(payload.get('dashboard_update'))}",
        ]
        return "\n".join(lines)

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = self.build_message(payload)
        if self.output == "file":
            if self.file_path is None:
                raise ValueError("file_path is required for file output")
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n\n")
        else:
            self.print_fn(message)
        return {"status": "sent", "output": self.output}
