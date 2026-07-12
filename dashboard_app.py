import os
import re
from datetime import datetime, timezone

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from monitoring_db import MonitoringDatabase


MAX_DAILY_ORDERS = 3
MAX_DAILY_SUBMITTED_NOTIONAL = 30.0
DASHBOARD_VERSION = "v1.1"


STATUS_COLORS = {
    "healthy": "#1B7F3A",
    "warning": "#B88A00",
    "error": "#B42318",
    "neutral": "#6B7280",
    "buy": "#1B7F3A",
    "hold": "#6B7280",
    "sell": "#B42318",
}


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


def _safe_text(value, fallback=""):
    text = str(value or "").strip()
    if not text:
        return fallback
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
    return safe


def format_currency(value, default="$0.00"):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return f"${number:,.2f}"


def normalize_signal(signal):
    text = str(signal or "").strip().upper()
    if text in {"BUY", "HOLD", "SELL"}:
        return text
    return "HOLD" if not text else text


def _as_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def classify_bot_health(latest_run):
    status = str((latest_run or {}).get("bot_status", "")).strip().lower()
    review_required = _as_bool((latest_run or {}).get("review_required"))
    if review_required or status == "error":
        return "Error", "error"
    if status == "warning":
        return "Warning", "warning"
    if status == "healthy":
        return "Healthy", "healthy"
    return "Warning", "warning"


def classify_market_status(latest_signal):
    market_open = _as_bool((latest_signal or {}).get("market_open"))
    if market_open:
        return "Open", "healthy"
    return "Closed", "neutral"


def classify_signal(signal):
    normalized = normalize_signal(signal)
    if normalized == "BUY":
        return "BUY", "buy"
    if normalized == "SELL":
        return "SELL", "sell"
    return "HOLD", "hold"


def market_display_value(value, latest_signal):
    if value is None:
        market_open = _as_bool((latest_signal or {}).get("market_open"))
        if not market_open:
            return "Waiting for market data"
        return "Waiting for market data"
    return value


