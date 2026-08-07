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


def test_new_paper_execution_event_types_are_deliverable(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    for event_type in (
        "trade_recommended",
        "paper_order_submission_requested",
        "paper_order_partially_filled",
        "paper_order_cancelled",
    ):
        result = service.notify(
            event_type=event_type,
            title="Lifecycle Update",
            message="event delivered",
            severity="INFO",
            metadata={"run_id": "r11", "dry_run": False, "symbol": "AAPL"},
            deduplication_key=f"{event_type}:r11",
        )
        assert result.status == "sent"

    assert len(poster.calls) == 4


def test_recommendation_only_wording_is_unambiguous(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="trade_recommended",
        title="Trade Recommended",
        message="Recommendation only. No paper order has been submitted.",
        severity="INFO",
        metadata={"run_id": "r12", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="trade_recommended:r12:AAPL",
    )

    assert result.status == "sent"
    content = poster.calls[0]["body"]["content"]
    assert "Trade Recommended" in content
    assert "Recommendation only" in content
    assert "Order submitted" not in content
    assert "Order filled" not in content
    assert "Accepted by Alpaca" not in content


def test_submission_requested_wording_never_claims_acceptance(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="paper_order_submission_requested",
        title="Paper Order Submission Requested",
        message="Execution gate passed; requesting paper order submission.",
        severity="INFO",
        metadata={"run_id": "r13", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="paper_order_submission_requested:r13:AAPL",
    )

    assert result.status == "sent"
    content = poster.calls[0]["body"]["content"]
    assert "Paper Order Submission Requested" in content
    assert "Accepted by Alpaca PAPER" not in content


def test_submitted_wording_requires_broker_acceptance_phrase(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="paper_order_submitted",
        title="Paper Order Submitted",
        message="Accepted by Alpaca PAPER.",
        severity="SUCCESS",
        metadata={"run_id": "r14", "dry_run": False, "symbol": "AAPL", "order_id": "ord-1"},
        deduplication_key="paper_order_submitted:r14:ord-1",
    )

    assert result.status == "sent"
    content = poster.calls[0]["body"]["content"]
    assert "Paper Order Submitted" in content
    assert "Accepted by Alpaca PAPER" in content


def test_filled_wording_includes_quantity_and_price(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="paper_order_filled",
        title="Paper Order Filled",
        message="Paper order reached filled status.",
        severity="SUCCESS",
        metadata={
            "run_id": "r15",
            "dry_run": False,
            "symbol": "AAPL",
            "filled_quantity": 2.5,
            "average_fill_price": 123.45,
        },
        deduplication_key="paper_order_filled:r15:ord-2",
    )

    assert result.status == "sent"
    content = poster.calls[0]["body"]["content"]
    assert "Paper Order Filled" in content
    assert "Filled Quantity: 2.5" in content
    assert "Average Fill Price: 123.45" in content


def test_rejected_wording_uses_safe_reason_only(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    result = service.notify(
        event_type="paper_order_rejected",
        title="Paper Order Rejected",
        message="Paper order failed or was rejected by broker.",
        severity="ERROR",
        metadata={
            "run_id": "r16",
            "dry_run": False,
            "symbol": "AAPL",
            "reason": "ALPACA_API_KEY=secret-reason",
        },
        deduplication_key="paper_order_rejected:r16:ord-3",
    )

    assert result.status == "sent"
    content = poster.calls[0]["body"]["content"]
    assert "Paper Order Rejected" in content
    assert "reason" in content.lower()
    assert "secret-reason" not in content


def test_dedup_scopes_event_type_and_order_id(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    first = service.notify(
        event_type="paper_order_submitted",
        title="Paper Order Submitted",
        message="Accepted by Alpaca PAPER.",
        severity="SUCCESS",
        metadata={"run_id": "r17", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="paper_order_submitted:ord-9",
    )
    duplicate = service.notify(
        event_type="paper_order_submitted",
        title="Paper Order Submitted",
        message="Accepted by Alpaca PAPER.",
        severity="SUCCESS",
        metadata={"run_id": "r17", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="paper_order_submitted:ord-9",
    )
    different_event = service.notify(
        event_type="paper_order_filled",
        title="Paper Order Filled",
        message="Paper order reached filled status.",
        severity="SUCCESS",
        metadata={"run_id": "r17", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="paper_order_filled:ord-9",
    )

    assert first.status == "sent"
    assert duplicate.status == "deduplicated"
    assert different_event.status == "sent"
    assert len(poster.calls) == 2


def test_cancelled_and_partial_fill_events_are_clear(tmp_path):
    poster = _Poster()
    service = _service(tmp_path, poster=poster, enabled=True)

    partial = service.notify(
        event_type="paper_order_partially_filled",
        title="Paper Order Partially Filled",
        message="Paper order received a partial fill.",
        severity="WARNING",
        metadata={"run_id": "r18", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="paper_order_partially_filled:r18:ord-10",
    )
    cancelled = service.notify(
        event_type="paper_order_cancelled",
        title="Paper Order Cancelled",
        message="Paper order was cancelled before full fill.",
        severity="WARNING",
        metadata={"run_id": "r18", "dry_run": False, "symbol": "AAPL"},
        deduplication_key="paper_order_cancelled:r18:ord-10",
    )

    assert partial.status == "sent"
    assert cancelled.status == "sent"
    assert "Partially Filled" in poster.calls[0]["body"]["content"]
    assert "Cancelled" in poster.calls[1]["body"]["content"]
