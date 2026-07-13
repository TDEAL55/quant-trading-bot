import os
import re
from datetime import datetime, timedelta, time, timezone
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

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

from monitoring_db import MonitoringDatabase
from dashboard_data import fetch_dashboard_payload
from dashboard_exports import export_daily_activity, export_performance_summary, export_sanitized_orders, export_signal_history, export_system_health
from dashboard_charts import build_line_chart, build_market_chart
from dashboard_components import build_palette, status_style
from dashboard_models import build_normalized_view_model
from dashboard_sanitization import sanitize_identifier, sanitize_text
from dashboard_status import classify_market_clock, format_est


MAX_DAILY_ORDERS = 3
MAX_DAILY_SUBMITTED_NOTIONAL = 30.0
DASHBOARD_VERSION = "v2.0"
EASTERN_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_ET = time(9, 30)
MARKET_CLOSE_ET = time(16, 0)

STATUS_COLORS = {
    "healthy": "#21c46b",
    "warning": "#f1c75b",
    "error": "#ff5c5c",
    "neutral": "#44a3ff",
    "buy": "#21c46b",
    "hold": "#8a96a8",
    "sell": "#ff5c5c",
}

THEMES = {
    "Midnight Blue": {
        "bg": "radial-gradient(circle at 10% 10%, #1a1e2e 0%, #0e1119 48%, #090c13 100%)",
        "panel": "linear-gradient(130deg, rgba(22, 28, 42, 0.78), rgba(15, 21, 34, 0.66))",
        "text": "#e8eefc",
        "subtle": "#a8b4ca",
        "accent": "#44a3ff",
        "grid": "rgba(68, 163, 255, 0.10)",
    },
    "Black Terminal": {
        "bg": "radial-gradient(circle at 20% 0%, #121212 0%, #080808 58%, #040404 100%)",
        "panel": "linear-gradient(130deg, rgba(14, 14, 14, 0.84), rgba(8, 8, 8, 0.74))",
        "text": "#e8f0e8",
        "subtle": "#9ab29a",
        "accent": "#2ecb70",
        "grid": "rgba(46, 203, 112, 0.10)",
    },
}


def _theme_palette(theme_name: str):
    return THEMES.get(theme_name, THEMES["Midnight Blue"])


def _is_market_day(dt_et: datetime) -> bool:
    return dt_et.weekday() < 5


def _next_market_open(dt_et: datetime) -> datetime:
    candidate = dt_et
    if _is_market_day(candidate) and candidate.time() < MARKET_OPEN_ET:
        return candidate.replace(hour=MARKET_OPEN_ET.hour, minute=MARKET_OPEN_ET.minute, second=0, microsecond=0)

    candidate = candidate + timedelta(days=1)
    while not _is_market_day(candidate):
        candidate += timedelta(days=1)
    return candidate.replace(hour=MARKET_OPEN_ET.hour, minute=MARKET_OPEN_ET.minute, second=0, microsecond=0)


def _next_market_close(dt_et: datetime) -> datetime:
    return dt_et.replace(hour=MARKET_CLOSE_ET.hour, minute=MARKET_CLOSE_ET.minute, second=0, microsecond=0)


