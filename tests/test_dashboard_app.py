from pathlib import Path

import pytest

import dashboard_app


def test_password_protection_helper():
    assert dashboard_app.check_dashboard_password("abc", "abc") is True
    assert dashboard_app.check_dashboard_password("abc", "def") is False
    assert dashboard_app.check_dashboard_password("", "def") is False


def test_paper_only_enforcement_blocks_live():
    with pytest.raises(RuntimeError, match="blocked in LIVE mode"):
        dashboard_app.enforce_paper_mode("LIVE")


def test_dashboard_code_has_no_write_capability():
    module_text = Path("dashboard_app.py").read_text(encoding="utf-8")
    assert dashboard_app.has_write_capability(module_text) is False


def test_dashboard_allows_empty_database_reads(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'monitoring.db'}"
    db = dashboard_app.MonitoringDatabase(database_url=db_url)
    db.ensure_schema()

    assert db.fetch_latest_bot_run() is None
    assert db.fetch_recent_runs(limit=5) == []


def test_bot_health_state_labels():
    assert dashboard_app.classify_bot_health({"bot_status": "healthy", "review_required": 0}) == ("Healthy", "healthy")
    assert dashboard_app.classify_bot_health({"bot_status": "warning", "review_required": 0}) == ("Warning", "warning")
    assert dashboard_app.classify_bot_health({"bot_status": "error", "review_required": 0}) == ("Error", "error")
    assert dashboard_app.classify_bot_health({"bot_status": "healthy", "review_required": 1}) == ("Error", "error")


def test_market_closed_and_signal_state_labels():
    assert dashboard_app.classify_market_status({"market_open": 0}) == ("Closed", "neutral")
    assert dashboard_app.classify_market_status({"market_open": 1}) == ("Open", "healthy")
    assert dashboard_app.classify_signal("BUY") == ("BUY", "buy")
    assert dashboard_app.classify_signal("HOLD") == ("HOLD", "hold")
    assert dashboard_app.classify_signal("SELL") == ("SELL", "sell")


def test_currency_formatting():
    assert dashboard_app.format_currency(1234.5) == "$1,234.50"
    assert dashboard_app.format_currency("bad", default="$0.00") == "$0.00"


def test_market_waiting_message_for_missing_values():
    assert dashboard_app.market_display_value(None, {"market_open": 0}) == "Waiting for market data"
    assert dashboard_app.market_display_value(None, {"market_open": 1}) == "Waiting for market data"


def test_empty_state_messages():
    messages = dashboard_app.empty_state_messages(recent_runs=[], signal_history=[], recent_orders=[], open_positions=0)
    assert "No monitoring records available yet" in messages
    assert "No paper orders yet" in messages
    assert "No signal history yet" in messages
    assert "No open positions" in messages


class _FakeContainer:
    def __init__(self, streamlit_ref):
        self._st = streamlit_ref

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def write(self, value):
        self._st._calls.append(("write", value))

    def markdown(self, value, unsafe_allow_html=False):
        self._st._calls.append(("markdown", value, unsafe_allow_html))

    def metric(self, label, value, delta=None):
        self._st._calls.append(("metric", label, value, delta))

    def title(self, value):
        self._st._calls.append(("title", value))

    def button(self, label, help=None):
        self._st._calls.append(("button", label, help))
        return False


class _FakeStreamlit(_FakeContainer):
    def __init__(self):
        super().__init__(self)
        self._calls = []
        self.session_state = {}
        self.sidebar = _FakeContainer(self)

    def set_page_config(self, **kwargs):
        self._calls.append(("set_page_config", kwargs))

    def text_input(self, label, type="default"):
        self._calls.append(("text_input", label, type))
        return "test-pass"

    def warning(self, message):
        self._calls.append(("warning", message))

    def stop(self):
        raise RuntimeError("stop called")

    def header(self, value):
        self._calls.append(("header", value))

    def subheader(self, value):
        self._calls.append(("subheader", value))

    def caption(self, value):
        self._calls.append(("caption", value))

    def columns(self, count):
        self._calls.append(("columns", count))
        if isinstance(count, int):
            size = count
        else:
            size = len(list(count))
        return [_FakeContainer(self) for _ in range(size)]

    def progress(self, value):
        self._calls.append(("progress", value))

    def dataframe(self, value):
        self._calls.append(("dataframe", value))

    def line_chart(self, value):
        self._calls.append(("line_chart", value))

    def bar_chart(self, value):
        self._calls.append(("bar_chart", value))

    def info(self, message):
        self._calls.append(("info", message))

    def rerun(self):
        self._calls.append(("rerun",))


