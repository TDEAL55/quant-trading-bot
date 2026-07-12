import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

try:
    import plotly.express as px
except Exception:  # pragma: no cover
    px = None

from monitoring_db import MonitoringDatabase


MAX_DAILY_ORDERS = 3
MAX_DAILY_SUBMITTED_NOTIONAL = 30.0
DASHBOARD_VERSION = "v2.0"
EASTERN_TZ = ZoneInfo("America/New_York")

STATUS_COLORS = {
    "healthy": "#21c46b",
    "warning": "#f1c75b",
    "error": "#ff5c5c",
    "neutral": "#44a3ff",
    "buy": "#21c46b",
    "hold": "#8a96a8",
    "sell": "#ff5c5c",
}


if st is not None and hasattr(st, "cache_data"):

    @st.cache_data(ttl=20, show_spinner=False)
    def _cached_payload(database_url: str | None):
        return _fetch_payload_uncached(database_url)

else:

    def _cached_payload(database_url: str | None):
        return _fetch_payload_uncached(database_url)


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


def _as_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_currency(value, default="$0.00"):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return f"${number:,.2f}"


def format_percent(value, default="0.00%"):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    sign = "+" if number >= 0 else ""
    return f"{sign}{number:.2f}%"


def normalize_signal(signal):
    text = str(signal or "").strip().upper()
    if text in {"BUY", "HOLD", "SELL"}:
        return text
    return "HOLD" if not text else text


def friendly_status_text(value: Any, fallback="Unknown"):
    text = str(value or "").strip()
    if not text:
        return fallback
    if "." in text:
        text = text.split(".")[-1]
    text = text.replace("_", " ")
    return text.title()


def format_timestamp_eastern(value, fallback="Waiting for the next market-hours run"):
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_est = dt.astimezone(EASTERN_TZ)
        return dt_est.strftime("%Y-%m-%d %I:%M:%S %p ET")
    except Exception:
        return text


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
    if _as_bool((latest_signal or {}).get("market_open")):
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
        return "Waiting for next market-hours run"
    if not _as_bool((latest_signal or {}).get("market_open")):
        return value
    return value


def calculate_daily_and_total_pl(portfolio_history):
    if not portfolio_history:
        return 0.0, 0.0
    values = [_as_float(item.get("portfolio_value"), 0.0) for item in portfolio_history]
    if not values:
        return 0.0, 0.0
    total_pl = values[-1] - values[0]
    if len(values) >= 2:
        daily_pl = values[-1] - values[-2]
    else:
        daily_pl = 0.0
    return daily_pl, total_pl


def moving_average_distance(short_ma, long_ma):
    return _as_float(short_ma) - _as_float(long_ma)


def empty_state_messages(payload, view):
    messages = []
    if not payload.get("recent_runs"):
        messages.append("No monitoring records available yet")
    if not payload.get("recent_orders"):
        messages.append("No paper orders yet")
    if not payload.get("signal_history"):
        messages.append("No signal history yet")
    if int(view.get("open_positions", 0)) == 0:
        messages.append("No open positions")
    return messages


def is_mobile_layout(viewport_width):
    try:
        return int(viewport_width) < 768
    except Exception:
        return False


def build_run_history_rows(recent_runs, recent_orders):
    order_by_run_id = {}
    for order in recent_orders or []:
        run_id = order.get("run_id")
        if run_id and run_id not in order_by_run_id:
            order_by_run_id[run_id] = order

    rows = []
    for run in recent_runs or []:
        run_id = run.get("run_id")
        order = order_by_run_id.get(run_id, {})
        rows.append(
            {
                "Timestamp": format_timestamp_eastern(run.get("run_timestamp")),
                "Market Status": friendly_status_text(run.get("market_status"), "Unknown"),
                "Signal": normalize_signal(order.get("signal", "HOLD")),
                "Submitted": bool(_as_bool(run.get("submitted"))),
                "Symbol": run.get("symbol", "SPY"),
                "Notional": format_currency(run.get("notional"), "$0.00"),
                "Order Status": friendly_status_text(run.get("safe_order_status"), "Unknown"),
                "Stop Reason": _safe_text(run.get("stop_reason", ""), "Waiting for the next market-hours run"),
                "Review Required": bool(_as_bool(run.get("review_required"))),
                "Safe Error Message": _safe_text(run.get("safe_error_message", ""), ""),
            }
        )
    return rows


