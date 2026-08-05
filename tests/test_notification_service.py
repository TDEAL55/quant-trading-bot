from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from deployment_config import DeploymentConfigError, load_deployment_config
from notification_service import (
    DiscordWebhookTransport,
    NotificationHistoryRepository,
    NotificationService,
    format_daily_summary_message,
    format_weekly_summary_message,
)


EASTERN_TZ = ZoneInfo("America/New_York")


class _Poster:
    def __init__(self, should_fail: bool = False, exc: Exception | None = None):
        self.should_fail = should_fail
        self.exc = exc or TimeoutError("timeout")
        self.calls = []

    def __call__(self, url, body, timeout_seconds):
        self.calls.append({"url": url, "body": dict(body), "timeout_seconds": timeout_seconds})
        if self.should_fail:
            raise self.exc


class _NoSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds: float):
        self.calls.append(float(seconds))


def _service(tmp_path: Path, poster: _Poster | None = None, enabled: bool = True, min_severity: str = "INFO"):
    repo = NotificationHistoryRepository(database_url=f"sqlite:///{tmp_path / 'notify.db'}")
    transport = DiscordWebhookTransport(
        webhook_url="https://discord.com/api/webhooks/123/abc",
        timeout_seconds=1,
        max_retries=2,
        post_fn=poster or _Poster(),
        sleep_fn=_NoSleep(),
    )
    return NotificationService(
        notifications_enabled=enabled,
        min_severity=min_severity,
        dedup_window_seconds=300,
        history_repo=repo,
        discord_transport=transport,
    )


def test_disabled_notifications_make_no_network_call(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=False)

    result = service.notify(
        event_type="scan_started",
        title="Scan Started",
        message="start",
        severity="INFO",
        metadata={"run_id": "r1", "dry_run": True},
        deduplication_key="k1",
    )

    assert result.status == "disabled"
    assert poster.calls == []


def test_webhook_url_is_never_logged(tmp_path, caplog):
    poster = _Poster(should_fail=True, exc=RuntimeError("webhook_url=https://discord.com/api/webhooks/123/verysecret"))
    service = _service(tmp_path, poster=poster, enabled=True)

    service.notify(
        event_type="scan_failed",
        title="Scan Failed",
        message="failed",
        severity="ERROR",
        metadata={"run_id": "r1", "dry_run": True},
        deduplication_key="k2",
    )

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "verysecret" not in joined
    assert "api/webhooks/123" not in joined


def test_discord_timeout_does_not_crash_runner_path(tmp_path):
    poster = _Poster(should_fail=True, exc=TimeoutError("timeout"))
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="broker_connection_failed",
        title="Broker Down",
        message="timeout",
        severity="ERROR",
        metadata={"run_id": "r2", "dry_run": True},
        deduplication_key="k3",
    )

    assert result.status == "failed"
    assert result.delivered is False


def test_retries_are_bounded(tmp_path):
    poster = _Poster(should_fail=True, exc=TimeoutError("timeout"))
    sleep = _NoSleep()
    repo = NotificationHistoryRepository(database_url=f"sqlite:///{tmp_path / 'notify.db'}")
    transport = DiscordWebhookTransport(
        webhook_url="https://discord.com/api/webhooks/123/abc",
        timeout_seconds=1,
        max_retries=3,
        post_fn=poster,
        sleep_fn=sleep,
    )
    service = NotificationService(
        notifications_enabled=True,
        min_severity="INFO",
        dedup_window_seconds=300,
        history_repo=repo,
        discord_transport=transport,
    )

    service.notify(
        event_type="scan_failed",
        title="Scan Failed",
        message="failure",
        severity="ERROR",
        metadata={"run_id": "r3", "dry_run": True},
        deduplication_key="k4",
    )

    assert len(poster.calls) == 4
    assert len(sleep.calls) == 3