def format_countdown(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_market_clock(now_et: datetime | None = None) -> dict[str, Any]:
    clock = classify_market_clock(now_et)
    return {
        "label": clock.get("label", "MARKET CLOSED"),
        "style": "healthy" if clock.get("is_open") else "neutral",
        "is_open": bool(clock.get("is_open")),
        "countdown_label": clock.get("countdown_label", "Opens in"),
        "countdown": clock.get("countdown_text", "00:00:00"),
        "time_text": clock.get("timestamp", format_timestamp_eastern(now_et)),
    }


def _parse_iso(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _timeframe_window(timeframe: str) -> timedelta:
    if timeframe == "1D":
        return timedelta(days=1)
    if timeframe == "5D":
        return timedelta(days=5)
    if timeframe == "1M":
        return timedelta(days=31)
    return timedelta(days=93)


def _bucket_seconds(timeframe: str) -> int:
    if timeframe == "1D":
        return 5 * 60
    if timeframe == "5D":
        return 30 * 60
    if timeframe == "1M":
        return 2 * 60 * 60
    return 4 * 60 * 60


def build_price_points(signal_history, timeframe="1D"):
    points = []
    for row in signal_history or []:
        price = row.get("latest_price")
        ts = _parse_iso(row.get("snapshot_timestamp"))
        if ts is None:
            continue
        price_number = _as_float(price, None)
        if price_number is None:
            continue
        points.append(
            {
                "timestamp": ts,
                "price": price_number,
                "short_ma": _as_float(row.get("short_moving_average"), None),
                "long_ma": _as_float(row.get("long_moving_average"), None),
                "signal": normalize_signal(row.get("generated_signal", "HOLD")),
            }
        )

    if not points:
        return []

    points.sort(key=lambda x: x["timestamp"])
    end_time = points[-1]["timestamp"]
    window = _timeframe_window(timeframe)
    start_time = end_time - window
    filtered = [item for item in points if item["timestamp"] >= start_time]
    return filtered if filtered else points


def build_ohlc_series(price_points, timeframe="1D"):
    if not price_points:
        return []
    bucket_seconds = _bucket_seconds(timeframe)
    buckets = {}
    for point in price_points:
        ts = point["timestamp"]
        epoch = int(ts.timestamp())
        bucket_epoch = epoch - (epoch % bucket_seconds)
        bucket = buckets.setdefault(
            bucket_epoch,
            {
                "timestamp": datetime.fromtimestamp(bucket_epoch, tz=timezone.utc),
                "open": point["price"],
                "high": point["price"],
                "low": point["price"],
                "close": point["price"],
                "short_ma": point.get("short_ma"),
                "long_ma": point.get("long_ma"),
            },
        )
        bucket["high"] = max(bucket["high"], point["price"])
        bucket["low"] = min(bucket["low"], point["price"])
        bucket["close"] = point["price"]
        if point.get("short_ma") is not None:
            bucket["short_ma"] = point["short_ma"]
        if point.get("long_ma") is not None:
            bucket["long_ma"] = point["long_ma"]

    candles = [buckets[key] for key in sorted(buckets.keys())]
    for candle in candles:
        candle["timestamp"] = candle["timestamp"].astimezone(EASTERN_TZ)
    return candles


def build_signal_strength(ma_distance_value):
    distance = abs(_as_float(ma_distance_value, 0.0))
    max_distance = 2.0
    strength = min(distance / max_distance, 1.0)
    return {
        "value": strength,
        "percent": int(round(strength * 100)),
        "description": "Informational only - based on moving-average separation",
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
    return sanitize_text(value, fallback)


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


def format_timestamp_eastern(value, fallback="Waiting for the next market-hours update"):
    return format_est(value, fallback)


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
        return "Waiting for the next market-hours update"
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
                "Stop Reason": _safe_text(run.get("stop_reason", ""), "Waiting for the next market-hours update"),
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
    normalized_vm = build_normalized_view_model(payload)
    latest_run = payload.get("latest_run") or {}
    latest_success = payload.get("latest_success") or {}
    latest_signal = payload.get("latest_signal") or {}
    latest_account = payload.get("latest_account") or {}
    portfolio_history = payload.get("portfolio_history") or []

    bot_health_text, bot_health_style = classify_bot_health(latest_run)
    market_text, market_style = classify_market_status(latest_signal)
    signal_text, signal_style = classify_signal(latest_signal.get("generated_signal"))
    daily_pl, total_pl = calculate_daily_and_total_pl(portfolio_history)
    previous_portfolio = _as_float(portfolio_history[-2].get("portfolio_value"), 0.0) if len(portfolio_history) >= 2 else None

    signal_history = payload.get("signal_history") or []
    recent_prices = [
        _as_float(item.get("latest_price"), None)
        for item in signal_history
        if _as_float(item.get("latest_price"), None) is not None
    ]
    previous_price = recent_prices[-2] if len(recent_prices) >= 2 else None

    mission_signal_counts = {"BUY": 0, "HOLD": 0, "SELL": 0}
    for item in signal_history:
        normalized = normalize_signal(item.get("generated_signal"))
        mission_signal_counts[normalized] = mission_signal_counts.get(normalized, 0) + 1

    recent_orders = payload.get("recent_orders") or []
    blocked_orders = len([o for o in recent_orders if not _as_bool(o.get("submitted"))])
    submitted_orders = len([o for o in recent_orders if _as_bool(o.get("submitted"))])
    daily_notional_used = _as_float(latest_signal.get("daily_submitted_notional"), 0.0)
    remaining_order_capacity = max(MAX_DAILY_ORDERS - int(latest_signal.get("daily_submitted_order_count") or 0), 0)
    open_position_value = max(_as_float(latest_account.get("portfolio_value"), 0.0) - _as_float(latest_account.get("cash"), 0.0), 0.0)

    return {
        "bot_health": {"label": bot_health_text, "style": bot_health_style},
        "market_status": {"label": market_text, "style": market_style},
        "signal": {"label": signal_text, "style": signal_style},
        "last_successful_run": format_timestamp_eastern(latest_success.get("run_timestamp")),
        "trading_mode": friendly_status_text(latest_run.get("trading_mode"), "Paper"),
        "review_required": bool(_as_bool(latest_run.get("review_required"))),
        "latest_stop_reason": _safe_text(latest_run.get("stop_reason"), "Waiting for the next market-hours update"),
        "latest_safe_error_message": _safe_text(latest_run.get("safe_error_message"), ""),
        "last_run_timestamp": format_timestamp_eastern(latest_run.get("run_timestamp")),
        "last_successful_run_timestamp": format_timestamp_eastern(latest_success.get("run_timestamp")),
        "daily_submitted_order_count": int(latest_signal.get("daily_submitted_order_count") or 0),
        "daily_submitted_notional": _as_float(latest_signal.get("daily_submitted_notional"), 0.0),
        "latest_spy_price": market_display_value(latest_signal.get("latest_price"), latest_signal),
        "latest_market_data_timestamp": format_timestamp_eastern(latest_signal.get("latest_market_data_timestamp"), "Waiting for the next market-hours update"),
        "short_moving_average": market_display_value(latest_signal.get("short_moving_average"), latest_signal),
        "long_moving_average": market_display_value(latest_signal.get("long_moving_average"), latest_signal),
        "generated_signal": signal_text,
        "trade_or_skip_reason": _safe_text(latest_signal.get("trade_or_skip_reason"), "Waiting for the next market-hours update"),
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
        "previous_portfolio_value": previous_portfolio,
        "previous_spy_price": previous_price,
        "signal_strength": build_signal_strength(moving_average_distance(latest_signal.get("short_moving_average"), latest_signal.get("long_moving_average"))),
        "open_position_value": open_position_value,
        "mission_summary": {
            "runs_completed": len(payload.get("recent_runs") or []),
            "signal_counts": mission_signal_counts,
            "submitted_orders": submitted_orders,
            "blocked_orders": blocked_orders,
            "daily_notional_used": daily_notional_used,
            "remaining_order_capacity": remaining_order_capacity,
            "latest_stop_reason": _safe_text(latest_run.get("stop_reason"), "Waiting for the next market-hours update"),
        },
        "risk_matrix": normalized_vm.risk_matrix,
        "operations": normalized_vm.operations,
        "intelligence": normalized_vm.intelligence,
        "freshness": normalized_vm.freshness,
    }


def _fetch_payload_uncached(database_url: str | None):
    try:
        payload = fetch_dashboard_payload(database_url or os.getenv("DATABASE_URL"), database_factory=MonitoringDatabase)
    except Exception:
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
    return payload


def clear_dashboard_cache():
    if st is not None and hasattr(st, "cache_data"):
        st.cache_data.clear()


def apply_dashboard_css(theme_name="Midnight Blue"):
    palette = build_palette(theme_name)
    st.markdown(
        f"""
        <style>
        .stApp {{
            background: {palette.page_bg};
            color: {palette.primary_text};
        }}
        .main .block-container {{
            padding-top: 1.2rem;
            max-width: 1280px;
        }}
        .dq-logo {{
            font-size: 1.1rem;
            letter-spacing: 0.28rem;
            font-weight: 800;
            color: {palette.positive};
            margin-bottom: 0.3rem;
            text-transform: uppercase;
            text-shadow: 0 0 14px {palette.accent_glow};
        }}
        .dq-subtitle {{
            color: {palette.secondary_text};
            margin-top: -0.4rem;
            margin-bottom: 0.8rem;
            font-size: 0.82rem;
            letter-spacing: 0.12rem;
        }}
        .dq-theme-badge {{
            display: inline-block;
            border-radius: 999px;
            padding: 0.26rem 0.68rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid {palette.border};
            color: {palette.primary_text};
            font-weight: 600;
            font-size: 0.72rem;
        }}
        .dq-ticker {{
            position: relative;
            overflow: hidden;
            border-radius: 12px;
            border: 1px solid {palette.border};
            background: {palette.panel_bg};
            margin-bottom: 0.85rem;
            padding: 0.45rem 0;
        }}
        .dq-ticker-track {{
            display: inline-flex;
            gap: 1.8rem;
            align-items: center;
            padding-left: 1rem;
            white-space: nowrap;
            animation: dqTickerMove 22s linear infinite;
        }}
        .dq-ticker-item {{
            color: {palette.primary_text};
            font-size: 0.82rem;
            letter-spacing: 0.01rem;
        }}
        .dq-ticker-label {{
            color: {palette.secondary_text};
            margin-right: 0.25rem;
        }}
        .dq-card {{
            background: {palette.elevated_bg};
            border: 1px solid {palette.border};
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.22);
            border-radius: 16px;
            padding: 0.95rem 1.05rem;
            margin-bottom: 0.75rem;
            animation: dqFadeIn 0.32s ease-in;
        }}
        .dq-hero {{
            position: relative;
            overflow: hidden;
            background: linear-gradient(135deg, rgba(18, 34, 56, 0.62), rgba(8, 16, 30, 0.38));
        }}
        .dq-hero::after {{
            content: "";
            position: absolute;
            inset: 0;
            background-image: linear-gradient(to right, {palette.border} 1px, transparent 1px), linear-gradient(to bottom, {palette.border} 1px, transparent 1px);
            background-size: 30px 30px;
            opacity: 0.35;
            pointer-events: none;
        }}
        .dq-hero-value {{
            font-size: 2.05rem;
            font-weight: 800;
            margin-top: 0.3rem;
            color: {palette.primary_text};
        }}
        .dq-label {{
            color: {palette.secondary_text};
            font-size: 0.8rem;
            letter-spacing: 0.06rem;
            text-transform: uppercase;
        }}
        .dq-value {{
            color: {palette.primary_text};
            font-size: 1.06rem;
            font-weight: 650;
        }}
        .dq-badge {{
            display: inline-block;
            border-radius: 999px;
            padding: 0.35rem 0.7rem;
            background: rgba(33, 196, 107, 0.22);
            border: 1px solid rgba(33, 196, 107, 0.48);
            color: {palette.positive};
            font-weight: 700;
        }}
        .dq-pulse {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 0.4rem;
            animation: dqPulse 1.8s infinite;
        }}
        .dq-badge-neutral {{
            background: rgba(138, 150, 168, 0.22);
            border-color: rgba(138, 150, 168, 0.55);
            color: {palette.neutral};
        }}
        .dq-orb {{
            width: 114px;
            height: 114px;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 1.1rem;
            margin: 0 auto;
            box-shadow: inset 0 0 30px rgba(255, 255, 255, 0.08), 0 0 20px rgba(0, 0, 0, 0.3);
        }}
        .dq-market-indicator {{
            width: 10px;
            height: 10px;
            border-radius: 999px;
            display: inline-block;
            margin-right: 0.45rem;
        }}
        .dq-market-open {{
            background: {palette.positive};
            animation: dqPulse 1.7s infinite;
        }}
        .dq-market-closed {{
            background: {palette.neutral};
        }}
        .dq-alert {{
            border-radius: 12px;
            border: 1px solid rgba(255, 92, 92, 0.6);
            background: rgba(255, 92, 92, 0.12);
            padding: 0.7rem 0.85rem;
            margin-bottom: 0.8rem;
            color: #ffd2d2;
        }}
        .dq-skeleton {{
            border-radius: 10px;
            height: 54px;
            margin-bottom: 0.55rem;
            background: linear-gradient(90deg, rgba(255,255,255,0.06), rgba(255,255,255,0.12), rgba(255,255,255,0.06));
            background-size: 200% 100%;
            animation: dqShimmer 1.3s linear infinite;
        }}
        .dq-timeline-item {{
            border-left: 2px solid {palette.border};
            padding-left: 0.7rem;
            margin-bottom: 0.7rem;
        }}
        .dq-alert-pill {{
            display: inline-block;
            border-radius: 999px;
            padding: 0.14rem 0.58rem;
            margin-right: 0.35rem;
            font-size: 0.72rem;
            border: 1px solid rgba(255, 255, 255, 0.18);
        }}
        .dq-empty-state {{
            border-radius: 14px;
            padding: 0.9rem;
            border: 1px dashed {palette.border};
            color: {palette.secondary_text};
            background: rgba(6, 11, 20, 0.25);
        }}
        [data-testid="stDataFrame"], .stTable {{
            overflow-x: auto !important;
        }}
        @media (max-width: 768px) {{
            .main .block-container {{
                padding-left: 0.6rem;
                padding-right: 0.6rem;
            }}
            .dq-hero-value {{
                font-size: 1.6rem;
            }}
            .dq-orb {{
                width: 96px;
                height: 96px;
                font-size: 0.95rem;
            }}
        }}
        @keyframes dqPulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(33, 196, 107, 0.58); }}
            70% {{ box-shadow: 0 0 0 10px rgba(33, 196, 107, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(33, 196, 107, 0); }}
        }}
        @keyframes dqFadeIn {{
            from {{ opacity: 0.0; transform: translateY(4px); }}
            to {{ opacity: 1.0; transform: translateY(0px); }}
        }}
        @keyframes dqShimmer {{
            0% {{ background-position: 200% 0; }}
            100% {{ background-position: -200% 0; }}
        }}
        @keyframes dqTickerMove {{
            0% {{ transform: translateX(0); }}
            100% {{ transform: translateX(-35%); }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _direction_arrow(current_value, previous_value):
    if previous_value is None:
        return ""
    delta = _as_float(current_value, 0.0) - _as_float(previous_value, 0.0)
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def _metric_card(container, label, value, style_key="neutral", trend_arrow=""):
    color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
    arrow_html = f" <span style='font-size:0.95rem;'>{trend_arrow}</span>" if trend_arrow else ""
    container.markdown(
        f"<div class='dq-card'><div class='dq-label'>{label}</div><div class='dq-value' style='color:{color};'>{value}{arrow_html}</div></div>",
        unsafe_allow_html=True,
    )


def _badge(container, text, style="healthy"):
    css_class = "dq-badge" if style == "healthy" else "dq-badge dq-badge-neutral"
    container.markdown(f"<span class='{css_class}'>{text}</span>", unsafe_allow_html=True)


def _empty_state(message):
    st.markdown(f"<div class='dq-empty-state'>{message}</div>", unsafe_allow_html=True)


def _safe_plotly_chart(fig, fallback_message):
    if fig is None:
        st.info(fallback_message)
        return
    try:
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.info(fallback_message)


def render_loading_skeleton():
    st.markdown("<div class='dq-skeleton'></div><div class='dq-skeleton'></div><div class='dq-skeleton'></div>", unsafe_allow_html=True)


def render_header(payload, view):
    clock = build_market_clock()
    if "dashboard_last_refresh" not in st.session_state:
        st.session_state["dashboard_last_refresh"] = datetime.now(timezone.utc).isoformat()

    left, mid, right = st.columns([5.2, 2.2, 2.6])
    left.markdown("<div class='dq-logo'>DEAL QUANT</div>", unsafe_allow_html=True)
    left.title("DEAL QUANT COMMAND CENTER")
    left.markdown("<div class='dq-subtitle'>AUTOMATED PAPER MARKET INTELLIGENCE</div>", unsafe_allow_html=True)

    ticker_items = [
        f"<span class='dq-ticker-item'><span class='dq-ticker-label'>SPY</span>{format_currency(view.get('latest_spy_price')) if isinstance(view.get('latest_spy_price'), (int, float)) else view.get('latest_spy_price')}</span>",
        f"<span class='dq-ticker-item'><span class='dq-ticker-label'>Signal</span>{view.get('generated_signal', 'HOLD')}</span>",
        f"<span class='dq-ticker-item'><span class='dq-ticker-label'>Market</span>{clock['label']}</span>",
        f"<span class='dq-ticker-item'><span class='dq-ticker-label'>Portfolio</span>{format_currency(view.get('portfolio_value'))} {_direction_arrow(view.get('portfolio_value'), view.get('previous_portfolio_value'))}</span>",
        f"<span class='dq-ticker-item'><span class='dq-ticker-label'>Today P/L</span>{format_currency(view.get('today_pl'))} {_direction_arrow(view.get('today_pl'), 0.0)}</span>",
        f"<span class='dq-ticker-item'><span class='dq-ticker-label'>Orders Used</span>{view.get('daily_submitted_order_count', 0)} / {MAX_DAILY_ORDERS}</span>",
    ]
    left.markdown(
        "<div class='dq-ticker'><div class='dq-ticker-track'>" + "".join(ticker_items + ticker_items) + "</div></div>",
        unsafe_allow_html=True,
    )

    online_color = STATUS_COLORS["healthy"] if payload.get("db_connected") else STATUS_COLORS["error"]
    online_text = "ONLINE" if payload.get("db_connected") else "OFFLINE"
    mid.markdown(
        f"<span class='dq-pulse' style='background:{online_color};'></span><span class='dq-value'>{online_text}</span>",
        unsafe_allow_html=True,
    )
    _badge(mid, "PAPER ONLY")
    mid.markdown(f"<span class='dq-theme-badge'>{st.session_state.get('dashboard_theme', 'Midnight Blue')}</span>", unsafe_allow_html=True)

    market_indicator = "dq-market-open" if clock["is_open"] else "dq-market-closed"
    right.markdown(
        f"<div class='dq-card'><div class='dq-label'>Eastern Time</div><div class='dq-value'>{clock['time_text']}</div><div class='dq-label'><span class='dq-market-indicator {market_indicator}'></span>{clock['label']} | {clock['countdown_label']} {clock['countdown']}</div></div>",
        unsafe_allow_html=True,
    )
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
        mode_options = ["Standard Mode", "Focus Mode", "Presentation Mode"]
        mode = st.selectbox("Dashboard mode", mode_options)
        if mode not in mode_options:
            mode = st.session_state.get("dashboard_mode", "Standard Mode")
        st.session_state["dashboard_mode"] = mode
        if mode != "Presentation Mode":
            theme_options = ["Midnight Blue", "Black Terminal", "Arctic Glass"]
            theme = st.selectbox("Theme", theme_options)
            if theme not in theme_options:
                theme = st.session_state.get("dashboard_theme", "Midnight Blue")
            st.session_state["dashboard_theme"] = theme
            refresh_choice = st.selectbox("Auto-refresh", ["Off", "30 seconds", "60 seconds", "5 minutes"])
            st.session_state["dashboard_auto_refresh"] = refresh_choice
        else:
            st.session_state["dashboard_theme"] = st.session_state.get("dashboard_theme", "Midnight Blue")
            st.session_state["dashboard_auto_refresh"] = "Off"
        st.write(f"Environment: {friendly_status_text(os.getenv('TRADING_MODE', 'PAPER'))}")
        st.write(f"Database connected: {'yes' if payload.get('db_connected') else 'no'}")
        st.write(f"Last data refresh: {format_timestamp_eastern(st.session_state.get('dashboard_last_refresh'))}")
        st.write(f"Last DB refresh: {format_timestamp_eastern(st.session_state.get('dashboard_last_db_refresh'))}")
        st.caption("No trading controls")
        st.caption(f"Dashboard version {DASHBOARD_VERSION}")


def render_overview_page(payload, view):
    hero, signal_col = st.columns([3.5, 1.2])
    hero.markdown(
        f"""
        <div class='dq-card dq-hero'>
            <div class='dq-label'>Portfolio Value</div>
            <div class='dq-hero-value'>{format_currency(view['portfolio_value'])} {_direction_arrow(view.get('portfolio_value'), view.get('previous_portfolio_value'))}</div>
            <div class='dq-value'>Today P/L: <span style='color:{STATUS_COLORS['buy'] if view['today_pl'] >= 0 else STATUS_COLORS['sell']};'>{format_currency(view['today_pl'])}</span> | Total P/L: <span style='color:{STATUS_COLORS['buy'] if view['total_pl'] >= 0 else STATUS_COLORS['sell']};'>{format_currency(view['total_pl'])}</span></div>
            <div class='dq-label'>Bot Health: {view['bot_health']['label']} | Last Successful Run: {view['last_successful_run']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    orb_color = STATUS_COLORS.get(view["signal"]["style"], STATUS_COLORS["neutral"])
    signal_col.markdown(
        f"<div class='dq-card' style='text-align:center;'><div class='dq-label'>Live Signal</div><div class='dq-orb' style='background:radial-gradient(circle at 35% 25%, {orb_color}, rgba(0,0,0,0.3));'>{view['generated_signal']}</div><div class='dq-label' style='margin-top:0.5rem;'>Informational</div></div>",
        unsafe_allow_html=True,
    )

    top = st.columns(4)
    _metric_card(top[0], "Cash", format_currency(view["cash"]), "neutral")
    _metric_card(top[1], "Buying Power", format_currency(view["buying_power"]), "neutral")
    _metric_card(top[2], "Orders Used Today", f"{view['daily_submitted_order_count']} / {MAX_DAILY_ORDERS}", "warning")
    _metric_card(top[3], "Daily Notional Used", f"{format_currency(view['daily_submitted_notional'])} / {format_currency(MAX_DAILY_SUBMITTED_NOTIONAL)}", "warning")

    render_mission_control_summary(view)
    render_achievement_milestones(payload)


def _build_order_markers(recent_orders):
    buy_points = []
    sell_points = []
    for order in recent_orders or []:
        ts = _parse_iso(order.get("event_timestamp"))
        if ts is None:
            continue
        signal = normalize_signal(order.get("signal", "HOLD"))
        if signal == "BUY":
            buy_points.append(ts.astimezone(EASTERN_TZ))
        elif signal == "SELL":
            sell_points.append(ts.astimezone(EASTERN_TZ))
    return buy_points, sell_points


def render_spy_chart(payload, view):
    st.subheader("Interactive SPY Chart")
    timeframe = st.radio("Timeframe", ["1D", "5D", "1M", "3M"], horizontal=True)

    signal_history = payload.get("signal_history") or []
    price_points = build_price_points(signal_history, timeframe=timeframe)
    if len(price_points) < 2:
        st.info("Waiting for the next market-hours update")
        return

    candles = build_ohlc_series(price_points, timeframe=timeframe)
    if len(candles) < 2:
        st.info("Waiting for the next market-hours update")
        return

    market_fig = build_market_chart(candles, title="SPY")
    if market_fig is None:
        st.line_chart({"price": [item["close"] for item in candles]})
        return

    buy_points, sell_points = _build_order_markers(payload.get("recent_orders") or [])
    fig = market_fig
    fig.add_trace(go.Scatter(x=[item["timestamp"] for item in candles], y=[item.get("short_ma") for item in candles], mode="lines", name="Short MA", line={"color": "#21c46b", "width": 1.5}))
    fig.add_trace(go.Scatter(x=[item["timestamp"] for item in candles], y=[item.get("long_ma") for item in candles], mode="lines", name="Long MA", line={"color": "#f1c75b", "width": 1.5}))

    if buy_points:
        fig.add_trace(
            go.Scatter(
                x=buy_points,
                y=[candles[-1]["close"]] * len(buy_points),
                mode="markers",
                marker={"symbol": "triangle-up", "size": 10, "color": "#21c46b"},
                name="BUY markers",
            )
        )
    if sell_points:
        fig.add_trace(
            go.Scatter(
                x=sell_points,
                y=[candles[-1]["close"]] * len(sell_points),
                mode="markers",
                marker={"symbol": "triangle-down", "size": 10, "color": "#ff5c5c"},
                name="SELL markers",
            )
        )

    fig.update_layout(height=420, margin={"l": 10, "r": 10, "t": 10, "b": 10}, xaxis_rangeslider_visible=False)
    _safe_plotly_chart(fig, "Chart is temporarily unavailable")


def render_strategy_page(payload, view):
    decision, metrics = st.columns([1.25, 2.75])
    decision.markdown("### Signal Decision")
    decision.markdown(f"<div class='dq-card'><div class='dq-label'>Current decision</div><div class='dq-value'>{view['generated_signal']}</div></div>", unsafe_allow_html=True)
    decision.markdown(f"<div class='dq-card'><div class='dq-label'>Rule-based signal</div><div class='dq-value'>Not a price prediction</div></div>", unsafe_allow_html=True)
    decision.markdown(f"<div class='dq-card'><div class='dq-label'>Crossover state</div><div class='dq-value'>{'Bullish' if view['ma_distance'] > 0 else 'Bearish' if view['ma_distance'] < 0 else 'Neutral'}</div></div>", unsafe_allow_html=True)

    metrics.markdown("### Strategy Inputs")
    rows = [
        ("Current price", view["latest_spy_price"]),
        ("Short MA", view["short_moving_average"]),
        ("Long MA", view["long_moving_average"]),
        ("MA spread", f"{_as_float(view['ma_distance'], 0.0):.4f}"),
        ("Data freshness", view["latest_market_data_timestamp"]),
        ("Signal reason", view["trade_or_skip_reason"]),
    ]
    for label, value in rows:
        metrics.markdown(f"- **{_safe_text(label)}:** {_safe_text(value, 'Not available')}")

    if payload.get("signal_history"):
        chart_rows = []
        for row in payload.get("signal_history")[-80:]:
            chart_rows.append({
                "timestamp": _parse_iso(row.get("snapshot_timestamp")) or datetime.now(timezone.utc),
                "value": _as_float(row.get("latest_price"), 0.0),
            })
        _safe_plotly_chart(build_line_chart(chart_rows, "SPY price snapshots", "value"), "Signal chart unavailable")
    else:
        _empty_state("More paper-trading history is needed before this metric is meaningful.")


def render_account_page(payload, view):
    acc_cols = st.columns(3)
    acc_cols[0].metric("Portfolio Value", format_currency(view["portfolio_value"]))
    acc_cols[0].metric("Cash", format_currency(view["cash"]))
    acc_cols[1].metric("Buying Power", format_currency(view["buying_power"]))
    acc_cols[1].metric("Unrealized P/L", format_currency(view["unrealized_paper_pl"]))
    acc_cols[2].metric("Realized P/L", format_currency(view["realized_paper_pl"]))
    acc_cols[2].metric("Account Status", view["account_status"])

    st.subheader("Portfolio Allocation")
    if go is not None:
        position_value = max(view.get("open_position_value", 0.0), 0.0)
        cash_value = max(view.get("cash", 0.0), 0.0)
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=["Cash", "Open Positions"],
                    values=[cash_value, position_value],
                    hole=0.58,
                    marker={"colors": ["#44a3ff", "#21c46b"]},
                )
            ]
        )
        fig.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10})
        _safe_plotly_chart(fig, "Allocation chart unavailable")

    latest_account = payload.get("latest_account") or {}
    positions = latest_account.get("positions") if isinstance(latest_account, dict) else None
    if isinstance(positions, list) and positions:
        st.markdown("### Position Cards")
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
        _empty_state("No open paper positions. The bot is waiting for a valid rule-based entry signal.")