def build_order_rows(recent_orders):
    rows = []
    for order in recent_orders or []:
        rows.append(
            {
                "Timestamp": format_timestamp_eastern(order.get("event_timestamp")),
                "Symbol": order.get("symbol", "SPY"),
                "Signal": normalize_signal(order.get("signal", "HOLD")),
                "Submitted": bool(_as_bool(order.get("submitted"))),
                "Notional": format_currency(order.get("notional"), "$0.00"),
                "Order Status": friendly_status_text(order.get("safe_order_status"), "Unknown"),
                "Stop Reason": _safe_text(order.get("stop_reason", ""), "N/A"),
                "Review Required": bool(_as_bool(order.get("review_required"))),
            }
        )
    return rows


def load_research_summary():
    report_paths = [
        Path(__file__).resolve().parent / "OVERNIGHT_BACKTEST_REPORT.md",
        Path(__file__).resolve().parent / "OVERNIGHT_COST_SENSITIVITY_2023.md",
    ]
    text_chunks = []
    for path in report_paths:
        if path.exists():
            text_chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    text = "\n".join(text_chunks)

    def _extract(label):
        if not text:
            return "N/A"
        pattern = rf"(?im){re.escape(label)}\s*[:=]\s*([^\n]+)"
        match = re.search(pattern, text)
        return match.group(1).strip() if match else "N/A"

    return {
        "gross_return": _extract("gross return"),
        "cost_sensitivity": _extract("cost sensitivity"),
        "break_even_cost": _extract("break-even cost"),
        "drawdown": _extract("drawdown"),
        "sharpe_ratio": _extract("sharpe ratio"),
    }


def build_dashboard_view_model(payload):
    latest_run = payload.get("latest_run") or {}
    latest_success = payload.get("latest_success") or {}
    latest_signal = payload.get("latest_signal") or {}
    latest_account = payload.get("latest_account") or {}
    portfolio_history = payload.get("portfolio_history") or []

    bot_health_text, bot_health_style = classify_bot_health(latest_run)
    market_text, market_style = classify_market_status(latest_signal)
    signal_text, signal_style = classify_signal(latest_signal.get("generated_signal"))
    daily_pl, total_pl = calculate_daily_and_total_pl(portfolio_history)

    return {
        "bot_health": {"label": bot_health_text, "style": bot_health_style},
        "market_status": {"label": market_text, "style": market_style},
        "signal": {"label": signal_text, "style": signal_style},
        "last_successful_run": format_timestamp_eastern(latest_success.get("run_timestamp")),
        "trading_mode": friendly_status_text(latest_run.get("trading_mode"), "Paper"),
        "review_required": bool(_as_bool(latest_run.get("review_required"))),
        "latest_stop_reason": _safe_text(latest_run.get("stop_reason"), "Waiting for the next market-hours run"),
        "latest_safe_error_message": _safe_text(latest_run.get("safe_error_message"), ""),
        "last_run_timestamp": format_timestamp_eastern(latest_run.get("run_timestamp")),
        "last_successful_run_timestamp": format_timestamp_eastern(latest_success.get("run_timestamp")),
        "daily_submitted_order_count": int(latest_signal.get("daily_submitted_order_count") or 0),
        "daily_submitted_notional": _as_float(latest_signal.get("daily_submitted_notional"), 0.0),
        "latest_spy_price": market_display_value(latest_signal.get("latest_price"), latest_signal),
        "latest_market_data_timestamp": format_timestamp_eastern(latest_signal.get("latest_market_data_timestamp"), "Waiting for next market-hours run"),
        "short_moving_average": market_display_value(latest_signal.get("short_moving_average"), latest_signal),
        "long_moving_average": market_display_value(latest_signal.get("long_moving_average"), latest_signal),
        "generated_signal": signal_text,
        "trade_or_skip_reason": _safe_text(latest_signal.get("trade_or_skip_reason"), "Waiting for next market-hours run"),
        "cooldown_status": friendly_status_text(latest_signal.get("cooldown_status"), "Unknown"),
        "duplicate_signal_status": friendly_status_text(latest_signal.get("duplicate_signal_status"), "Unknown"),
        "pending_order_status": friendly_status_text(latest_signal.get("pending_order_status"), "Unknown"),
        "daily_loss_stop_status": friendly_status_text(latest_signal.get("daily_loss_stop_status"), "Unknown"),
        "portfolio_value": _as_float(latest_account.get("portfolio_value"), 0.0),
        "cash": _as_float(latest_account.get("cash"), 0.0),
        "buying_power": _as_float(latest_account.get("buying_power"), 0.0),
        "unrealized_paper_pl": _as_float(latest_account.get("unrealized_paper_pl"), 0.0),
        "realized_paper_pl": _as_float(latest_account.get("realized_paper_pl"), 0.0),
        "open_positions": int(latest_account.get("open_positions") or 0),
        "account_status": friendly_status_text(latest_account.get("account_status"), "Unknown"),
        "today_pl": daily_pl,
        "total_pl": total_pl,
        "ma_distance": moving_average_distance(latest_signal.get("short_moving_average"), latest_signal.get("long_moving_average")),
    }