def _sanitize_notional(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_run_history_rows(recent_runs, recent_orders):
    order_by_run_id = {}
    for order in recent_orders or []:
        run_id = order.get("run_id")
        if not run_id:
            continue
        order_by_run_id.setdefault(run_id, order)

    rows = []
    for run in recent_runs or []:
        run_id = run.get("run_id")
        order = order_by_run_id.get(run_id, {})
        rows.append(
            {
                "Timestamp": run.get("run_timestamp"),
                "Market Status": run.get("market_status", "unknown"),
                "Signal": normalize_signal(order.get("signal", "HOLD")),
                "Submitted": bool(_as_bool(run.get("submitted"))),
                "Symbol": run.get("symbol", "SPY"),
                "Notional": format_currency(_sanitize_notional(run.get("notional"))),
                "Order Status": run.get("safe_order_status", "unknown"),
                "Stop Reason": _safe_text(run.get("stop_reason", "")),
                "Review Required": bool(_as_bool(run.get("review_required"))),
                "Safe Error Message": _safe_text(run.get("safe_error_message", "")),
            }
        )
    return rows


def build_dashboard_view_model(latest_run, latest_success, latest_signal, latest_account):
    bot_health_text, bot_health_style = classify_bot_health(latest_run)
    market_text, market_style = classify_market_status(latest_signal)
    signal_text, signal_style = classify_signal((latest_signal or {}).get("generated_signal"))

    daily_orders_used = int((latest_signal or {}).get("daily_submitted_order_count") or 0)
    daily_notional_used = _sanitize_notional((latest_signal or {}).get("daily_submitted_notional"))

    return {
        "bot_health": {"label": bot_health_text, "style": bot_health_style},
        "market_status": {"label": market_text, "style": market_style},
        "signal": {"label": signal_text, "style": signal_style},
        "last_successful_run": _safe_value((latest_success or {}).get("run_timestamp"), "Waiting for the next market-hours run"),
        "daily_orders_used_text": f"{daily_orders_used} / {MAX_DAILY_ORDERS}",
        "daily_notional_used_text": f"{format_currency(daily_notional_used)} / {format_currency(MAX_DAILY_SUBMITTED_NOTIONAL)}",
        "trading_mode": _safe_value((latest_run or {}).get("trading_mode"), "PAPER"),
        "review_required": bool(_as_bool((latest_run or {}).get("review_required"))),
        "latest_stop_reason": _safe_text((latest_run or {}).get("stop_reason"), "Waiting for the next market-hours run"),
        "latest_safe_error_message": _safe_text((latest_run or {}).get("safe_error_message"), ""),
        "last_run_timestamp": _safe_value((latest_run or {}).get("run_timestamp"), "Waiting for the next market-hours run"),
        "last_successful_run_timestamp": _safe_value((latest_success or {}).get("run_timestamp"), "Waiting for the next market-hours run"),
        "latest_spy_price": market_display_value((latest_signal or {}).get("latest_price"), latest_signal),
        "latest_market_data_timestamp": market_display_value((latest_signal or {}).get("latest_market_data_timestamp"), latest_signal),
        "short_moving_average": market_display_value((latest_signal or {}).get("short_moving_average"), latest_signal),
        "long_moving_average": market_display_value((latest_signal or {}).get("long_moving_average"), latest_signal),
        "generated_signal": signal_text,
        "trade_or_skip_reason": _safe_text((latest_signal or {}).get("trade_or_skip_reason"), "Waiting for the next market-hours run"),
        "daily_submitted_order_count": daily_orders_used,
        "daily_submitted_notional": daily_notional_used,
        "cooldown_status": _safe_value((latest_signal or {}).get("cooldown_status"), "unknown"),
        "duplicate_signal_status": _safe_value((latest_signal or {}).get("duplicate_signal_status"), "unknown"),
        "pending_order_status": _safe_value((latest_signal or {}).get("pending_order_status"), "unknown"),
        "daily_loss_stop_status": _safe_value((latest_signal or {}).get("daily_loss_stop_status"), "unknown"),
        "portfolio_value": format_currency((latest_account or {}).get("portfolio_value")),
        "cash": format_currency((latest_account or {}).get("cash")),
        "buying_power": format_currency((latest_account or {}).get("buying_power")),
        "unrealized_paper_pl": format_currency((latest_account or {}).get("unrealized_paper_pl"), "$0.00"),
        "open_positions": int((latest_account or {}).get("open_positions") or 0),
        "account_status": _safe_value((latest_account or {}).get("account_status"), "N/A"),
    }


def empty_state_messages(recent_runs, signal_history, recent_orders, open_positions):
    messages = []
    if not recent_runs:
        messages.append("No monitoring records available yet")
    if not recent_orders:
        messages.append("No paper orders yet")
    if not signal_history:
        messages.append("No signal history yet")
    if int(open_positions or 0) == 0:
        messages.append("No open positions")
    return messages


def _render_badge(label, style_key):
    color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
    st.markdown(
        f"<span style='display:inline-block;padding:0.25rem 0.6rem;border-radius:999px;background:{color};color:white;font-size:0.82rem;font-weight:600;'>{label}</span>",
        unsafe_allow_html=True,
    )


def _manual_refresh_header():
    now_utc = datetime.now(timezone.utc)
    if "dashboard_last_refresh" not in st.session_state:
        st.session_state["dashboard_last_refresh"] = now_utc.isoformat()

    title_col, refresh_col, badge_col = st.columns([6, 1.5, 1.5])
    title_col.title("Paper Trading Bot Dashboard")
    refresh_col.write("")
    if refresh_col.button("Refresh", help="Refresh dashboard data only"):
        st.session_state["dashboard_last_refresh"] = datetime.now(timezone.utc).isoformat()
        st.rerun()
    badge_col.write("")
    badge_col.markdown(
        "<div style='margin-top:0.5rem;text-align:right;'><span style='display:inline-block;padding:0.3rem 0.6rem;border-radius:999px;background:#1B7F3A;color:white;font-weight:700;'>PAPER TRADING</span></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Last refresh: {st.session_state['dashboard_last_refresh']}")


def render_dashboard(database_url: str | None = None):
    if st is None:
        raise RuntimeError("streamlit is required to run the dashboard")

    enforce_paper_mode(os.getenv("TRADING_MODE", "PAPER"))

    st.set_page_config(page_title="Paper Trading Bot Dashboard", layout="wide")
    _manual_refresh_header()

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
    portfolio_history = db.fetch_portfolio_history(limit=500)
    signal_history = db.fetch_signal_history(limit=500)
    order_count_by_day = db.fetch_order_count_by_day(limit=120)

    view = build_dashboard_view_model(latest_run, latest_success, latest_signal, latest_account)

    with st.sidebar:
        st.subheader("Read-Only Dashboard")
        st.write(f"Environment: {view['trading_mode']}")
        st.write(f"Database connected: {'yes' if db.enabled else 'no'}")
        st.write(f"Last data refresh: {st.session_state.get('dashboard_last_refresh', 'N/A')}")
        st.write(f"Dashboard version: {DASHBOARD_VERSION}")
        st.caption("No trading controls")

    st.header("Top Status")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Bot health", view["bot_health"]["label"])
    c1.markdown(f"<span style='color:{STATUS_COLORS[view['bot_health']['style']]};font-weight:600;'>status</span>", unsafe_allow_html=True)
    c2.metric("Market status", view["market_status"]["label"])
    c2.markdown(f"<span style='color:{STATUS_COLORS[view['market_status']['style']]};font-weight:600;'>status</span>", unsafe_allow_html=True)
    c3.metric("Current signal", view["signal"]["label"])
    c3.markdown(f"<span style='color:{STATUS_COLORS[view['signal']['style']]};font-weight:600;'>signal</span>", unsafe_allow_html=True)
    c4.metric("Last successful run", view["last_successful_run"])
    c5.metric("Daily orders used", view["daily_orders_used_text"])
    c6.metric("Daily notional used", view["daily_notional_used_text"])

    st.header("Bot Status")
    b1, b2 = st.columns(2)
    b1.write("Trading mode", view["trading_mode"])
    b1.write("Review required", view["review_required"])
    b1.write("Latest stop reason", view["latest_stop_reason"])
    b2.write("Latest safe error message", view["latest_safe_error_message"])
    b2.write("Last run timestamp", view["last_run_timestamp"])
    b2.write("Last successful run timestamp", view["last_successful_run_timestamp"])

    st.header("Market and Signal")
    m1, m2, m3, m4 = st.columns(4)
    m1.write("Market open/closed", view["market_status"]["label"])
    m1.write("Latest SPY price", view["latest_spy_price"])
    m2.write("Latest market-data timestamp", view["latest_market_data_timestamp"])
    m2.write("Short moving average", view["short_moving_average"])
    m3.write("Long moving average", view["long_moving_average"])
    m3.write("Generated signal", view["generated_signal"])
    m4.write("Trade or skip reason", view["trade_or_skip_reason"])
    if str(view["latest_spy_price"]) == "Waiting for market data":
        st.info("Waiting for market data")

    st.header("Daily Safety Limits")
    orders_progress = min(1.0, max(0.0, view["daily_submitted_order_count"] / MAX_DAILY_ORDERS))
    notional_progress = min(1.0, max(0.0, view["daily_submitted_notional"] / MAX_DAILY_SUBMITTED_NOTIONAL))
    st.write(f"Daily order count: {view['daily_submitted_order_count']} / {MAX_DAILY_ORDERS}")
    st.progress(orders_progress)
    st.write(f"Daily submitted notional: {format_currency(view['daily_submitted_notional'])} / {format_currency(MAX_DAILY_SUBMITTED_NOTIONAL)}")
    st.progress(notional_progress)
    s1, s2, s3, s4 = st.columns(4)
    s1.write("Cooldown", view["cooldown_status"])
    s2.write("Duplicate signal", view["duplicate_signal_status"])
    s3.write("Pending order", view["pending_order_status"])
    s4.write("Daily loss stop", view["daily_loss_stop_status"])

    st.header("Alpaca Paper Account")
    a1, a2, a3 = st.columns(3)
    a1.metric("Portfolio value", view["portfolio_value"])
    a1.metric("Cash", view["cash"])
    a2.metric("Buying power", view["buying_power"])
    a2.metric("Unrealized paper P/L", view["unrealized_paper_pl"])
    a3.metric("Open positions", view["open_positions"])
    a3.metric("Account status", view["account_status"])
    if view["open_positions"] == 0:
        st.info("No open positions")
    if not recent_orders:
        st.info("No paper orders yet")

    st.header("Run History")
    run_history_rows = build_run_history_rows(recent_runs, recent_orders)
    if run_history_rows:
        st.dataframe(run_history_rows)
    else:
        st.info("No monitoring records available yet")

    st.header("Charts")
    if portfolio_history:
        st.subheader("Paper Portfolio Value History")
        st.caption("Portfolio value over time")
        st.line_chart({"portfolio_value": [row.get("portfolio_value") for row in reversed(portfolio_history)]})

        st.subheader("Unrealized Paper P/L History")
        st.caption("Unrealized P/L over time")
        st.line_chart({"unrealized_paper_pl": [row.get("unrealized_paper_pl") for row in reversed(portfolio_history)]})
    else:
        st.info("Waiting for the next market-hours run")

    if signal_history:
        st.subheader("Signal History")
        st.caption("Most recent signals")
        st.dataframe(list(reversed(signal_history)))
    else:
        st.info("No signal history yet")

    if order_count_by_day:
        st.subheader("Order Count By Day")
        st.caption("Submitted order count by day")
        st.bar_chart({"submitted_orders": [row.get("submitted_count") for row in reversed(order_count_by_day)]})
    else:
        st.info("No paper orders yet")

    for message in empty_state_messages(recent_runs, signal_history, recent_orders, view["open_positions"]):
        if message in {"No monitoring records available yet", "No signal history yet", "No paper orders yet", "No open positions"}:
            continue
        st.info(message)


def main():
    render_dashboard()


if __name__ == "__main__":
    main()