def render_risk_page(payload, view):
    st.markdown("### Risk-Control Matrix")
    rows = view.get("risk_matrix") or []
    if not rows:
        _empty_state("Risk data is not available right now.")
        return
    st.dataframe(rows)


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
        st.info("More paper-trading history is needed before this metric is meaningful.")
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
    portfolio_fig = build_line_chart([{ "timestamp": idx, "value": value } for idx, value in enumerate(values)], "Portfolio Value", "value", "timestamp")
    _safe_plotly_chart(portfolio_fig, "Portfolio chart unavailable")

    st.subheader("Daily Paper P/L")
    st.line_chart({"daily_pl": daily_pl})

    st.subheader("Cumulative P/L")
    cumulative_fig = build_line_chart([{ "timestamp": idx, "value": value } for idx, value in enumerate(cumulative)], "Cumulative P/L", "value", "timestamp")
    _safe_plotly_chart(cumulative_fig, "Cumulative P/L chart unavailable")

    if len(values) > 3:
        st.subheader("Drawdown")
        drawdown_fig = build_line_chart([{ "timestamp": idx, "value": value } for idx, value in enumerate(drawdown)], "Drawdown", "value", "timestamp")
        _safe_plotly_chart(drawdown_fig, "Drawdown chart unavailable")
    else:
        st.info("More paper-trading history is needed before this metric is meaningful.")

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
        st.info("More paper-trading history is needed before this metric is meaningful.")