def test_deduplication_suppresses_duplicates(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    first = service.notify(
        event_type="scan_completed",
        title="Scan Completed",
        message="ok",
        severity="SUCCESS",
        metadata={"run_id": "r4", "dry_run": True},
        deduplication_key="same-key",
    )
    second = service.notify(
        event_type="scan_completed",
        title="Scan Completed",
        message="ok",
        severity="SUCCESS",
        metadata={"run_id": "r4", "dry_run": True},
        deduplication_key="same-key",
    )

    assert first.status == "sent"
    assert second.status == "deduplicated"
    assert len(poster.calls) == 1


def test_different_event_keys_delivered_separately(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    a = service.notify(
        event_type="scan_completed",
        title="A",
        message="ok",
        severity="SUCCESS",
        metadata={"run_id": "r5", "dry_run": True},
        deduplication_key="k-a",
    )
    b = service.notify(
        event_type="scan_completed",
        title="B",
        message="ok",
        severity="SUCCESS",
        metadata={"run_id": "r5", "dry_run": True},
        deduplication_key="k-b",
    )

    assert a.status == "sent"
    assert b.status == "sent"
    assert len(poster.calls) == 2


def test_market_closed_notification_sends_once_per_market_date(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    r1 = service.notify(
        event_type="market_closed_wait",
        title="Market Closed",
        message="waiting",
        severity="INFO",
        metadata={"run_id": "r6", "dry_run": True, "market_date": "2026-08-03"},
        deduplication_key="market_closed_wait:2026-08-03",
        deduplication_window_seconds=60 * 60 * 30,
    )
    r2 = service.notify(
        event_type="market_closed_wait",
        title="Market Closed",
        message="waiting",
        severity="INFO",
        metadata={"run_id": "r6", "dry_run": True, "market_date": "2026-08-03"},
        deduplication_key="market_closed_wait:2026-08-03",
        deduplication_window_seconds=60 * 60 * 30,
    )

    assert r1.status == "sent"
    assert r2.status == "deduplicated"


def test_dry_run_messages_never_claim_submission(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="dry_run_trade_skipped",
        title="Dry Run Trade Skipped",
        message="skip",
        severity="INFO",
        metadata={
            "run_id": "r7",
            "dry_run": True,
            "symbol": "AAPL",
            "quantum_score": 88.4,
            "strategy_id": "trend_momentum_v1",
            "proposed_notional": 1000,
            "orders_attempted": 1,
            "orders_submitted": 0,
            "reason": "dry_run_mode",
        },
        deduplication_key="dry-run-msg",
    )

    assert result.status == "sent"
    assert len(poster.calls) == 1
    content = poster.calls[0]["body"]["content"]
    assert "PAPER DRY RUN" in content
    assert "Orders Submitted: 0" in content
    assert "submitted to broker" not in content.lower()


def test_paper_order_events_contain_safe_metadata(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="paper_order_rejected",
        title="Order Rejected",
        message="failed",
        severity="ERROR",
        metadata={
            "run_id": "r8",
            "dry_run": False,
            "symbol": "AAPL",
            "strategy_id": "trend_momentum_v1",
            "reason": "ALPACA_API_SECRET=mysecret",
        },
        deduplication_key="order-rejected",
    )

    assert result.status == "sent"
    content = poster.calls[0]["body"]["content"]
    assert "AAPL" in content
    assert "trend_momentum_v1" in content
    assert "mysecret" not in content


def test_crash_event_is_emitted_on_fatal_runner_failure(tmp_path):
    from continuous_paper_runner import run_continuous_paper_runner

    sent = []

    class _Notifier:
        def __init__(self, database_url=None):
            self.database_url = database_url

        def notify(self, **kwargs):
            sent.append(dict(kwargs))

        def close(self):
            return None

    cfg_path = tmp_path / "runner.db"
    cfg = type(
        "Cfg",
        (),
        {
            "trading_mode": "PAPER",
            "database_url": f"sqlite:///{cfg_path}",
            "database_path": cfg_path,
            "scan_interval_minutes": 5,
            "max_daily_orders": 1,
            "scan_only_during_market_hours": False,
            "continuous_runner_dry_run": True,
        },
    )()

    with pytest.raises(RuntimeError, match="fatal sleep"):
        run_continuous_paper_runner(
            config_loader=lambda: cfg,
            runner=lambda **kwargs: {"execution_status": "no_candidates", "execution": {}, "scan": {}},
            now_provider=lambda: datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN_TZ),
            sleep_fn=lambda _seconds: (_ for _ in ()).throw(RuntimeError("fatal sleep")),
            max_iterations=2,
            notifier_factory=lambda **kwargs: _Notifier(**kwargs),
        )

    assert any(item.get("event_type") == "bot_crashed" for item in sent)


def test_daily_summary_handles_missing_data_safely():
    text = format_daily_summary_message({})
    assert "N/A" in text


def test_weekly_summary_remains_review_only():
    text = format_weekly_summary_message({})
    assert "review-only" in text.lower()


def test_live_mode_remains_blocked_in_deployment_config(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
    monkeypatch.setenv("MAX_DAILY_ORDERS", "5")
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "5")
    monkeypatch.setenv("MAX_POSITION_EQUITY_PERCENT", "10")
    monkeypatch.setenv("SCAN_INTERVAL_MINUTES", "5")

    with pytest.raises(DeploymentConfigError, match="LIVE trading is hard-blocked"):
        load_deployment_config()


def test_notification_history_persisted_without_secrets(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    service.notify(
        event_type="scan_failed",
        title="Failed",
        message="error",
        severity="ERROR",
        metadata={"run_id": "r9", "dry_run": True, "safe_error_message": "ALPACA_API_KEY=abc123"},
        deduplication_key="persist-safe",
    )

    repo = NotificationHistoryRepository(database_url=f"sqlite:///{tmp_path / 'notify.db'}")
    rows = repo.db.query_all("SELECT metadata_json FROM notification_history")
    assert rows
    metadata_json = str(rows[0].get("metadata_json") or "")
    assert "abc123" not in metadata_json
    assert "REDACTED" in metadata_json
    parsed = json.loads(metadata_json)
    assert parsed.get("run_id") == "r9"


def test_portfolio_recommendation_notification_is_concise_and_deduplicated(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    first = service.notify(
        event_type="portfolio_recommendation_generated",
        title="Portfolio Recommendation Generated",
        message="Final portfolio recommendation is ready for human review.",
        severity="INFO",
        metadata={
            "run_id": "r10",
            "dry_run": True,
            "orders_attempted": 0,
            "orders_submitted": 0,
        },
        deduplication_key="portfolio_recommendation_generated:r10",
    )
    second = service.notify(
        event_type="portfolio_recommendation_generated",
        title="Portfolio Recommendation Generated",
        message="Final portfolio recommendation is ready for human review.",
        severity="INFO",
        metadata={
            "run_id": "r10",
            "dry_run": True,
            "orders_attempted": 0,
            "orders_submitted": 0,
        },
        deduplication_key="portfolio_recommendation_generated:r10",
    )

    assert first.status == "sent"
    assert second.status == "deduplicated"
    assert len(poster.calls) == 1
    content = poster.calls[0]["body"]["content"]
    assert "PAPER DRY RUN" in content
    assert "Orders Submitted: 0" in content
