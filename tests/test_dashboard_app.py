from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import dashboard_app


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_password_protection_helper():
    assert dashboard_app.check_dashboard_password("abc", "abc") is True
    assert dashboard_app.check_dashboard_password("abc", "def") is False
    assert dashboard_app.check_dashboard_password("", "def") is False


def test_paper_only_enforcement_blocks_live():
    with pytest.raises(RuntimeError, match="blocked in LIVE mode"):
        dashboard_app.enforce_paper_mode("LIVE")


def test_dashboard_code_has_no_write_capability():
    module_text = (REPO_ROOT / "dashboard_app.py").read_text(encoding="utf-8")
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
    assert dashboard_app.market_display_value(None, {"market_open": 0}) == "Waiting for the next market-hours update"
    assert dashboard_app.market_display_value(None, {"market_open": 1}) == "Waiting for the next market-hours update"


def test_empty_state_messages():
    payload = {"recent_runs": [], "recent_orders": [], "signal_history": []}
    view = {"open_positions": 0}
    messages = dashboard_app.empty_state_messages(payload, view)
    assert "No monitoring records available yet" in messages
    assert "No paper orders yet" in messages
    assert "No signal history yet" in messages
    assert "No open positions" in messages


def test_format_percent_and_mobile_layout_helpers():
    assert dashboard_app.format_percent(1.25) == "+1.25%"
    assert dashboard_app.format_percent(-1.25) == "-1.25%"
    assert dashboard_app.is_mobile_layout(320) is True
    assert dashboard_app.is_mobile_layout(1080) is False


def test_market_clock_open_and_closed_states():
    tz = ZoneInfo("America/New_York")
    open_dt = datetime(2026, 7, 13, 10, 0, 0, tzinfo=tz)
    closed_dt = datetime(2026, 7, 12, 10, 0, 0, tzinfo=tz)

    open_clock = dashboard_app.build_market_clock(open_dt)
    closed_clock = dashboard_app.build_market_clock(closed_dt)

    assert open_clock["label"] == "MARKET OPEN"
    assert open_clock["is_open"] is True
    assert closed_clock["label"] == "MARKET CLOSED"
    assert closed_clock["is_open"] is False


def test_signal_strength_meter_boundaries():
    weak = dashboard_app.build_signal_strength(0.01)
    strong = dashboard_app.build_signal_strength(9.0)

    assert weak["value"] >= 0.0
    assert strong["value"] == 1.0
    assert "Informational" in strong["description"]


class _FakeContainer:
    def __init__(self, streamlit_ref, button_return=False):
        self._st = streamlit_ref
        self._button_return = button_return

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

    def button(self, label, help=None, **kwargs):
        self._st._calls.append(("button", label, help, kwargs))
        return self._button_return

    def selectbox(self, label, options, **kwargs):
        self._st._calls.append(("selectbox", label, options, kwargs))
        key = kwargs.get("key")
        if key and key in self._st.session_state:
            return self._st.session_state[key]
        value = options[0]
        if key:
            self._st.session_state[key] = value
        return value

    def text_input(self, label, value="", type="default", **kwargs):
        self._st._calls.append(("container_text_input", label, value, type, kwargs))
        key = kwargs.get("key")
        if key:
            return self._st.session_state.get(key, value)
        return value

    def progress(self, value):
        self._st._calls.append(("container_progress", value))

    def caption(self, value):
        self._st._calls.append(("container_caption", value))