def _event_style(level):
    if level == "critical":
        return "error", "❗"
    if level == "warning":
        return "warning", "⚠"
    return "neutral", "●"


def build_activity_timeline(payload, view):
    events = []
    if payload.get("latest_run"):
        events.append({"event": "Worker started", "time": view.get("last_run_timestamp"), "level": "info"})
        if str(view.get("account_status", "")).lower() == "active":
            events.append({"event": "Account authenticated", "time": view.get("last_run_timestamp"), "level": "info"})
        events.append({"event": f"Market checked ({view['market_status']['label']})", "time": view.get("last_run_timestamp"), "level": "info"})
        events.append({"event": f"Signal generated ({view.get('generated_signal', 'HOLD')})", "time": view.get("last_run_timestamp"), "level": "info"})

    for order in (payload.get("recent_orders") or [])[:12]:
        submitted = _as_bool(order.get("submitted"))
        signal = normalize_signal(order.get("signal", "HOLD"))
        status = friendly_status_text(order.get("safe_order_status"), "Unknown")
        if submitted:
            events.append({"event": f"Order submitted ({signal})", "time": format_timestamp_eastern(order.get("event_timestamp")), "level": "info"})
            if str(status).lower() in {"filled", "accepted"}:
                events.append({"event": f"Order {status.lower()}", "time": format_timestamp_eastern(order.get("event_timestamp")), "level": "info"})
        else:
            events.append({"event": f"Order blocked ({_safe_text(order.get('stop_reason'), 'safety block')})", "time": format_timestamp_eastern(order.get("event_timestamp")), "level": "warning"})

    if view.get("review_required"):
        events.append({"event": "Review required", "time": view.get("last_run_timestamp"), "level": "critical"})
    if view.get("latest_safe_error_message"):
        events.append({"event": f"Warning/Error: {_safe_text(view.get('latest_safe_error_message'))}", "time": view.get("last_run_timestamp"), "level": "warning"})
    return events[:24]


