import os
import re

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from monitoring_db import MonitoringDatabase


MAX_DAILY_ORDERS = 3
MAX_DAILY_SUBMITTED_NOTIONAL = 30.0


def enforce_paper_mode(mode: str | None):
    normalized = str(mode or "").strip().upper()
    if normalized == "LIVE":
        raise RuntimeError("Dashboard is blocked in LIVE mode")
    if normalized and normalized != "PAPER":
        raise RuntimeError("Dashboard requires TRADING_MODE=PAPER")


def check_dashboard_password(provided: str, expected: str | None) -> bool:
    if not expected:
        return False
    return str(provided or "") == str(expected)


def has_write_capability(module_text: str) -> bool:
    lowered = module_text.lower()
    blocked_call_patterns = [
        r"\bsubmit_order\s*\(",
        r"\bplace_order\s*\(",
        r"\bcancel_order\s*\(",
        r"\brequests\.post\s*\(",
        r"\bclient\.post\s*\(\s*['\"]/?orders",
    ]
    return any(re.search(pattern, lowered) for pattern in blocked_call_patterns)


def _safe_value(value, fallback="N/A"):
    return fallback if value is None else value


def render_dashboard(database_url: str | None = None):
    if st is None:
        raise RuntimeError("streamlit is required to run the dashboard")

    enforce_paper_mode(os.getenv("TRADING_MODE", "PAPER"))

    st.set_page_config(page_title="Paper Trading Monitor", layout="wide")
    st.title("Paper Trading Monitoring Dashboard (Read-Only)")

    expected_password = os.getenv("DASHBOARD_PASSWORD", "")
    provided_password = st.text_input("Dashboard Password", type="password")
    if not check_dashboard_password(provided_password, expected_password):
        st.warning("Access denied")
        st.stop()

    db = MonitoringDatabase(database_url=database_url or os.getenv("DATABASE_URL"))
    if not db.enabled:
        st.info("No monitoring database configured. Set DATABASE_URL.")
        return

    db.ensure_schema()

    latest_run = db.fetch_latest_bot_run() or {}
    latest_success = db.fetch_latest_successful_run() or {}
    latest_signal = db.fetch_latest_signal_snapshot() or {}
    latest_account = db.fetch_latest_account_snapshot() or {}
    recent_runs = db.fetch_recent_runs(limit=100)
    recent_orders = db.fetch_recent_order_events(limit=100)

    st.header("Bot Status")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last Run", _safe_value(latest_run.get("run_timestamp")))
    c2.metric("Last Successful Run", _safe_value(latest_success.get("run_timestamp")))
    c3.metric("Trading Mode", _safe_value(latest_run.get("trading_mode"), "PAPER"))
    c4.metric("Bot Status", _safe_value(latest_run.get("bot_status"), "unknown"))
    st.write(
        {
            "review_required": bool(latest_run.get("review_required", 0)),
            "latest_stop_reason": _safe_value(latest_run.get("stop_reason")),
            "latest_safe_error_message": _safe_value(latest_run.get("safe_error_message"), ""),
        }
    )

    st.header("Market and Signal")
    st.write(
        {
            "market_open": bool(latest_signal.get("market_open", 0)) if latest_signal else "N/A",
            "latest_spy_market_data_timestamp": _safe_value(latest_signal.get("latest_market_data_timestamp")),
            "latest_spy_price": _safe_value(latest_signal.get("latest_price")),
            "short_moving_average": _safe_value(latest_signal.get("short_moving_average")),
            "long_moving_average": _safe_value(latest_signal.get("long_moving_average")),
            "generated_signal": _safe_value(latest_signal.get("generated_signal")),
            "trade_or_skip_reason": _safe_value(latest_signal.get("trade_or_skip_reason")),
        }
    )

    st.header("Daily Safety Limits")
    st.write(
        {
            "daily_submitted_order_count": _safe_value(latest_signal.get("daily_submitted_order_count"), 0),
            "maximum_daily_orders": MAX_DAILY_ORDERS,
            "daily_submitted_notional": _safe_value(latest_signal.get("daily_submitted_notional"), 0.0),
            "maximum_daily_submitted_notional": MAX_DAILY_SUBMITTED_NOTIONAL,
            "cooldown_status": _safe_value(latest_signal.get("cooldown_status"), "unknown"),
            "duplicate_signal_status": _safe_value(latest_signal.get("duplicate_signal_status"), "unknown"),
            "pending_order_status": _safe_value(latest_signal.get("pending_order_status"), "unknown"),
            "daily_loss_stop_status": _safe_value(latest_signal.get("daily_loss_stop_status"), "unknown"),
        }
    )

    st.header("Alpaca Paper Account Snapshot")
    st.write(
        {
            "account_status": _safe_value(latest_account.get("account_status")),
            "portfolio_value": _safe_value(latest_account.get("portfolio_value")),
            "cash": _safe_value(latest_account.get("cash")),
            "buying_power": _safe_value(latest_account.get("buying_power")),
            "open_positions": _safe_value(latest_account.get("open_positions"), 0),
            "unrealized_paper_profit_loss": _safe_value(latest_account.get("unrealized_paper_pl"), 0.0),
            "recent_and_pending_paper_orders": recent_orders[:10],
        }
    )

    st.header("Run History")
    st.dataframe(recent_runs)

    st.header("Charts")
    portfolio_history = db.fetch_portfolio_history(limit=500)
    signal_history = db.fetch_signal_history(limit=500)
    order_count_by_day = db.fetch_order_count_by_day(limit=120)

    if portfolio_history:
        st.subheader("Paper Portfolio Value History")
        st.line_chart({"portfolio_value": [row.get("portfolio_value") for row in reversed(portfolio_history)]})

        st.subheader("Unrealized Paper P/L History")
        st.line_chart({"unrealized_paper_pl": [row.get("unrealized_paper_pl") for row in reversed(portfolio_history)]})
    else:
        st.info("No portfolio history yet")

    if signal_history:
        st.subheader("Signal History")
        st.dataframe(list(reversed(signal_history)))
    else:
        st.info("No signal history yet")

    if order_count_by_day:
        st.subheader("Order Count By Day")
        st.bar_chart({"submitted_orders": [row.get("submitted_count") for row in reversed(order_count_by_day)]})
    else:
        st.info("No order-count history yet")


def main():
    render_dashboard()


if __name__ == "__main__":
    main()