class _FakeStreamlit(_FakeContainer):
    def __init__(self, button_return=False):
        super().__init__(self, button_return=button_return)
        self._calls = []
        self.session_state = {}
        self.sidebar = _FakeContainer(self, button_return=button_return)
        self._button_return = button_return

    def set_page_config(self, **kwargs):
        self._calls.append(("set_page_config", kwargs))

    def text_input(self, label, type="default", key=None, **kwargs):
        self._calls.append(("text_input", label, type, key, kwargs))
        if key:
            return self.session_state.get(key, "test-pass")
        return "test-pass"

    def warning(self, message):
        self._calls.append(("warning", message))

    def error(self, message):
        self._calls.append(("error", message))

    def success(self, message):
        self._calls.append(("success", message))

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
        return [_FakeContainer(self, button_return=self._button_return) for _ in range(size)]

    def tabs(self, names):
        self._calls.append(("tabs", names))
        return [_FakeContainer(self, button_return=self._button_return) for _ in names]

    def radio(self, label, options, horizontal=False, key=None):
        self._calls.append(("radio", label, options, horizontal, key))
        if key and key in self.session_state:
            return self.session_state[key]
        value = options[0]
        if key:
            self.session_state[key] = value
        return value

    def progress(self, value):
        self._calls.append(("progress", value))

    def dataframe(self, value):
        self._calls.append(("dataframe", value))

    def line_chart(self, value):
        self._calls.append(("line_chart", value))

    def bar_chart(self, value):
        self._calls.append(("bar_chart", value))

    def plotly_chart(self, value, width="stretch"):
        self._calls.append(("plotly_chart", width))

    def info(self, message):
        self._calls.append(("info", message))

    def spinner(self, text):
        self._calls.append(("spinner", text))
        return _FakeContainer(self)

    def rerun(self):
        self._calls.append(("rerun",))

    def form(self, key, clear_on_submit=False):
        self._calls.append(("form", key, clear_on_submit))
        return _FakeContainer(self, button_return=self._button_return)

    def form_submit_button(self, label, **kwargs):
        self._calls.append(("form_submit_button", label, kwargs))
        return self._button_return

    def download_button(self, label, data, file_name=None, mime=None, key=None, disabled=False):
        self._calls.append(("download_button", label, file_name, mime, key, disabled, bool(data)))
        return False


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
    monkeypatch.setattr(dashboard_app, "_cached_payload", lambda database_url: {
        "db_connected": True,
        "latest_run": _FakeDatabase().fetch_latest_bot_run(),
        "latest_success": _FakeDatabase().fetch_latest_successful_run(),
        "latest_signal": _FakeDatabase().fetch_latest_signal_snapshot(),
        "latest_account": _FakeDatabase().fetch_latest_account_snapshot(),
        "recent_runs": _FakeDatabase().fetch_recent_runs(),
        "recent_orders": _FakeDatabase().fetch_recent_order_events(),
        "portfolio_history": list(reversed(_FakeDatabase().fetch_portfolio_history())),
        "signal_history": [
            {"snapshot_timestamp": "2026-07-12T14:45:00+00:00", "latest_price": 600.4, "short_moving_average": 599.9, "long_moving_average": 598.8, "generated_signal": "HOLD"},
            {"snapshot_timestamp": "2026-07-12T15:00:00+00:00", "latest_price": 601.2, "short_moving_average": 600.5, "long_moving_average": 598.4, "generated_signal": "BUY"},
        ],
        "order_count_by_day": list(reversed(_FakeDatabase().fetch_order_count_by_day())),
    })
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass")
    fake_st.session_state["dashboard_authenticated"] = True

    dashboard_app.render_dashboard(database_url="postgresql://example")

    nav_calls = [call for call in fake_st._calls if call[0] == "selectbox" and call[1] == "Navigate"]
    assert nav_calls
    assert nav_calls[0][2] == ["Command Center", "Strategy", "Risk", "Portfolio", "Orders", "Performance", "Operations", "Alerts", "Research"]
    assert all("trade" not in str(page).lower() for page in nav_calls[0][2])

    build_markers = [call for call in fake_st._calls if call[0] == "markdown" and dashboard_app.UI_BUILD_LABEL in call[1]]
    assert len(build_markers) == 1
    assert not any(call[0] == "markdown" and "<div class='dq-skeleton'>" in call[1] for call in fake_st._calls)