def _fetch_payload_uncached(database_url: str | None):
    db = MonitoringDatabase(database_url=database_url or os.getenv("DATABASE_URL"))
    payload = {
        "db_connected": db.enabled,
        "latest_run": {},
        "latest_success": {},
        "latest_signal": {},
        "latest_account": {},
        "recent_runs": [],
        "recent_orders": [],
        "portfolio_history": [],
        "signal_history": [],
        "order_count_by_day": [],
    }

    if not db.enabled:
        return payload

    db.ensure_schema()
    payload["latest_run"] = db.fetch_latest_bot_run() or {}
    payload["latest_success"] = db.fetch_latest_successful_run() or {}
    payload["latest_signal"] = db.fetch_latest_signal_snapshot() or {}
    payload["latest_account"] = db.fetch_latest_account_snapshot() or {}
    payload["recent_runs"] = db.fetch_recent_runs(limit=100)
    payload["recent_orders"] = db.fetch_recent_order_events(limit=250)
    payload["portfolio_history"] = list(reversed(db.fetch_portfolio_history(limit=500)))
    payload["signal_history"] = list(reversed(db.fetch_signal_history(limit=500)))
    payload["order_count_by_day"] = list(reversed(db.fetch_order_count_by_day(limit=365)))
    return payload


def clear_dashboard_cache():
    if st is not None and hasattr(st, "cache_data"):
        st.cache_data.clear()