def build_activity_feed(payload, view):
    feed = []
    for item in build_activity_timeline(payload, view):
        feed.append(
            {
                "timestamp": item.get("time"),
                "event": item.get("event"),
                "status": item.get("level"),
                "category": "Trading" if "Order" in str(item.get("event", "")) or "Signal" in str(item.get("event", "")) else "Infrastructure",
                "detail": item.get("event"),
            }
        )
    return feed


def render_system_health_page(payload, view):
    cols = st.columns(3)
    _metric_card(cols[0], "Bot Status", view["bot_health"]["label"], view["bot_health"]["style"])
    _metric_card(cols[1], "Database", "Connected" if payload.get("db_connected") else "Disconnected", "healthy" if payload.get("db_connected") else "error")
    _metric_card(cols[2], "Alpaca Paper Authentication", "Active" if view["account_status"].lower() == "active" else view["account_status"], "healthy" if view["account_status"].lower() == "active" else "warning")

    st.markdown("### Bot Activity Timeline")
    events = build_activity_timeline(payload, view)
    if not events:
        _empty_state("No monitoring records available yet")
        return

    for item in events:
        style_key, icon = _event_style(item["level"])
        color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
        st.markdown(
            f"<div class='dq-timeline-item'><span style='color:{color};font-weight:700;'>{icon}</span> <span class='dq-value'>{_safe_text(item['event'])}</span><div class='dq-label'>{_safe_text(item['time'])}</div></div>",
            unsafe_allow_html=True,
        )