def test_refresh_calls_cache_clear_and_rerun_without_trading_actions(monkeypatch):
    fake_st = _FakeStreamlit(button_return=True)
    monkeypatch.setattr(dashboard_app, "st", fake_st)
    monkeypatch.setattr(dashboard_app, "_cached_payload", lambda database_url: {
        "db_connected": True,
        "latest_run": {},
        "latest_success": {},
        "latest_signal": {},
        "latest_account": {},
        "recent_runs": [],
        "recent_orders": [],
        "portfolio_history": [],
        "signal_history": [],
        "order_count_by_day": [],
    })
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass")
    fake_st.session_state["dashboard_authenticated"] = True

    class _CacheData:
        @staticmethod
        def clear():
            fake_st._calls.append(("cache_clear",))

    fake_st.cache_data = _CacheData()
    dashboard_app.render_dashboard(database_url="postgresql://example")

    assert any(call[0] == "cache_clear" for call in fake_st._calls)
    assert any(call[0] == "rerun" for call in fake_st._calls)


def test_authenticated_state_hides_password_input(monkeypatch):
    fake_st = _FakeStreamlit()
    fake_st.session_state["dashboard_authenticated"] = True
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    assert dashboard_app._ensure_authenticated("test-pass") is True
    assert not any(call[0] == "text_input" and call[1] == "Dashboard Password" for call in fake_st._calls)


def test_successful_authentication_clears_password_and_reruns(monkeypatch):
    fake_st = _FakeStreamlit(button_return=True)
    fake_st.session_state["dashboard_password_input"] = "test-pass"
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    assert dashboard_app._ensure_authenticated("test-pass") is True
    assert fake_st.session_state.get("dashboard_authenticated") is True
    assert fake_st.session_state.get("dashboard_password_clear_requested") is True
    assert any(call[0] == "rerun" for call in fake_st._calls)


def test_hold_and_market_closed_are_neutral_in_status_bar():
    payload = {"db_connected": True, "latest_run": {"run_timestamp": "2026-07-12T15:00:00+00:00"}}
    view = {"generated_signal": "HOLD"}
    clock = {"is_open": False, "countdown": "17:22:00"}

    items = dashboard_app.build_status_bar_items(payload, view, clock)
    market = next(item for item in items if item["label"].startswith("MARKET"))
    signal = next(item for item in items if item["label"].startswith("CURRENT SIGNAL"))
    countdown = next(item for item in items if item["label"].startswith("NEXT OPEN IN"))

    assert market["label"] == "MARKET CLOSED"
    assert market["style"] == "neutral"
    assert signal["label"] == "CURRENT SIGNAL: HOLD"
    assert signal["style"] == "neutral"
    assert countdown["label"] == "NEXT OPEN IN 17H 22M"


def test_notification_builder_and_empty_positions_state():
    payload = {
        "db_connected": True,
        "latest_run": {"run_timestamp": "2026-07-12T15:00:00+00:00", "bot_status": "healthy", "review_required": 0, "trading_mode": "PAPER"},
        "latest_success": {"run_timestamp": "2026-07-12T15:00:00+00:00"},
        "latest_signal": {"market_open": 1, "generated_signal": "HOLD", "daily_submitted_order_count": 0, "daily_submitted_notional": 0.0},
        "latest_account": {"portfolio_value": 1000.0, "cash": 1000.0, "buying_power": 1000.0, "open_positions": 0, "account_status": "ACTIVE"},
        "recent_runs": [],
        "recent_orders": [
            {"submitted": 0, "stop_reason": "daily order limit reached", "event_timestamp": "2026-07-12T15:00:00+00:00", "signal": "BUY", "safe_order_status": "blocked", "symbol": "SPY"}
        ],
        "portfolio_history": [{"portfolio_value": 1000.0}],
        "signal_history": [],
        "order_count_by_day": [],
    }
    view = dashboard_app.build_dashboard_view_model(payload)
    notices = dashboard_app.build_notification_items(payload, view)
    assert any(item["severity"] == "Warning" for item in notices)
    assert view["open_position_value"] == 0.0