class _FakeDatabase:
    def __init__(self, database_url=None):
        self.enabled = True

    def ensure_schema(self):
        return None

    def fetch_latest_bot_run(self):
        return {
            "run_id": "run-ui",
            "run_timestamp": "2026-07-12T15:00:00+00:00",
            "trading_mode": "PAPER",
            "bot_status": "healthy",
            "review_required": 0,
            "stop_reason": "completed",
            "safe_error_message": "",
            "market_status": "open",
            "submitted": 1,
            "symbol": "SPY",
            "notional": 10.0,
            "safe_order_status": "accepted",
        }

    def fetch_latest_successful_run(self):
        return {
            "run_timestamp": "2026-07-12T15:00:00+00:00",
        }

    def fetch_latest_signal_snapshot(self):
        return {
            "market_open": 1,
            "latest_market_data_timestamp": "2026-07-12T14:59:00+00:00",
            "latest_price": 601.23,
            "short_moving_average": 600.5,
            "long_moving_average": 598.4,
            "generated_signal": "BUY",
            "trade_or_skip_reason": "submitted",
            "daily_submitted_order_count": 1,
            "daily_submitted_notional": 10.0,
            "cooldown_status": "inactive",
            "duplicate_signal_status": "clear",
            "pending_order_status": "clear",
            "daily_loss_stop_status": "clear",
        }

    def fetch_latest_account_snapshot(self):
        return {
            "account_status": "ACTIVE",
            "portfolio_value": 1005.0,
            "cash": 995.0,
            "buying_power": 1000.0,
            "unrealized_paper_pl": 5.0,
            "open_positions": 1,
        }

    def fetch_recent_runs(self, limit=100):
        return [self.fetch_latest_bot_run()]

    def fetch_recent_order_events(self, limit=100):
        return [
            {
                "run_id": "run-ui",
                "signal": "BUY",
                "submitted": 1,
                "symbol": "SPY",
                "safe_order_status": "accepted",
                "stop_reason": "submitted",
                "review_required": 0,
                "safe_error_message": "",
                "order_id_masked": "pa***12",
            }
        ]

    def fetch_portfolio_history(self, limit=500):
        return [
            {"snapshot_timestamp": "2026-07-12T14:50:00+00:00", "portfolio_value": 1000.0, "unrealized_paper_pl": 0.0},
            {"snapshot_timestamp": "2026-07-12T15:00:00+00:00", "portfolio_value": 1005.0, "unrealized_paper_pl": 5.0},
        ]

    def fetch_signal_history(self, limit=500):
        return [
            {"snapshot_timestamp": "2026-07-12T15:00:00+00:00", "generated_signal": "BUY"},
        ]

    def fetch_order_count_by_day(self, limit=120):
        return [{"market_date": "2026-07-12", "submitted_count": 1}]


def test_render_dashboard_executes_all_sections_with_populated_data(monkeypatch):
    fake_st = _FakeStreamlit()
    monkeypatch.setattr(dashboard_app, "st", fake_st)
    monkeypatch.setattr(dashboard_app, "MonitoringDatabase", _FakeDatabase)
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass")

    dashboard_app.render_dashboard(database_url="postgresql://example")

    headers = [call[1] for call in fake_st._calls if call[0] == "header"]
    assert "Top Status" in headers
    assert "Bot Status" in headers
    assert "Market and Signal" in headers
    assert "Daily Safety Limits" in headers
    assert "Alpaca Paper Account" in headers
    assert "Run History" in headers
    assert "Charts" in headers
    assert any(call[0] == "dataframe" for call in fake_st._calls)
    assert any(call[0] == "line_chart" for call in fake_st._calls)
    assert any(call[0] == "bar_chart" for call in fake_st._calls)