def build_notification_items(payload, view):
    notices = []
    for order in (payload.get("recent_orders") or [])[:25]:
        if _as_bool(order.get("submitted")):
            notices.append({
                "severity": "Info",
                "message": f"Order submitted: {normalize_signal(order.get('signal', 'HOLD'))} {order.get('symbol', 'SPY')} ({friendly_status_text(order.get('safe_order_status'), 'unknown')})",
                "timestamp": format_timestamp_eastern(order.get("event_timestamp")),
            })
        else:
            notices.append({
                "severity": "Warning",
                "message": f"Safety block: {_safe_text(order.get('stop_reason'), 'blocked')}",
                "timestamp": format_timestamp_eastern(order.get("event_timestamp")),
            })

    if view.get("review_required"):
        notices.append({"severity": "Critical", "message": "Review required is active", "timestamp": view.get("last_run_timestamp")})
    if not payload.get("db_connected"):
        notices.append({"severity": "Critical", "message": "Monitoring database disconnected", "timestamp": view.get("last_run_timestamp")})
    if "monitoring" in str(view.get("latest_stop_reason", "")).lower() or "database" in str(view.get("latest_stop_reason", "")).lower():
        notices.append({"severity": "Warning", "message": f"Monitoring warning: {_safe_text(view.get('latest_stop_reason'))}", "timestamp": view.get("last_run_timestamp")})
    if "discord" in str(view.get("latest_stop_reason", "")).lower() or "discord" in str(view.get("latest_safe_error_message", "")).lower():
        notices.append({"severity": "Info", "message": "Discord alert status detected in monitoring logs", "timestamp": view.get("last_run_timestamp")})

    return notices