def test_disconnected_database_payload_is_safe(monkeypatch):
    class _BrokenDb:
        def __init__(self, database_url=None):
            self.enabled = True

        def ensure_schema(self):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(dashboard_app, "MonitoringDatabase", _BrokenDb)
    payload = dashboard_app._fetch_payload_uncached("sqlite:///ignore.db")
    assert payload["db_connected"] is False
    assert payload["recent_orders"] == []


def test_initialize_session_state_preserves_existing_values(monkeypatch):
    fake_st = _FakeStreamlit()
    fake_st.session_state["dashboard_page"] = "Strategy"
    fake_st.session_state["dashboard_theme"] = "Black Terminal"
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    dashboard_app.initialize_dashboard_session_state()

    assert fake_st.session_state["dashboard_page"] == "Strategy"
    assert fake_st.session_state["dashboard_theme"] == "Black Terminal"
    assert fake_st.session_state["dashboard_timeframe"] == "1D"


def test_refresh_button_sets_force_refresh_without_page_reset(monkeypatch):
    fake_st = _FakeStreamlit(button_return=True)
    fake_st.session_state["dashboard_page"] = "Strategy"
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    dashboard_app.render_header({"db_connected": True, "latest_run": {}}, {"generated_signal": "HOLD"})

    assert fake_st.session_state.get("dashboard_force_refresh") is True
    assert fake_st.session_state.get("dashboard_page") == "Strategy"


def test_timeframe_uses_persisted_session_state(monkeypatch):
    fake_st = _FakeStreamlit()
    fake_st.session_state["dashboard_timeframe"] = "5D"
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    payload = {
        "db_connected": True,
        "signal_history": [
            {"snapshot_timestamp": "2026-07-12T14:45:00+00:00", "latest_price": 600.4, "short_moving_average": 599.9, "long_moving_average": 598.8, "generated_signal": "HOLD"},
            {"snapshot_timestamp": "2026-07-12T15:00:00+00:00", "latest_price": 601.2, "short_moving_average": 600.5, "long_moving_average": 598.4, "generated_signal": "BUY"},
        ],
    }
    dashboard_app._render_spy_chart_frame(payload, {"generated_signal": "HOLD", "latest_market_data_timestamp": "2026-07-12T15:00:00+00:00"})

    assert fake_st.session_state.get("dashboard_timeframe") == "5D"


def test_alert_acknowledgement_is_session_local(monkeypatch):
    fake_st = _FakeStreamlit(button_return=True)
    fake_st.session_state["dashboard_acknowledged_alerts"] = []
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    payload = {
        "db_connected": True,
        "recent_orders": [{"submitted": 0, "stop_reason": "daily order limit reached", "event_timestamp": "2026-07-12T15:00:00+00:00", "signal": "BUY"}],
    }
    view = {"review_required": False, "latest_stop_reason": "", "latest_safe_error_message": "", "last_run_timestamp": "2026-07-12 11:00:00 AM ET"}

    dashboard_app.render_notification_center(payload, view)

    assert fake_st.session_state.get("dashboard_acknowledged_alerts")


def test_research_downloads_disable_when_no_rows(monkeypatch):
    fake_st = _FakeStreamlit()
    fake_st.session_state["dashboard_export_payload"] = {
        "activity": [],
        "signals": [],
        "orders": [],
        "health": [],
        "performance": [],
    }
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    dashboard_app.render_research_page()

    download_calls = [call for call in fake_st._calls if call[0] == "download_button"]
    assert download_calls
    assert all(call[5] is True for call in download_calls)


