import json

from discord_notifier import DiscordNotifier


class _RecordingPoster:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []

    def __call__(self, url, body, timeout_seconds):
        if self.should_fail:
            raise RuntimeError("authorization=Bearer super-secret")
        self.calls.append({"url": url, "body": body, "timeout_seconds": timeout_seconds})


def test_duplicate_event_is_sent_once(tmp_path):
    poster = _RecordingPoster()
    notifier = DiscordNotifier(
        webhook_url="https://example.invalid/webhook",
        http_post=poster,
        state_path=tmp_path / "discord_state.json",
    )

    first = notifier.send_alert("paper_order_submitted", "event-1", symbol="SPY", signal="BUY")
    second = notifier.send_alert("paper_order_submitted", "event-1", symbol="SPY", signal="BUY")

    assert first is True
    assert second is False
    assert len(poster.calls) == 1


def test_notification_failure_is_safe_and_non_raising(tmp_path):
    poster = _RecordingPoster(should_fail=True)
    notifier = DiscordNotifier(
        webhook_url="https://example.invalid/webhook",
        http_post=poster,
        state_path=tmp_path / "discord_state.json",
    )

    sent = notifier.send_alert("bot_error", "event-fail", error_message="token=abc123")
    assert sent is False


def test_secrets_and_full_ids_are_sanitized(tmp_path):
    poster = _RecordingPoster()
    notifier = DiscordNotifier(
        webhook_url="https://example.invalid/webhook",
        http_post=poster,
        state_path=tmp_path / "discord_state.json",
    )

    notifier.send_alert(
        "paper_order_submitted",
        "event-sanitize",
        symbol="SPY",
        notional="10",
        signal="BUY",
        order_id="paper-order-123456789012",
        note="ALPACA_API_KEY=my-secret-key authorization=Bearer abcdef token=xyz",
    )

    assert len(poster.calls) == 1
    body = poster.calls[0]["body"]
    assert isinstance(body, dict)
    content = body.get("content", "")
    assert "my-secret-key" not in content
    assert "abcdef" not in content
    assert "123456789012" not in content
    assert "[REDACTED]" in content


def test_final_status_only_alerts_for_relevant_statuses(tmp_path):
    poster = _RecordingPoster()
    notifier = DiscordNotifier(
        webhook_url="https://example.invalid/webhook",
        http_post=poster,
        state_path=tmp_path / "discord_state.json",
    )

    sent_non_final = notifier.notify_order_status_if_final("evt-accepted", status="accepted", symbol="SPY")
    sent_final = notifier.notify_order_status_if_final("evt-filled", status="filled", symbol="SPY")

    assert sent_non_final is False
    assert sent_final is True
    assert len(poster.calls) == 1