def render_notification_center(payload, view):
    st.subheader("Notification Center")
    severity = st.selectbox("Severity", ["All", "Info", "Warning", "Critical"])
    notices = build_notification_items(payload, view)
    if severity != "All":
        notices = [n for n in notices if n["severity"] == severity]
    if not notices:
        _empty_state("No notifications for the selected severity")
        return
    for notice in notices:
        style_key, _ = _event_style(notice["severity"].lower())
        color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
        st.markdown(
            f"<div class='dq-card'><span class='dq-alert-pill' style='color:{color};'>{notice['severity']}</span><span class='dq-value'>{_safe_text(notice['message'])}</span><div class='dq-label'>{_safe_text(notice['timestamp'])}</div></div>",
            unsafe_allow_html=True,
        )


def _component_status(payload, view):
    db_ok = payload.get("db_connected")
    bot_ok = bool(payload.get("latest_run")) and view.get("bot_health", {}).get("style") != "error"
    alpaca_ok = str(view.get("account_status", "")).lower() == "active"
    review_warning = bool(view.get("review_required"))
    status = {
        "GitHub": "healthy",
        "Railway worker": "healthy" if bot_ok else "warning",
        "Railway cron": "healthy" if payload.get("latest_run") else "warning",
        "Alpaca Paper": "healthy" if alpaca_ok else "warning",
        "Railway volume": "healthy" if db_ok else "warning",
        "PostgreSQL": "healthy" if db_ok else "offline",
        "Discord notifications": "warning" if review_warning else "healthy",
        "Streamlit dashboard": "healthy" if db_ok else "warning",
    }
    return status


def render_architecture_page(payload, view):
    st.subheader("System Architecture")
    statuses = _component_status(payload, view)
    components = [
        "GitHub",
        "Railway worker",
        "Railway cron",
        "Alpaca Paper",
        "Railway volume",
        "PostgreSQL",
        "Discord notifications",
        "Streamlit dashboard",
    ]
    cols = st.columns(2)
    palette = build_palette(st.session_state.get("dashboard_theme", "Midnight Blue"))
    for idx, component in enumerate(components):
        status = statuses.get(component, "warning")
        style = "healthy" if status == "healthy" else "warning" if status == "warning" else "error"
        value = "Healthy" if status == "healthy" else "Warning" if status == "warning" else "Offline"
        status_color = status_style(value.lower(), palette)
        cols[idx % 2].markdown(f"<div class='dq-card'><div class='dq-label'>{component}</div><div class='dq-value' style='color:{status_color};'>{value}</div></div>", unsafe_allow_html=True)


render_operations_page = render_system_health_page
render_alerts_page = render_notification_center


def render_mission_control_summary(view):
    summary = view.get("mission_summary", {})
    st.markdown("### Daily Mission-Control Summary")
    cols = st.columns(4)
    _metric_card(cols[0], "Runs Completed Today", summary.get("runs_completed", 0), "neutral")
    counts = summary.get("signal_counts", {"BUY": 0, "HOLD": 0, "SELL": 0})
    _metric_card(cols[1], "BUY / HOLD / SELL", f"{counts.get('BUY', 0)} / {counts.get('HOLD', 0)} / {counts.get('SELL', 0)}", "neutral")
    _metric_card(cols[2], "Submitted / Blocked", f"{summary.get('submitted_orders', 0)} / {summary.get('blocked_orders', 0)}", "warning")
    _metric_card(cols[3], "Remaining Order Capacity", summary.get("remaining_order_capacity", 0), "warning")

    st.caption(f"Daily notional used: {format_currency(summary.get('daily_notional_used', 0.0))} | Latest stop reason: {_safe_text(summary.get('latest_stop_reason', 'N/A'))}")
    capacity_ratio = (MAX_DAILY_ORDERS - summary.get("remaining_order_capacity", 0)) / max(MAX_DAILY_ORDERS, 1)
    st.progress(min(max(capacity_ratio, 0.0), 1.0))


def render_achievement_milestones(payload):
    st.markdown("### Achievement Milestones")
    has_run = bool(payload.get("recent_runs"))
    has_auth = any(str((payload.get("latest_account") or {}).get("account_status", "")).upper() == "ACTIVE" for _ in [0])
    has_state = bool(payload.get("latest_run"))
    has_record = bool(payload.get("signal_history"))
    has_order = any(_as_bool(o.get("submitted")) for o in (payload.get("recent_orders") or []))
    has_week = len(payload.get("order_count_by_day") or []) >= 5

    milestones = [
        ("First successful cloud run", has_run),
        ("First authenticated Alpaca connection", has_auth),
        ("First persisted state load", has_state),
        ("First dashboard record", has_record),
        ("First paper order", has_order),
        ("First completed market week", has_week),
    ]
    cols = st.columns(3)
    for idx, (label, done) in enumerate(milestones):
        style = "healthy" if done else "neutral"
        marker = "Unlocked" if done else "Pending"
        _metric_card(cols[idx % 3], label, marker, style)