def test_research_page_renders_evaluation_payload(monkeypatch):
    fake_st = _FakeStreamlit()
    fake_st.session_state["dashboard_research_payload"] = {
        "db_connected": True,
        "latest_research_run": {"research_run_id": "research-1", "completed_at": "2024-01-10T15:00:00+00:00", "benchmark_symbol": "SPY", "market_regime": "strong_bull", "universe_size": 1, "scanner_duration_seconds": 1.0, "eligible_count": 1, "rejected_count": 0, "error_count": 0, "data_source": "synthetic"},
        "recent_research_runs": [],
        "selected_research_run_id": "research-1",
        "selected_research_candidates": [],
        "research_analytics": {
            "total_research_runs": 1,
            "total_candidate_observations": 1,
            "average_candidates_per_run": 1.0,
            "average_overall_score": 80.0,
            "average_confidence": 70.0,
            "score_distribution": [],
            "confidence_distribution": [],
            "candidate_count_by_sector": [],
            "candidate_count_by_regime": [],
            "signal_distribution": [],
            "top_recurring_symbols": [],
            "average_score_by_sector": [],
            "average_confidence_by_sector": [],
            "average_score_by_regime": [],
            "average_confidence_by_regime": [],
        },
        "latest_research_summary": {},
        "evaluation": {
            "db_connected": True,
            "latest_labeling_run": {"latest_attempted_at": "2024-01-10T15:00:00+00:00"},
            "recent_labeled_observations": [
                {
                    "observation_date": "2024-01-02",
                    "symbol": "AAA",
                    "rank": 1,
                    "overall_score": 80.0,
                    "confidence": 70.0,
                    "sector": "Technology",
                    "market_regime": "strong_bull",
                    "forward_1d_return": 0.01,
                    "forward_5d_return": 0.05,
                    "forward_10d_return": 0.10,
                    "forward_20d_return": 0.20,
                    "forward_1d_excess_return": 0.01,
                    "forward_5d_excess_return": 0.05,
                    "forward_10d_excess_return": 0.10,
                    "forward_20d_excess_return": 0.20,
                    "label_status": "complete",
                }
            ],
            "recent_label_failures": [],
            "selected_horizon": "1d",
            "evaluation_analytics": {
                "benchmark_symbol": "SPY",
                "total_observations": 1,
                "labeled_candidates": 1,
                "status_counts": {"pending": 0, "partial": 0, "complete": 1, "unavailable": 0, "data_error": 0},
                "horizons": {"1d": {"sample_size": 1, "average_raw_return": 0.01, "average_benchmark_return": 0.0, "average_excess_return": 0.01, "median_raw_return": 0.01, "median_excess_return": 0.01, "positive_return_rate": 1.0, "positive_excess_return_rate": 1.0}},
                "score_buckets": {"1d": [{"bucket": "below_40", "candidate_count": 0}]},
                "confidence_buckets": {"1d": [{"bucket": "below_40", "candidate_count": 0}]},
                "regime_analysis": {"1d": []},
                "sector_analysis": {"1d": []},
                "signal_analysis": {"1d": []},
                "rank_analysis": {"1d": []},
                "recurring_symbol_analysis": {"1d": []},
                "correlations": {"1d": {"sample_size": 1, "score_vs_forward_return": None, "score_vs_excess_return": None, "confidence_vs_forward_return": None, "confidence_vs_excess_return": None, "rank_vs_forward_return": None, "rank_vs_excess_return": None}},
                "latest_attempted_at": "2024-01-10T15:00:00+00:00",
            },
            "evaluation_config": {"benchmark_symbol": "SPY"},
        },
    }
    monkeypatch.setattr(dashboard_app, "st", fake_st)

    dashboard_app.render_research_page()

    assert any(call[0] == "markdown" and "Strategy Evaluation" in call[1] for call in fake_st._calls)
    assert any(call[0] == "selectbox" and call[1] == "Evaluation horizon" for call in fake_st._calls)
    assert any(call[0] == "dataframe" for call in fake_st._calls)


def test_dashboard_app_has_no_use_container_width_calls():
    module_text = (REPO_ROOT / "dashboard_app.py").read_text(encoding="utf-8")
    assert "use_container_width" not in module_text