def apply_dashboard_css():
    st.markdown(
        """
        <style>
        .stApp {
            background: radial-gradient(circle at 10% 10%, #1a1e2e 0%, #0e1119 48%, #090c13 100%);
            color: #e8eefc;
        }
        .dq-logo {
            font-size: 0.85rem;
            letter-spacing: 0.2rem;
            font-weight: 700;
            color: #44a3ff;
            margin-bottom: 0.4rem;
        }
        .dq-subtitle {
            color: #a9b7d0;
            margin-top: -0.6rem;
            margin-bottom: 1rem;
        }
        .dq-badge {
            display: inline-block;
            border-radius: 999px;
            padding: 0.35rem 0.7rem;
            background: rgba(33, 196, 107, 0.25);
            border: 1px solid rgba(33, 196, 107, 0.55);
            color: #72e4ab;
            font-weight: 700;
            box-shadow: 0 0 12px rgba(33, 196, 107, 0.45);
        }
        .dq-card {
            background: linear-gradient(130deg, rgba(22, 28, 42, 0.72), rgba(15, 21, 34, 0.62));
            border: 1px solid rgba(100, 130, 200, 0.22);
            box-shadow: 0 6px 22px rgba(0, 0, 0, 0.28);
            border-radius: 16px;
            padding: 0.95rem 1.05rem;
            margin-bottom: 0.7rem;
            animation: dqFadeIn 0.35s ease-in;
            transition: all 0.18s ease;
        }
        .dq-card:hover {
            transform: translateY(-1px);
            border-color: rgba(100, 160, 255, 0.38);
        }
        .dq-label {
            color: #a8b4ca;
            font-size: 0.83rem;
            letter-spacing: 0.02rem;
        }
        .dq-value {
            color: #f4f8ff;
            font-size: 1.06rem;
            font-weight: 600;
        }
        .dq-pulse {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 0.4rem;
            animation: dqPulse 1.8s infinite;
        }
        .dq-alert {
            border-radius: 12px;
            border: 1px solid rgba(255, 92, 92, 0.6);
            background: rgba(255, 92, 92, 0.12);
            padding: 0.7rem 0.85rem;
            margin-bottom: 0.8rem;
            color: #ffd2d2;
        }
        @keyframes dqPulse {
            0% { box-shadow: 0 0 0 0 rgba(68, 163, 255, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(68, 163, 255, 0); }
            100% { box-shadow: 0 0 0 0 rgba(68, 163, 255, 0); }
        }
        @keyframes dqFadeIn {
            from { opacity: 0.0; transform: translateY(4px); }
            to { opacity: 1.0; transform: translateY(0px); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric_card(container, label, value, style_key="neutral"):
    color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
    container.markdown(
        f"<div class='dq-card'><div class='dq-label'>{label}</div><div class='dq-value' style='color:{color};'>{value}</div></div>",
        unsafe_allow_html=True,
    )


def _safe_plotly_chart(fig, fallback_message):
    if fig is None:
        st.info(fallback_message)
        return
    try:
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.info(fallback_message)


def render_header(payload):
    now_eastern = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %I:%M:%S %p ET")
    if "dashboard_last_refresh" not in st.session_state:
        st.session_state["dashboard_last_refresh"] = datetime.now(timezone.utc).isoformat()

    left, mid, right = st.columns([6, 2, 2])
    left.markdown("<div class='dq-logo'>DEAL QUANT</div>", unsafe_allow_html=True)
    left.title("DEAL QUANT COMMAND CENTER")
    left.markdown("<div class='dq-subtitle'>Automated Paper Trading Intelligence</div>", unsafe_allow_html=True)

    online_color = STATUS_COLORS["healthy"] if payload.get("db_connected") else STATUS_COLORS["error"]
    online_text = "ONLINE" if payload.get("db_connected") else "OFFLINE"
    mid.markdown(
        f"<span class='dq-pulse' style='background:{online_color};'></span><span class='dq-value'>{online_text}</span>",
        unsafe_allow_html=True,
    )
    mid.markdown("<div class='dq-badge'>PAPER TRADING</div>", unsafe_allow_html=True)

    right.metric("Eastern Time", now_eastern)
    right.metric("Last Refresh", format_timestamp_eastern(st.session_state.get("dashboard_last_refresh")))
    if right.button("Refresh", help="Refresh dashboard data only"):
        clear_dashboard_cache()
        st.session_state["dashboard_last_refresh"] = datetime.now(timezone.utc).isoformat()
        st.rerun()


def render_alert_banner(payload, view):
    alert_messages = []
    if view.get("review_required"):
        alert_messages.append("Review required is active")
    if not payload.get("db_connected"):
        alert_messages.append("Database disconnected")
    if view.get("bot_health", {}).get("style") == "error":
        alert_messages.append("Bot status reports an error")
    if "stale" in str(view.get("trade_or_skip_reason", "")).lower():
        alert_messages.append("Market data appears stale")
    if "loss limit" in str(view.get("latest_stop_reason", "")).lower() or "loss" in str(view.get("daily_loss_stop_status", "")).lower():
        alert_messages.append("Daily loss stop condition detected")

    if alert_messages:
        st.markdown(
            "<div class='dq-alert'><strong>System Alert:</strong> " + " | ".join(alert_messages) + "</div>",
            unsafe_allow_html=True,
        )


def render_sidebar(payload):
    with st.sidebar:
        st.subheader("DEAL QUANT")
        st.markdown("Read-only controls")
        st.write(f"Environment: {friendly_status_text(os.getenv('TRADING_MODE', 'PAPER'))}")
        st.write(f"Database connected: {'yes' if payload.get('db_connected') else 'no'}")
        st.write(f"Last data refresh: {format_timestamp_eastern(st.session_state.get('dashboard_last_refresh'))}")
        st.caption("No trading controls")
        st.caption(f"Dashboard version {DASHBOARD_VERSION}")


def render_overview_page(payload, view):
    top = st.columns(4)
    _metric_card(top[0], "Account Equity", format_currency(view["portfolio_value"]), "neutral")
    _metric_card(top[1], "Today's Paper P/L", format_currency(view["today_pl"]), "buy" if view["today_pl"] >= 0 else "sell")
    _metric_card(top[2], "Total Paper P/L", format_currency(view["total_pl"]), "buy" if view["total_pl"] >= 0 else "sell")
    _metric_card(top[3], "Open Positions", view["open_positions"], "neutral")

    row2 = st.columns(4)
    _metric_card(row2[0], "Cash", format_currency(view["cash"]), "neutral")
    _metric_card(row2[1], "Buying Power", format_currency(view["buying_power"]), "neutral")
    _metric_card(row2[2], "Current SPY Signal", view["generated_signal"], view["signal"]["style"])
    _metric_card(row2[3], "Market", view["market_status"]["label"], view["market_status"]["style"])

    row3 = st.columns(4)
    _metric_card(row3[0], "Bot Status", view["bot_health"]["label"], view["bot_health"]["style"])
    _metric_card(row3[1], "Last Successful Run", view["last_successful_run"], "neutral")
    _metric_card(row3[2], "Daily Orders Used", f"{view['daily_submitted_order_count']} / {MAX_DAILY_ORDERS}", "warning")
    _metric_card(row3[3], "Daily Notional Used", f"{format_currency(view['daily_submitted_notional'])} / {format_currency(MAX_DAILY_SUBMITTED_NOTIONAL)}", "warning")

    if payload.get("portfolio_history"):
        trend_vals = [item.get("portfolio_value") for item in payload["portfolio_history"]][-24:]
        if trend_vals:
            st.caption("Equity sparkline")
            st.line_chart({"equity": trend_vals})


def render_strategy_page(payload, view):
    signal_col, detail_col = st.columns([1.8, 2.2])
    signal_col.markdown("### Strategy Signal")
    _metric_card(signal_col, "Current Signal", view["generated_signal"], view["signal"]["style"])
    ma_state = "Bullish crossover" if view["ma_distance"] > 0 else "Bearish crossover" if view["ma_distance"] < 0 else "Neutral crossover"
    _metric_card(signal_col, "Crossover State", ma_state, "buy" if view["ma_distance"] > 0 else "sell" if view["ma_distance"] < 0 else "hold")

    detail_col.markdown("### Live Inputs")
    detail_col.markdown(f"**Latest SPY price:** {view['latest_spy_price']}")
    detail_col.markdown(f"**Short moving average:** {view['short_moving_average']}")
    detail_col.markdown(f"**Long moving average:** {view['long_moving_average']}")
    detail_col.markdown(f"**MA distance:** {format_currency(view['ma_distance'])}")
    detail_col.markdown(f"**Latest market-data timestamp:** {view['latest_market_data_timestamp']}")
    detail_col.markdown(f"**Trade/skip reason:** {view['trade_or_skip_reason']}")

    signal_history = payload.get("signal_history") or []
    if signal_history:
        if px is not None:
            fig = px.scatter(signal_history, x="snapshot_timestamp", y="generated_signal", title="Signal History")
            _safe_plotly_chart(fig, "Signal history is temporarily unavailable")
        else:
            st.dataframe(signal_history)
    else:
        st.info("Waiting for next market-hours run")


def render_account_page(payload, view):
    acc_cols = st.columns(3)
    acc_cols[0].metric("Portfolio Value", format_currency(view["portfolio_value"]))
    acc_cols[0].metric("Cash", format_currency(view["cash"]))
    acc_cols[1].metric("Buying Power", format_currency(view["buying_power"]))
    acc_cols[1].metric("Unrealized P/L", format_currency(view["unrealized_paper_pl"]))
    acc_cols[2].metric("Realized P/L", format_currency(view["realized_paper_pl"]))
    acc_cols[2].metric("Account Status", view["account_status"])

    latest_account = payload.get("latest_account") or {}
    positions = latest_account.get("positions") if isinstance(latest_account, dict) else None
    if isinstance(positions, list) and positions:
        st.markdown("### Open Positions")
        for position in positions:
            pcols = st.columns(2)
            pnl = _as_float(position.get("unrealized_pl"), 0.0)
            style = "buy" if pnl >= 0 else "sell"
            _metric_card(
                pcols[0],
                str(position.get("symbol", "Position")),
                f"Qty {position.get('quantity', 'N/A')} | MV {format_currency(position.get('market_value', 0.0))}",
                style,
            )
            _metric_card(
                pcols[1],
                "Entry / Current / Unrealized",
                f"{format_currency(position.get('average_entry_price', 0.0))} / {format_currency(position.get('current_price', 0.0))} / {format_currency(pnl)}",
                style,
            )
    else:
        st.info("No open positions")


def render_orders_page(payload):
    rows = build_order_rows(payload.get("recent_orders") or [])
    if not rows:
        st.info("No paper orders yet")
        return

    filter_cols = st.columns(4)
    submitted_filter = filter_cols[0].selectbox("Submitted", ["All", "Submitted", "Not Submitted"])
    signal_filter = filter_cols[1].selectbox("Signal", ["All", "BUY", "HOLD", "SELL"])
    stop_reason_filter = filter_cols[2].text_input("Stop reason contains", "")
    date_filter = filter_cols[3].text_input("Date contains", "")

    filtered = rows
    if submitted_filter != "All":
        want = submitted_filter == "Submitted"
        filtered = [row for row in filtered if bool(row["Submitted"]) == want]
    if signal_filter != "All":
        filtered = [row for row in filtered if row["Signal"] == signal_filter]
    if stop_reason_filter.strip():
        q = stop_reason_filter.strip().lower()
        filtered = [row for row in filtered if q in str(row.get("Stop Reason", "")).lower()]
    if date_filter.strip():
        qd = date_filter.strip().lower()
        filtered = [row for row in filtered if qd in str(row.get("Timestamp", "")).lower()]

    if not filtered:
        st.info("No paper orders yet")
        return

    st.dataframe(filtered)


def render_performance_page(payload):
    history = payload.get("portfolio_history") or []
    signal_history = payload.get("signal_history") or []
    order_count_by_day = payload.get("order_count_by_day") or []

    if len(history) < 2:
        st.info("Waiting for the next market-hours run")
        return

    values = [_as_float(item.get("portfolio_value"), 0.0) for item in history]
    unrealized = [_as_float(item.get("unrealized_paper_pl"), 0.0) for item in history]
    daily_pl = [values[i] - values[i - 1] if i > 0 else 0.0 for i in range(len(values))]
    cumulative = [v - values[0] for v in values]
    running_max = []
    drawdown = []
    max_val = values[0]
    for v in values:
        max_val = max(max_val, v)
        running_max.append(max_val)
        dd = 0.0 if max_val == 0 else ((v - max_val) / max_val) * 100.0
        drawdown.append(dd)

    st.subheader("Portfolio Value")
    if px is not None:
        _safe_plotly_chart(px.line(x=list(range(len(values))), y=values, labels={"x": "Samples", "y": "Portfolio Value"}), "Portfolio chart unavailable")
    else:
        st.line_chart({"portfolio_value": values})

    st.subheader("Daily Paper P/L")
    st.line_chart({"daily_pl": daily_pl})

    st.subheader("Cumulative P/L")
    st.line_chart({"cumulative_pl": cumulative})

    if len(values) > 3:
        st.subheader("Drawdown")
        st.line_chart({"drawdown_percent": drawdown})
    else:
        st.info("Not enough data for drawdown chart")

    if signal_history:
        dist = {"BUY": 0, "HOLD": 0, "SELL": 0}
        for item in signal_history:
            dist[normalize_signal(item.get("generated_signal"))] = dist.get(normalize_signal(item.get("generated_signal")), 0) + 1
        st.subheader("Signal Distribution")
        st.bar_chart(dist)
    else:
        st.info("No signal history yet")

    if order_count_by_day:
        st.subheader("Orders Per Day")
        st.bar_chart({"orders_per_day": [item.get("submitted_count", 0) for item in order_count_by_day]})
    else:
        st.info("No paper orders yet")

    wins = len([x for x in daily_pl if x > 0])
    losses = len([x for x in daily_pl if x < 0])
    if wins + losses >= 3:
        st.subheader("Win/Loss Summary")
        st.write(f"Wins: {wins} | Losses: {losses}")
    else:
        st.info("Not enough completed trade history for win/loss summary")


def render_system_health_page(payload, view):
    cols = st.columns(3)
    _metric_card(cols[0], "Bot Status", view["bot_health"]["label"], view["bot_health"]["style"])
    _metric_card(cols[1], "Database", "Connected" if payload.get("db_connected") else "Disconnected", "healthy" if payload.get("db_connected") else "error")
    _metric_card(cols[2], "Alpaca Paper Authentication", "Active" if view["account_status"].lower() == "active" else view["account_status"], "healthy" if view["account_status"].lower() == "active" else "warning")

    row2 = st.columns(3)
    _metric_card(row2[0], "Railway Worker", "Healthy" if payload.get("latest_run") else "Waiting", "healthy" if payload.get("latest_run") else "warning")
    _metric_card(row2[1], "State Persistence", "Available" if payload.get("db_connected") else "Unavailable", "healthy" if payload.get("db_connected") else "error")
    _metric_card(row2[2], "Review Required", "Yes" if view["review_required"] else "No", "error" if view["review_required"] else "healthy")

    st.markdown("### Latest Diagnostics")
    st.markdown(f"**Last successful run:** {view['last_successful_run']}")
    st.markdown(f"**Latest safe error:** {view['latest_safe_error_message'] or 'None'}")
    st.markdown(f"**Market-data freshness:** {view['latest_market_data_timestamp']}")

    st.markdown("### Activity Feed")
    events = build_run_history_rows(payload.get("recent_runs"), payload.get("recent_orders"))[:8]
    if events:
        for event in events:
            st.markdown(
                f"<div class='dq-card'><div class='dq-label'>{event['Timestamp']}</div><div class='dq-value'>{event['Order Status']} | {event['Stop Reason']}</div></div>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No monitoring records available yet")


def render_research_page():
    st.markdown("### RESEARCH ONLY — NOT CONNECTED TO PAPER EXECUTION")
    metrics = load_research_summary()
    cols = st.columns(5)
    _metric_card(cols[0], "Gross Return", metrics["gross_return"], "neutral")
    _metric_card(cols[1], "Cost Sensitivity", metrics["cost_sensitivity"], "warning")
    _metric_card(cols[2], "Break-even Cost", metrics["break_even_cost"], "warning")
    _metric_card(cols[3], "Drawdown", metrics["drawdown"], "sell")
    _metric_card(cols[4], "Sharpe Ratio", metrics["sharpe_ratio"], "healthy")


def render_dashboard(database_url: str | None = None):
    if st is None:
        raise RuntimeError("streamlit is required to run the dashboard")

    enforce_paper_mode(os.getenv("TRADING_MODE", "PAPER"))
    st.set_page_config(page_title="DEAL QUANT COMMAND CENTER", layout="wide")
    apply_dashboard_css()

    expected_password = os.getenv("DASHBOARD_PASSWORD", "")
    provided_password = st.text_input("Dashboard Password", type="password")
    if not check_dashboard_password(provided_password, expected_password):
        st.warning("Access denied")
        st.stop()

    try:
        payload = _cached_payload(database_url or os.getenv("DATABASE_URL"))
    except Exception as exc:
        st.info(f"Dashboard data unavailable right now: {_safe_text(exc, 'temporary data error')}")
        payload = {
            "db_connected": False,
            "latest_run": {},
            "latest_success": {},
            "latest_signal": {},
            "latest_account": {},
            "recent_runs": [],
            "recent_orders": [],
            "portfolio_history": [],
            "signal_history": [],
            "order_count_by_day": [],
        }

    view = build_dashboard_view_model(payload)
    render_header(payload)
    render_alert_banner(payload, view)
    render_sidebar(payload)

    tabs = st.tabs(["Overview", "Strategy", "Account", "Orders", "Performance", "System Health", "Research"])

    with tabs[0]:
        render_overview_page(payload, view)
    with tabs[1]:
        render_strategy_page(payload, view)
    with tabs[2]:
        render_account_page(payload, view)
    with tabs[3]:
        render_orders_page(payload)
    with tabs[4]:
        render_performance_page(payload)
    with tabs[5]:
        render_system_health_page(payload, view)
    with tabs[6]:
        render_research_page()

    for message in empty_state_messages(payload, view):
        st.info(message)


def main():
    render_dashboard()


if __name__ == "__main__":
    main()