def render_system_health_page(payload, view):
    cols = st.columns(3)
    _metric_card(cols[0], "Bot Status", view["bot_health"]["label"], view["bot_health"]["style"])
    _metric_card(cols[1], "Database", "Connected" if payload.get("db_connected") else "Disconnected", "healthy" if payload.get("db_connected") else "error")
    _metric_card(cols[2], "Review Required", "Yes" if view["review_required"] else "No", "error" if view["review_required"] else "healthy")

    st.markdown("### Latest Diagnostics")
    st.markdown(f"- Last successful run: {_safe_text(view['last_successful_run'])}")
    st.markdown(f"- Latest safe error: {_safe_text(view['latest_safe_error_message'] or 'None')}")
    st.markdown(f"- Market-data freshness: {_safe_text(view['latest_market_data_timestamp'])}")

    st.markdown("### Bot Activity Timeline")
    events = build_activity_timeline(payload, view)
    if not events:
        _empty_state("No monitoring records available yet")
        return
    for item in events:
        style_key, icon = _event_style(item["level"])
        color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
        st.markdown(
            f"<div class='dq-timeline-item'><span style='color:{color};font-weight:700;'>{icon}</span> <span class='dq-value'>{_safe_text(item['event'])}</span><div class='dq-label'>{_safe_text(item['time'])}</div></div>",
            unsafe_allow_html=True,
        )


def render_research_page():
    st.markdown("### RESEARCH ONLY — NOT CONNECTED TO PAPER EXECUTION")
    metrics = load_research_summary()
    cols = st.columns(5)
    _metric_card(cols[0], "Gross Return", metrics["gross_return"], "neutral")
    _metric_card(cols[1], "Cost Sensitivity", metrics["cost_sensitivity"], "warning")
    _metric_card(cols[2], "Break-even Cost", metrics["break_even_cost"], "warning")
    _metric_card(cols[3], "Drawdown", metrics["drawdown"], "sell")
    _metric_card(cols[4], "Sharpe Ratio", metrics["sharpe_ratio"], "healthy")
    st.caption("The overnight research study is informational only and is not connected to the SPY paper runner.")

    st.markdown("### Read-only exports")
    export_payload = st.session_state.get("dashboard_export_payload") or {}
    activity_csv = export_daily_activity(export_payload.get("activity", []))
    signals_csv = export_signal_history(export_payload.get("signals", []))
    orders_csv = export_sanitized_orders(export_payload.get("orders", []))
    health_csv = export_system_health(export_payload.get("health", []))
    perf_csv = export_performance_summary(export_payload.get("performance", []))
    if hasattr(st, "download_button"):
        st.download_button("Daily activity CSV", activity_csv, file_name="daily_activity.csv", mime="text/csv")
        st.download_button("Signal history CSV", signals_csv, file_name="signal_history.csv", mime="text/csv")
        st.download_button("Sanitized order-events CSV", orders_csv, file_name="order_events.csv", mime="text/csv")
        st.download_button("System-health report", health_csv, file_name="system_health.csv", mime="text/csv")
        st.download_button("Paper-performance summary", perf_csv, file_name="performance_summary.csv", mime="text/csv")


def render_dashboard(database_url: str | None = None):
    if st is None:
        raise RuntimeError("streamlit is required to run the dashboard")

    enforce_paper_mode(os.getenv("TRADING_MODE", "PAPER"))
    st.set_page_config(page_title="DEAL QUANT COMMAND CENTER", layout="wide")

    if "dashboard_theme" not in st.session_state:
        st.session_state["dashboard_theme"] = "Midnight Blue"
    if "dashboard_auto_refresh" not in st.session_state:
        st.session_state["dashboard_auto_refresh"] = "Off"

    apply_dashboard_css(st.session_state.get("dashboard_theme", "Midnight Blue"))

    expected_password = os.getenv("DASHBOARD_PASSWORD", "")
    provided_password = st.text_input("Dashboard Password", type="password")
    if not check_dashboard_password(provided_password, expected_password):
        st.warning("Access denied")
        st.stop()

    render_loading_skeleton()
    try:
        payload = _cached_payload(database_url or os.getenv("DATABASE_URL"))
        st.session_state["dashboard_last_db_refresh"] = datetime.now(timezone.utc).isoformat()
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
    st.session_state["dashboard_export_payload"] = {
        "activity": build_activity_feed(payload, view),
        "signals": payload.get("signal_history") or [],
        "orders": payload.get("recent_orders") or [],
        "health": [{"component": component, "status": status, "timestamp": view.get("last_run_timestamp"), "reason": "Read only"} for component, status in _component_status(payload, view).items()],
        "performance": [{"metric": "portfolio_value", "value": view.get("portfolio_value")}, {"metric": "today_pl", "value": view.get("today_pl")}, {"metric": "total_pl", "value": view.get("total_pl")}],
    }
    render_sidebar(payload)
    apply_dashboard_css(st.session_state.get("dashboard_theme", "Midnight Blue"))

    refresh_mapping = {
        "Off": 0,
        "30 seconds": 30,
        "60 seconds": 60,
        "5 minutes": 300,
    }
    refresh_seconds = refresh_mapping.get(st.session_state.get("dashboard_auto_refresh", "Off"), 0)
    if refresh_seconds > 0 and st_autorefresh is not None:
        st_autorefresh(interval=refresh_seconds * 1000, key="dashboard_auto_refresh")

    render_header(payload, view)
    render_alert_banner(payload, view)

    tabs = st.tabs(["Command Center", "Strategy", "Risk", "Portfolio", "Orders", "Performance", "Operations", "Alerts", "Research"])

    with tabs[0]:
        render_overview_page(payload, view)
    with tabs[1]:
        render_strategy_page(payload, view)
    with tabs[2]:
        render_risk_page(payload, view)
    with tabs[3]:
        render_account_page(payload, view)
    with tabs[4]:
        render_orders_page(payload)
    with tabs[5]:
        render_performance_page(payload)
    with tabs[6]:
        render_operations_page(payload, view)
    with tabs[7]:
        render_alerts_page(payload, view)
    with tabs[8]:
        render_research_page()

    for message in empty_state_messages(payload, view):
        st.info(message)


def main():
    render_dashboard()


if __name__ == "__main__":
    main()
