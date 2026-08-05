from __future__ import annotations

import os

from deployment_config import load_deployment_config
from notification_service import NotificationService


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    config = load_deployment_config()
    send_test = _parse_bool(os.getenv("SEND_NOTIFICATION_TEST"), default=False)
    notifications_enabled = _parse_bool(os.getenv("NOTIFICATIONS_ENABLED"), default=False)
    discord_enabled = _parse_bool(os.getenv("DISCORD_NOTIFICATIONS_ENABLED"), default=False)

    service = NotificationService.from_env(database_url=config.database_url)
    try:
        if not notifications_enabled:
            print("NOTIFICATION_CHECK status=skipped reason=notifications_disabled")
            return 0
        if not discord_enabled:
            print("NOTIFICATION_CHECK status=skipped reason=discord_disabled")
            return 0
        if not send_test:
            print("NOTIFICATION_CHECK status=skipped reason=SEND_NOTIFICATION_TEST_not_true")
            return 0

        result = service.notify(
            event_type="bot_started",
            title="Notification Test",
            message="This is a sanitized notification connectivity test.",
            severity="INFO",
            metadata={
                "run_id": "notification-connection-check",
                "dry_run": True,
                "status": "test",
                "orders_submitted": 0,
            },
            deduplication_key="notification_connection_test",
            deduplication_window_seconds=1,
        )

        if result.delivered:
            print("NOTIFICATION_CHECK status=success provider=discord")
            return 0

        print(
            "NOTIFICATION_CHECK status=failed provider={provider} safe_error={safe_error}".format(
                provider=result.provider,
                safe_error=result.safe_error_message or "unknown",
            )
        )
        return 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
