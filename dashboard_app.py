import os
import json
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

    from config import BENCHMARK_SYMBOL
from monitoring_db import MonitoringDatabase
from dashboard_data import fetch_dashboard_payload
from dashboard_exports import export_daily_activity, export_performance_summary, export_sanitized_orders, export_signal_history, export_system_health
from dashboard_charts import build_line_chart, build_market_chart
from dashboard_components import build_palette, status_style
from dashboard_models import build_normalized_view_model
from dashboard_sanitization import sanitize_identifier, sanitize_text
from dashboard_status import classify_market_clock, format_est
from logger_setup import logger
from evaluation_data import fetch_evaluation_dashboard_payload
from factor_attribution import fetch_factor_attribution_dashboard_payload
from research_data import fetch_research_dashboard_payload


MAX_DAILY_ORDERS = 3
MAX_DAILY_SUBMITTED_NOTIONAL = 30.0
DASHBOARD_VERSION = "v2.0"
UI_BUILD_LABEL = "DEAL QUANT UI — BUILD 5"
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
    "Arctic Glass": {
        "bg": "radial-gradient(circle at 12% 8%, #edf5ff 0%, #d7e5f7 52%, #c8d9ee 100%)",
        "panel": "linear-gradient(140deg, rgba(255, 255, 255, 0.76), rgba(229, 240, 255, 0.58))",
        "text": "#13263d",
        "subtle": "#445d78",
        "accent": "#2e7ccf",
        "grid": "rgba(46, 124, 207, 0.10)",
    },
}

PAGE_OPTIONS = ["Command Center", "Strategy", "Risk", "Portfolio", "Orders", "Performance", "Operations", "Alerts", "Research", "Factor Attribution"]
MODE_OPTIONS = ["Standard Mode", "Focus Mode", "Presentation Mode"]
THEME_OPTIONS = ["Midnight Blue", "Black Terminal", "Arctic Glass"]
AUTO_REFRESH_OPTIONS = ["Off", "30 seconds", "60 seconds", "5 minutes"]
TIMEFRAME_OPTIONS = ["1D", "5D", "1M", "3M"]


def initialize_dashboard_session_state() -> None:
    defaults = {
        "dashboard_authenticated": False,
        "dashboard_page": "Command Center",
        "dashboard_page_selector": "Command Center",
        "dashboard_theme": "Midnight Blue",
        "dashboard_theme_selector": "Midnight Blue",
        "dashboard_mode": "Standard Mode",
        "dashboard_mode_selector": "Standard Mode",
        "dashboard_focus_mode": False,
        "dashboard_presentation_mode": False,
        "dashboard_timeframe": "1D",
        "dashboard_timeframe_selector": "1D",
        "dashboard_alert_severity": "All",
        "dashboard_acknowledged_alerts": [],
        "dashboard_auto_refresh": "Off",
        "dashboard_auto_refresh_selector": "Off",
        "dashboard_last_manual_refresh_status": "",
        "dashboard_last_refresh": None,
        "dashboard_last_db_refresh": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _refresh_mode_flags() -> None:
    mode = st.session_state.get("dashboard_mode", "Standard Mode")
    st.session_state["dashboard_focus_mode"] = mode == "Focus Mode"
    st.session_state["dashboard_presentation_mode"] = mode == "Presentation Mode"


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


def format_compact_timestamp(value, fallback="Waiting for the next market-hours update") -> dict[str, str]:
    dt = _parse_iso(value)
    if dt is None:
        return {
            "time": fallback,
            "date": "",
            "full": fallback,
            "relative": "",
        }
    now_et = datetime.now(EASTERN_TZ)
    dt_et = dt.astimezone(EASTERN_TZ)
    delta_seconds = max(int((now_et - dt_et).total_seconds()), 0)
    if delta_seconds < 90:
        relative = "Updated moments ago"
    elif delta_seconds < 3600:
        relative = f"Updated {delta_seconds // 60}m ago"
    else:
        relative = f"Updated {delta_seconds // 3600}h ago"
    return {
        "time": dt_et.strftime("%I:%M %p ET").lstrip("0"),
        "date": f"{dt_et.strftime('%B')} {dt_et.day}, {dt_et.year}",
        "full": dt_et.strftime("%Y-%m-%d %I:%M:%S %p ET"),
        "relative": relative,
    }


def format_compact_countdown(countdown_text: str) -> str:
    parts = str(countdown_text or "00:00:00").split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except Exception:
        return "0H 0M"
    return f"{hours}H {minutes:02d}M"


def build_status_bar_items(payload, view, clock):
    market_label = "MARKET OPEN" if clock.get("is_open") else "MARKET CLOSED"
    market_style = "healthy" if clock.get("is_open") else "neutral"
    signal = normalize_signal(view.get("generated_signal", "HOLD"))
    signal_style = "buy" if signal == "BUY" else "sell" if signal == "SELL" else "neutral"
    countdown_label = "NEXT CLOSE IN" if clock.get("is_open") else "NEXT OPEN IN"
    latest_run = (payload.get("latest_run") or {}).get("run_timestamp")
    latest_dt = _parse_iso(latest_run)
    next_run_text = "Waiting for next run"
    if latest_dt is not None:
        next_parts = format_compact_timestamp((latest_dt + timedelta(minutes=30)).isoformat())
        next_run_text = next_parts["time"]
    trading_mode = str(((payload.get("latest_run") or {}).get("trading_mode") or "PAPER")).upper()
    system_label = "SYSTEM ONLINE" if payload.get("db_connected") else "SYSTEM DEGRADED"
    system_style = "healthy" if payload.get("db_connected") else "warning"
    return [
        {"label": system_label, "style": system_style},
        {"label": market_label, "style": market_style},
        {"label": "PAPER", "style": "healthy"},
        {"label": "LIVE DETECTED" if trading_mode == "LIVE" else "LIVE BLOCKED", "style": "critical" if trading_mode == "LIVE" else "neutral"},
        {"label": f"CURRENT SIGNAL: {signal}", "style": signal_style},
        {"label": f"{countdown_label} {format_compact_countdown(clock.get('countdown', '00:00:00'))}", "style": "neutral"},
        {"label": f"NEXT SCHEDULED RUN: {next_run_text}", "style": "neutral"},
    ]


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


def load_research_summary(database_url: str | None = None, selected_run_id: str | None = None):
    try:
        database_value = database_url or os.getenv("DATABASE_URL")
        research_payload = fetch_research_dashboard_payload(database_value, selected_run_id=selected_run_id, database_factory=MonitoringDatabase)
        evaluation_payload = fetch_evaluation_dashboard_payload(database_value, database_factory=MonitoringDatabase)
        factor_attribution_payload = fetch_factor_attribution_dashboard_payload(database_value, database_factory=MonitoringDatabase)
        research_payload["evaluation"] = evaluation_payload
        research_payload["factor_attribution"] = factor_attribution_payload
        return research_payload
    except Exception as exc:  # pragma: no cover - defensive dashboard path
        logger.error("RESEARCH_DASHBOARD_QUERY_FAILURE type=%s message=%s", type(exc).__name__, exc)
        return {
            "db_connected": False,
            "latest_research_run": {},
            "recent_research_runs": [],
            "selected_research_run_id": selected_run_id,
            "selected_research_candidates": [],
            "research_analytics": {
                "total_research_runs": 0,
                "total_candidate_observations": 0,
                "average_candidates_per_run": 0.0,
                "average_overall_score": 0.0,
                "average_confidence": 0.0,
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
                "db_connected": False,
                "latest_labeling_run": {},
                "recent_labeled_observations": [],
                "recent_label_failures": [],
                "selected_horizon": "20d",
                "evaluation_analytics": {
                    "benchmark_symbol": BENCHMARK_SYMBOL,
                    "total_observations": 0,
                    "labeled_candidates": 0,
                    "status_counts": {"pending": 0, "partial": 0, "complete": 0, "unavailable": 0, "data_error": 0},
                    "horizons": {},
                    "score_buckets": {},
                    "confidence_buckets": {},
                    "regime_analysis": {},
                    "sector_analysis": {},
                    "signal_analysis": {},
                    "rank_analysis": {},
                    "recurring_symbol_analysis": {},
                    "correlations": {},
                    "latest_attempted_at": None,
                },
                "evaluation_config": {},
            },
            "factor_attribution": {
                "db_connected": False,
                "selected_horizon": "20d",
                "selected_factor": "overall_score",
                "factor_attribution_analytics": {
                    "factor_bucket_analysis": {},
                    "factor_distributions": {},
                    "factor_correlations": [],
                    "feature_importance_summary": [],
                    "strongest_predictive_factors": [],
                    "weakest_predictive_factors": [],
                    "minimum_sample_warnings": [],
                    "top_factor_combinations": {},
                },
                "factor_options": [],
            },
        }


def render_factor_attribution_page():
    st.markdown("### FACTOR ATTRIBUTION — READ ONLY")
    payload = st.session_state.get("dashboard_research_payload") or {}
    factor_payload = payload.get("factor_attribution") or {
        "db_connected": False,
        "selected_horizon": "20d",
        "selected_factor": "overall_score",
        "factor_attribution_analytics": {
            "factor_bucket_analysis": {},
            "factor_distributions": {},
            "factor_correlations": [],
            "feature_importance_summary": [],
            "strongest_predictive_factors": [],
            "weakest_predictive_factors": [],
            "minimum_sample_warnings": [],
            "top_factor_combinations": {},
        },
        "factor_options": [],
    }
    analytics = factor_payload.get("factor_attribution_analytics") or {}
    factor_options = list(factor_payload.get("factor_options") or [])
    if not factor_options:
        factor_options = ["overall_score", "confidence", "trend_score", "momentum_score", "volatility_score", "liquidity_score", "market_regime_score", "risk_quality_score", "rank", "signal", "market_regime", "sector"]
    horizon_options = ["1d", "5d", "10d", "20d"]
    control_cols = st.columns(2)
    selected_horizon = control_cols[0].selectbox("Attribution horizon", horizon_options, index=horizon_options.index(factor_payload.get("selected_horizon", "20d")) if factor_payload.get("selected_horizon", "20d") in horizon_options else len(horizon_options) - 1, key="dashboard_factor_attribution_horizon")
    selected_factor = control_cols[1].selectbox("Factor", factor_options, index=factor_options.index(factor_payload.get("selected_factor", factor_options[0])) if factor_payload.get("selected_factor", factor_options[0]) in factor_options else 0, key="dashboard_factor_attribution_factor")

    strongest = analytics.get("strongest_predictive_factors") or []
    weakest = analytics.get("weakest_predictive_factors") or []
    importance = analytics.get("feature_importance_summary") or []
    correlations = analytics.get("factor_correlations") or []
    selected_factor_buckets = ((analytics.get("factor_bucket_analysis") or {}).get(selected_factor) or {}).get(selected_horizon) or []
    selected_distribution = (analytics.get("factor_distributions") or {}).get(selected_factor) or {}
    combinations = (analytics.get("top_factor_combinations") or {}).get(selected_horizon) or []
    warnings = analytics.get("minimum_sample_warnings") or []

    overview_cols = st.columns(4)
    _metric_card(overview_cols[0], "Tracked Factors", len(importance), "neutral")
    _metric_card(overview_cols[1], "Strong Signals", len(strongest), "healthy")
    _metric_card(overview_cols[2], "Weak Signals", len(weakest), "warning")
    _metric_card(overview_cols[3], "Low-Sample Warnings", len(warnings), "warning")

    top_cols = st.columns(2)
    with top_cols[0]:
        st.markdown("#### Strongest Predictive Factors")
        st.dataframe(strongest)
    with top_cols[1]:
        st.markdown("#### Weakest Predictive Factors")
        st.dataframe(weakest)

    st.markdown("#### Correlation Table")
    st.dataframe(correlations)

    selected_cols = st.columns(2)
    with selected_cols[0]:
        st.markdown(f"#### Bucket Analysis — {selected_factor} / {selected_horizon}")
        st.dataframe(selected_factor_buckets)
    with selected_cols[1]:
        st.markdown(f"#### Distribution — {selected_factor}")
        st.dataframe([selected_distribution] if selected_distribution else [])

    st.markdown("#### Top Factor Combinations")
    st.dataframe(combinations)

    st.markdown("#### Minimum Sample Warnings")
    st.dataframe(warnings[:50])

    st.markdown("#### Read-only attribution exports")
    export_payload = {
        "feature_importance_summary": importance,
        "factor_correlations": correlations,
        "selected_factor_bucket_analysis": selected_factor_buckets,
        "top_factor_combinations": combinations,
        "minimum_sample_warnings": warnings,
    }
    export_blobs = {key: json.dumps(value, indent=2, sort_keys=True) for key, value in export_payload.items()}
    if hasattr(st, "download_button"):
        for label, rows, key, file_name in [
            ("Feature importance JSON", importance, "feature_importance_summary", "feature_importance_summary.json"),
            ("Factor correlations JSON", correlations, "factor_correlations", "factor_correlations.json"),
            ("Bucket analysis JSON", selected_factor_buckets, "selected_factor_bucket_analysis", "factor_bucket_analysis.json"),
            ("Top factor combinations JSON", combinations, "top_factor_combinations", "top_factor_combinations.json"),
        ]:
            has_rows = bool(rows)
            if not has_rows:
                st.info(f"No data available for {label}")
            st.download_button(label, export_blobs[key], file_name=sanitize_identifier(file_name.replace(".json", "")) + ".json", mime="application/json", key=f"download_{key}", disabled=not has_rows)


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
            "research": {
                "db_connected": False,
                "latest_research_run": {},
                "recent_research_runs": [],
                "selected_research_run_id": "",
                "selected_research_candidates": [],
                "research_analytics": {
                    "total_research_runs": 0,
                    "total_candidate_observations": 0,
                    "average_candidates_per_run": 0.0,
                    "average_overall_score": 0.0,
                    "average_confidence": 0.0,
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
            },
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
        .dq-build-marker {{
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            margin-bottom: 0.4rem;
            padding: 0.26rem 0.62rem;
            border-radius: 999px;
            border: 1px solid rgba(68, 163, 255, 0.36);
            background: linear-gradient(135deg, rgba(18, 32, 52, 0.96), rgba(11, 16, 28, 0.9));
            color: {palette.primary_text};
            font-size: 0.67rem;
            font-weight: 800;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.24);
        }}
        .dq-shell-header {{
            background: linear-gradient(160deg, rgba(18, 27, 44, 0.96), rgba(10, 14, 23, 0.9));
            border: 1px solid rgba(86, 121, 181, 0.24);
            border-radius: 16px;
            padding: 0.55rem 0.8rem 0.6rem;
            margin-bottom: 0.6rem;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.32);
            position: relative;
            overflow: hidden;
        }}
        .dq-shell-header::before {{
            content: "";
            position: absolute;
            inset: 0;
            background: radial-gradient(circle at top right, rgba(68, 163, 255, 0.18), transparent 28%), radial-gradient(circle at bottom left, rgba(33, 196, 107, 0.12), transparent 34%);
            pointer-events: none;
        }}
        .dq-header-grid {{
            display: grid;
            grid-template-columns: minmax(0, 1.6fr) minmax(320px, 1fr);
            gap: 0.55rem;
            align-items: stretch;
            position: relative;
            z-index: 1;
        }}
        .dq-header-kicker {{
            color: {palette.secondary_text};
            font-size: 0.72rem;
            letter-spacing: 0.06rem;
            font-weight: 800;
            margin-bottom: 0.15rem;
        }}
        .dq-header-title {{
            font-size: 1.55rem;
            line-height: 1.08;
            font-weight: 900;
            letter-spacing: 0.04rem;
            margin-bottom: 0.15rem;
        }}
        .dq-header-subtitle {{
            color: {palette.secondary_text};
            font-size: 0.8rem;
            letter-spacing: 0.03rem;
            margin-bottom: 0.45rem;
        }}
        .dq-header-badges {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
        }}
        .dq-chip {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: 999px;
            padding: 0.35rem 0.7rem;
            border: 1px solid transparent;
            font-size: 0.69rem;
            font-weight: 800;
            letter-spacing: 0.02rem;
        }}
        .dq-chip.neutral {{ background: rgba(138, 150, 168, 0.16); border-color: rgba(138, 150, 168, 0.28); color: {palette.neutral}; }}
        .dq-chip.healthy {{ background: rgba(33, 196, 107, 0.14); border-color: rgba(33, 196, 107, 0.34); color: {palette.positive}; }}
        .dq-chip.warning {{ background: rgba(241, 199, 91, 0.12); border-color: rgba(241, 199, 91, 0.34); color: {palette.warning}; }}
        .dq-chip.critical {{ background: rgba(255, 92, 92, 0.12); border-color: rgba(255, 92, 92, 0.34); color: {palette.critical}; }}
        .dq-header-meta {{
            display: grid;
            gap: 0.45rem;
            align-content: stretch;
        }}
        .dq-header-meta-row {{
            background: rgba(8, 13, 22, 0.52);
            border: 1px solid rgba(86, 121, 181, 0.18);
            border-radius: 10px;
            padding: 0.48rem 0.62rem;
            display: flex;
            justify-content: space-between;
            gap: 0.8rem;
        }}
        .dq-header-meta-label {{
            color: {palette.secondary_text};
            font-size: 0.72rem;
            letter-spacing: 0.01rem;
        }}
        .dq-header-meta-value {{
            color: {palette.primary_text};
            font-size: 0.88rem;
            font-weight: 700;
        }}
        .dq-status-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            align-items: center;
            margin-top: 0.42rem;
            margin-bottom: 0.25rem;
        }}
        .dq-header-footer {{
            margin-top: 0.4rem;
            color: {palette.secondary_text};
            font-size: 0.74rem;
            letter-spacing: 0.01rem;
        }}
        .dq-nav-bar {{
            position: sticky;
            top: 0;
            z-index: 10;
            background: rgba(9, 13, 21, 0.84);
            border: 1px solid rgba(86, 121, 181, 0.2);
            border-radius: 12px;
            padding: 0.35rem 0.55rem;
            margin-bottom: 0.6rem;
            backdrop-filter: blur(4px);
        }}
        div[data-testid="stSelectbox"] label p {{
            color: {palette.secondary_text};
            font-size: 0.75rem;
            letter-spacing: 0.01rem;
        }}
        .stButton > button {{
            border-radius: 9px;
            border: 1px solid rgba(86, 121, 181, 0.35);
            background: linear-gradient(135deg, rgba(14, 22, 35, 0.95), rgba(10, 15, 24, 0.95));
            color: {palette.primary_text};
            font-size: 0.8rem;
            padding: 0.35rem 0.7rem;
        }}
        .stButton > button:hover {{
            border-color: rgba(68, 163, 255, 0.55);
        }}
        .dq-metric-card {{
            background: linear-gradient(145deg, rgba(18, 27, 44, 0.88), rgba(13, 19, 31, 0.8));
            border: 1px solid rgba(86, 121, 181, 0.24);
            box-shadow: 0 14px 30px rgba(0, 0, 0, 0.28);
            border-radius: 14px;
            padding: 0.78rem 0.88rem;
            margin-bottom: 0.55rem;
            min-height: 102px;
            transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
        }}
        .dq-metric-card:hover {{
            transform: translateY(-2px);
            border-color: rgba(68, 163, 255, 0.48);
            box-shadow: 0 18px 34px rgba(0, 0, 0, 0.34);
        }}
        .dq-metric-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
            margin-bottom: 0.6rem;
        }}
        .dq-metric-icon {{
            width: 30px;
            height: 30px;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: {palette.primary_text};
            font-size: 0.8rem;
            font-weight: 900;
        }}
        .dq-metric-label {{
            color: {palette.secondary_text};
            font-size: 0.75rem;
            letter-spacing: 0.01rem;
            font-weight: 800;
        }}
        .dq-metric-value {{
            color: {palette.primary_text};
            font-size: 1.35rem;
            line-height: 1.12;
            font-weight: 850;
        }}
        .dq-metric-note {{
            margin-top: 0.35rem;
            color: {palette.secondary_text};
            font-size: 0.82rem;
        }}
        .dq-panel {{
            background: linear-gradient(150deg, rgba(18, 27, 44, 0.9), rgba(12, 16, 25, 0.78));
            border: 1px solid rgba(86, 121, 181, 0.22);
            border-radius: 14px;
            padding: 1rem 1.05rem;
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.28);
        }}
        .dq-panel-title {{
            font-size: 0.82rem;
            font-weight: 900;
            letter-spacing: 0.03rem;
        }}
        .dq-panel-subtitle {{
            margin-top: 0.35rem;
            color: {palette.secondary_text};
            font-size: 0.82rem;
        }}
        .dq-chart-frame {{
            background: linear-gradient(150deg, rgba(18, 27, 44, 0.9), rgba(12, 16, 25, 0.78));
            border: 1px solid rgba(86, 121, 181, 0.22);
            border-radius: 14px;
            padding: 0.82rem 0.92rem;
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.28);
        }}
        .dq-chart-toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
            margin-bottom: 0.7rem;
        }}
        .dq-chart-badges {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            justify-content: flex-end;
        }}
        .dq-chart-surface {{
            background: rgba(6, 10, 16, 0.3);
            border: 1px solid rgba(86, 121, 181, 0.16);
            border-radius: 16px;
            padding: 0.55rem;
        }}
        .dq-signal-panel {{
            height: 100%;
        }}
        .dq-signal-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.65rem;
            margin-top: 0.9rem;
        }}
        .dq-signal-row {{
            background: rgba(7, 12, 19, 0.3);
            border: 1px solid rgba(86, 121, 181, 0.16);
            border-radius: 14px;
            padding: 0.7rem 0.8rem;
        }}
        .dq-signal-chip {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: 999px;
            border: 1px solid transparent;
            padding: 0.3rem 0.65rem;
            font-size: 0.72rem;
            font-weight: 900;
            letter-spacing: 0.1rem;
            text-transform: uppercase;
        }}
        .dq-signal-buy {{ background: rgba(33, 196, 107, 0.12); border-color: rgba(33, 196, 107, 0.3); color: {palette.positive}; }}
        .dq-signal-hold {{ background: rgba(138, 150, 168, 0.12); border-color: rgba(138, 150, 168, 0.3); color: {palette.neutral}; }}
        .dq-signal-sell {{ background: rgba(255, 92, 92, 0.12); border-color: rgba(255, 92, 92, 0.3); color: {palette.critical}; }}
        .dq-signal-reason {{
            margin-top: 0.8rem;
            padding: 0.78rem 0.85rem;
            border-radius: 14px;
            border: 1px solid rgba(68, 163, 255, 0.18);
            background: rgba(68, 163, 255, 0.08);
            color: {palette.primary_text};
            font-size: 0.9rem;
            line-height: 1.45;
        }}
        .dq-matrix {{
            display: grid;
            gap: 0.65rem;
        }}
        .dq-matrix-head, .dq-matrix-row {{
            display: grid;
            grid-template-columns: 1.5fr 0.8fr 1fr 0.7fr 2fr;
            gap: 0.75rem;
            align-items: stretch;
        }}
        .dq-matrix-head {{
            color: {palette.secondary_text};
            font-size: 0.7rem;
            letter-spacing: 0.14rem;
            text-transform: uppercase;
            padding: 0 0.35rem;
        }}
        .dq-matrix-row {{
            background: rgba(8, 13, 22, 0.46);
            border: 1px solid rgba(86, 121, 181, 0.18);
            border-radius: 16px;
            padding: 0.85rem 0.9rem;
        }}
        .dq-matrix-cell {{
            color: {palette.primary_text};
            font-size: 0.92rem;
            line-height: 1.4;
        }}
        .dq-risk-status {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0.24rem 0.62rem;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 900;
            letter-spacing: 0.08rem;
            text-transform: uppercase;
            border: 1px solid transparent;
        }}
        .dq-risk-armed {{ background: rgba(33, 196, 107, 0.14); border-color: rgba(33, 196, 107, 0.3); color: {palette.positive}; }}
        .dq-risk-active {{ background: rgba(68, 163, 255, 0.14); border-color: rgba(68, 163, 255, 0.3); color: {palette.accent_glow}; }}
        .dq-risk-triggered {{ background: rgba(255, 92, 92, 0.14); border-color: rgba(255, 92, 92, 0.3); color: {palette.critical}; }}
        .dq-risk-warning {{ background: rgba(241, 199, 91, 0.14); border-color: rgba(241, 199, 91, 0.3); color: {palette.warning}; }}
        .dq-risk-unavailable {{ background: rgba(138, 150, 168, 0.12); border-color: rgba(138, 150, 168, 0.28); color: {palette.neutral}; }}
        .dq-ops-map {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.9rem;
        }}
        .dq-ops-node {{
            position: relative;
            background: linear-gradient(150deg, rgba(18, 27, 44, 0.88), rgba(12, 16, 25, 0.78));
            border: 1px solid rgba(86, 121, 181, 0.22);
            border-radius: 18px;
            padding: 0.95rem 1rem;
            min-height: 112px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.24);
        }}
        .dq-ops-node::after {{
            content: "";
            position: absolute;
            top: 50%;
            right: -0.45rem;
            width: 0.9rem;
            height: 2px;
            background: rgba(86, 121, 181, 0.42);
        }}
        .dq-ops-node.last::after {{ display: none; }}
        .dq-ops-node-title {{
            color: {palette.primary_text};
            font-size: 0.94rem;
            font-weight: 850;
            margin-bottom: 0.4rem;
        }}
        .dq-ops-node-meta {{
            color: {palette.secondary_text};
            font-size: 0.82rem;
            line-height: 1.4;
        }}
        .dq-ops-node-status {{
            margin-top: 0.7rem;
        }}
        .dq-narrative-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.75rem;
        }}
        .dq-narrative-card {{
            background: linear-gradient(150deg, rgba(18, 27, 44, 0.88), rgba(12, 16, 25, 0.78));
            border: 1px solid rgba(86, 121, 181, 0.22);
            border-radius: 18px;
            padding: 0.95rem 1rem;
            min-height: 116px;
        }}
        .dq-narrative-card .dq-metric-value {{
            font-size: 1.2rem;
        }}
        .dq-empty-state {{
            border-radius: 14px;
            padding: 0.9rem;
            border: 1px dashed {palette.border};
            color: {palette.secondary_text};
            background: rgba(6, 11, 20, 0.25);
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
            .dq-header-grid,
            .dq-ops-map,
            .dq-narrative-grid,
            .dq-matrix-head,
            .dq-matrix-row,
            .dq-signal-grid,
            .dq-status-bar {{
                grid-template-columns: 1fr;
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
    icon_map = {
        "healthy": "▲",
        "warning": "●",
        "error": "!",
        "neutral": "◉",
        "buy": "▲",
        "hold": "•",
        "sell": "▼",
        "armed": "◉",
        "active": "◆",
        "triggered": "!",
        "unavailable": "—",
    }
    icon = icon_map.get(str(style_key or "").lower(), "◉")
    arrow_html = f" <span style='font-size:0.95rem;'>{trend_arrow}</span>" if trend_arrow else ""
    container.markdown(
        f"<div class='dq-metric-card dq-status-{str(style_key or 'neutral').lower()}'><div class='dq-metric-top'><span class='dq-metric-icon' style='color:{color};'>{icon}</span><span class='dq-metric-label'>{label}</span></div><div class='dq-metric-value' style='color:{color};'>{value}{arrow_html}</div></div>",
        unsafe_allow_html=True,
    )


def _badge(container, text, style="healthy"):
    css_class = "dq-badge" if style == "healthy" else "dq-badge dq-badge-neutral"
    container.markdown(f"<span class='{css_class}'>{text}</span>", unsafe_allow_html=True)


def _empty_state(message):
    st.markdown(f"<div class='dq-empty-state'>{message}</div>", unsafe_allow_html=True)


def _render_component_error(component_name: str):
    safe_name = sanitize_text(component_name, "Component")
    st.markdown(
        f"<div class='dq-alert'><strong>{safe_name}</strong> is temporarily unavailable. The rest of the dashboard remains functional.</div>",
        unsafe_allow_html=True,
    )


def _render_with_error_guard(component_name: str, renderer, *args):
    try:
        renderer(*args)
        return True
    except Exception as exc:
        st.warning(f"{sanitize_text(component_name, 'Component')} unavailable ({sanitize_text(type(exc).__name__, 'Error')})")
        _render_component_error(component_name)
        return False


def _safe_plotly_chart(fig, fallback_message):
    if fig is None:
        st.info(fallback_message)
        return
    try:
        st.plotly_chart(fig, width="stretch")
    except Exception:
        st.info(fallback_message)


def render_loading_skeleton():
    st.markdown("<div class='dq-skeleton'></div><div class='dq-skeleton'></div><div class='dq-skeleton'></div>", unsafe_allow_html=True)


def render_header(payload, view):
    clock = build_market_clock()
    refresh_parts = format_compact_timestamp(st.session_state.get("dashboard_last_refresh"))
    worker_parts = format_compact_timestamp((payload.get("latest_run") or {}).get("run_timestamp"))
    now_parts = format_compact_timestamp(datetime.now(timezone.utc).isoformat())
    status_items = build_status_bar_items(payload, view, clock)
    st.markdown(
        f"""
        <div class='dq-shell-header'>
            <div class='dq-build-marker'>{UI_BUILD_LABEL}</div>
            <div class='dq-header-grid'>
                <div>
                    <div class='dq-header-kicker'>DEAL QUANT COMMAND CENTER</div>
                    <div class='dq-header-title'>DEAL QUANT COMMAND CENTER</div>
                    <div class='dq-header-subtitle'>AUTOMATED PAPER MARKET INTELLIGENCE</div>
                    <div class='dq-header-badges'>
                        <span class='dq-chip healthy'>PAPER</span>
                        <span class='dq-chip critical'>LIVE BLOCKED</span>
                        <span class='dq-chip {'healthy' if clock['is_open'] else 'neutral'}'>{clock['label']}</span>
                    </div>
                </div>
                <div class='dq-header-meta'>
                    <div class='dq-header-meta-row'><span class='dq-header-meta-label'>Eastern time</span><span class='dq-header-meta-value' title='{_safe_text(now_parts['full'])}'>{_safe_text(now_parts['time'])}<br><span class='dq-header-meta-label'>{_safe_text(now_parts['date'])}</span></span></div>
                    <div class='dq-header-meta-row'><span class='dq-header-meta-label'>Latest worker timestamp</span><span class='dq-header-meta-value' title='{_safe_text(worker_parts['full'])}'>{_safe_text(worker_parts['time'])}<br><span class='dq-header-meta-label'>{_safe_text(worker_parts['date'])}</span></span></div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    status_col, refresh_col = st.columns([8.0, 1.4])
    with status_col:
        status_html = "".join([f"<span class='dq-chip {item['style']}'>{_safe_text(item['label'])}</span>" for item in status_items])
        st.markdown(f"<div class='dq-status-bar'>{status_html}</div>", unsafe_allow_html=True)
    with refresh_col:
        if st.button("↻ Refresh Data", key="dashboard_refresh_button", help="Refresh dashboard read-only data only"):
            clear_dashboard_cache()
            st.session_state["dashboard_last_refresh"] = datetime.now(timezone.utc).isoformat()
            st.session_state["dashboard_force_refresh"] = True
            st.session_state["dashboard_last_manual_refresh_status"] = "Dashboard data refreshed"
            # Intentional rerun: ensures data is fetched again after cache clear.
            st.rerun()

    st.markdown(
        f"<div class='dq-header-footer' title='{_safe_text(refresh_parts['full'])}'>Dashboard refresh: {_safe_text(refresh_parts['time'])} | {_safe_text(refresh_parts['date'])} | {_safe_text(refresh_parts['relative'])}</div>",
        unsafe_allow_html=True,
    )
    if st.session_state.get("dashboard_last_manual_refresh_status"):
        st.success(st.session_state.get("dashboard_last_manual_refresh_status"))


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


def _next_worker_run_text(payload, view):
    latest_run = (payload.get("latest_run") or {}).get("run_timestamp")
    latest_dt = _parse_iso(latest_run)
    if latest_dt is not None:
        return format_timestamp_eastern((latest_dt.astimezone(EASTERN_TZ) + timedelta(minutes=30)).isoformat())
    return format_timestamp_eastern((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())


def _render_signal_panel(view):
    signal_label = view.get("generated_signal", "HOLD")
    signal_style = str(view.get("signal", {}).get("style", "hold")).lower()
    spread = _as_float(view.get("ma_distance"), 0.0)
    crossover = "Short MA above long MA" if spread > 0 else "Short MA below long MA" if spread < 0 else "MAs are aligned"
    freshness = _safe_text(view.get("latest_market_data_timestamp"), "Waiting for the next market-hours update")
    price = view.get("latest_spy_price")
    reason = _safe_text(view.get("trade_or_skip_reason"), "Waiting for the next market-hours update")
    signal_color = STATUS_COLORS.get(signal_style, STATUS_COLORS["neutral"])
    st.markdown(
        f"""
        <div class='dq-panel dq-signal-panel'>
            <div style='display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;'>
                <div>
                    <div class='dq-panel-title'>RULE-BASED SIGNAL — NOT A PRICE PREDICTION</div>
                    <div class='dq-panel-subtitle'>The decision is derived from market data freshness and moving-average separation.</div>
                </div>
                <span class='dq-signal-chip dq-signal-{signal_style}' style='color:{signal_color}; border-color:{signal_color};'>{signal_label}</span>
            </div>
            <div class='dq-signal-grid'>
                <div class='dq-signal-row'><div class='dq-label'>Current price</div><div class='dq-value'>{_safe_text(format_currency(price) if isinstance(price, (int, float)) else price, 'Waiting')}</div></div>
                <div class='dq-signal-row'><div class='dq-label'>Short MA</div><div class='dq-value'>{_safe_text(format_currency(view.get('short_moving_average')) if isinstance(view.get('short_moving_average'), (int, float)) else view.get('short_moving_average'), 'Waiting')}</div></div>
                <div class='dq-signal-row'><div class='dq-label'>Long MA</div><div class='dq-value'>{_safe_text(format_currency(view.get('long_moving_average')) if isinstance(view.get('long_moving_average'), (int, float)) else view.get('long_moving_average'), 'Waiting')}</div></div>
                <div class='dq-signal-row'><div class='dq-label'>MA spread</div><div class='dq-value'>{format_percent(spread)}</div></div>
                <div class='dq-signal-row'><div class='dq-label'>Crossover condition</div><div class='dq-value'>{_safe_text(crossover)}</div></div>
                <div class='dq-signal-row'><div class='dq-label'>Data freshness</div><div class='dq-value'>{freshness}</div></div>
            </div>
            <div class='dq-signal-reason'>{reason}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_spy_chart_frame(payload, view):
    if st.session_state.get("dashboard_timeframe") not in TIMEFRAME_OPTIONS:
        st.session_state["dashboard_timeframe"] = "1D"
    if st.session_state.get("dashboard_timeframe_selector") != st.session_state.get("dashboard_timeframe"):
        st.session_state["dashboard_timeframe_selector"] = st.session_state.get("dashboard_timeframe", "1D")
    timeframe = st.selectbox("Timeframe", TIMEFRAME_OPTIONS, key="dashboard_timeframe_selector")
    st.session_state["dashboard_timeframe"] = timeframe
    signal_history = payload.get("signal_history") or []
    price_points = build_price_points(signal_history, timeframe=timeframe)
    if len(price_points) < 2:
        _empty_state("More market history is needed before the SPY chart can render.")
        return

    st.markdown(
        f"""
        <div class='dq-chart-frame'>
            <div class='dq-chart-toolbar'>
                <div>
                    <div class='dq-panel-title'>SPY MARKET FRAME</div>
                    <div class='dq-panel-subtitle'>A line chart is used when genuine OHLC data is unavailable.</div>
                </div>
                <div class='dq-chart-badges'>
                    <span class='dq-chip {'healthy' if payload.get('db_connected') else 'warning'}'>{'Fresh data' if payload.get('db_connected') else 'Data stale'}</span>
                    <span class='dq-chip {'healthy' if view.get('generated_signal') == 'BUY' else 'neutral' if view.get('generated_signal') == 'HOLD' else 'critical'}'>{view.get('generated_signal', 'HOLD')} SIGNAL</span>
                    <span class='dq-chip neutral'>{_safe_text(view.get('latest_market_data_timestamp'))}</span>
                </div>
            </div>
            <div class='dq-chart-surface'>
        """,
        unsafe_allow_html=True,
    )

    if go is None:
        chart_points = [{"timestamp": point["timestamp"], "value": point["price"]} for point in price_points]
        fig = build_line_chart(chart_points, "SPY", "value")
        _safe_plotly_chart(fig, "Chart is temporarily unavailable")
        st.markdown("</div></div>", unsafe_allow_html=True)
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[point["timestamp"] for point in price_points],
            y=[point["price"] for point in price_points],
            mode="lines",
            name="SPY price",
            line={"color": "#44a3ff", "width": 2.8},
        )
    )
    if any(point.get("short_ma") is not None for point in price_points):
        fig.add_trace(
            go.Scatter(
                x=[point["timestamp"] for point in price_points],
                y=[point.get("short_ma") for point in price_points],
                mode="lines",
                name="Short MA",
                line={"color": "#21c46b", "width": 1.8, "dash": "solid"},
            )
        )
    if any(point.get("long_ma") is not None for point in price_points):
        fig.add_trace(
            go.Scatter(
                x=[point["timestamp"] for point in price_points],
                y=[point.get("long_ma") for point in price_points],
                mode="lines",
                name="Long MA",
                line={"color": "#f1c75b", "width": 1.8, "dash": "solid"},
            )
        )

    latest_point = price_points[-1]
    fig.add_annotation(
        x=latest_point["timestamp"],
        y=latest_point["price"],
        text=f"Latest {format_currency(latest_point['price'])}",
        showarrow=True,
        arrowhead=2,
        arrowcolor="#44a3ff",
        font={"color": "#e8eefc", "size": 12},
        bgcolor="rgba(7, 12, 20, 0.85)",
        bordercolor="rgba(68, 163, 255, 0.45)",
        borderpad=4,
    )
    fig.update_layout(
        height=430,
        margin={"l": 10, "r": 10, "t": 8, "b": 10},
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        template="plotly_dark",
    )
    _safe_plotly_chart(fig, "Chart is temporarily unavailable")
    st.markdown("</div></div>", unsafe_allow_html=True)


def _ensure_authenticated(expected_password: str) -> bool:
    if st.session_state.get("dashboard_authenticated"):
        return True

    with st.form("dashboard_auth_form", clear_on_submit=False):
        provided_password = st.text_input("Dashboard Password", type="password", key="dashboard_password_input")
        submitted = st.form_submit_button("Enter Command Center", type="primary")
    if submitted:
        if provided_password and check_dashboard_password(provided_password, expected_password):
            st.session_state["dashboard_authenticated"] = True
            st.session_state["dashboard_password_clear_requested"] = True
            st.session_state["dashboard_auth_error"] = ""
            # Intentional rerun: removes password widget immediately after success.
            st.rerun()
            return True
        st.session_state["dashboard_auth_error"] = "Access denied"
    if st.session_state.get("dashboard_auth_error"):
        st.error(st.session_state.get("dashboard_auth_error"))
    return False


def _render_navigation(pages: list[str]) -> str:
    st.markdown("<div class='dq-nav-bar'>", unsafe_allow_html=True)
    current_page = st.session_state.get("dashboard_page", pages[0])
    if current_page not in pages:
        current_page = pages[0]
    if st.session_state.get("dashboard_page_selector") not in pages:
        st.session_state["dashboard_page_selector"] = current_page
    selected = st.selectbox("Navigate", pages, key="dashboard_page_selector")
    st.session_state["dashboard_page"] = selected
    st.markdown("</div>", unsafe_allow_html=True)
    return selected


def render_command_center_page(payload, view):
    if st.session_state.get("dashboard_focus_mode"):
        st.caption("Focus Mode: secondary content hidden")
        focus_top = st.columns(3)
        _metric_card(focus_top[0], "Paper Portfolio Value", format_currency(view["portfolio_value"]), "neutral", _direction_arrow(view.get("portfolio_value"), view.get("previous_portfolio_value")))
        _metric_card(focus_top[1], "Current Signal", view.get("generated_signal", "HOLD"), view.get("signal", {}).get("style", "neutral"))
        _metric_card(focus_top[2], "Bot health", view["bot_health"]["label"], view["bot_health"]["style"])
        focus_left, focus_right = st.columns([2.2, 1.0])
        with focus_left:
            _render_spy_chart_frame(payload, view)
        with focus_right:
            _render_signal_panel(view)
        notices = build_notification_items(payload, view)
        latest_notice = notices[0] if notices else {"severity": "Info", "message": "No alerts", "timestamp": view.get("last_run_timestamp")}
        st.info(f"Latest alert: {sanitize_text(latest_notice.get('message'), 'No alerts')}")
        return

    top = st.columns(4)
    _metric_card(top[0], "Paper Portfolio Value", format_currency(view["portfolio_value"]), "neutral", _direction_arrow(view.get("portfolio_value"), view.get("previous_portfolio_value")))
    _metric_card(top[1], "Today's Paper P&L", format_currency(view["today_pl"]), "buy" if view["today_pl"] >= 0 else "sell")
    _metric_card(top[2], "Current Signal", view.get("generated_signal", "HOLD"), view.get("signal", {}).get("style", "neutral"))
    _metric_card(top[3], "Bot health", view["bot_health"]["label"], view["bot_health"]["style"])

    body_left, body_right = st.columns([2.05, 1.0])
    with body_left:
        _render_spy_chart_frame(payload, view)
    with body_right:
        _render_signal_panel(view)


def render_sidebar(payload):
    with st.sidebar:
        st.subheader("DEAL QUANT")
        st.markdown("Read-only controls")
        mode = st.selectbox("Dashboard mode", MODE_OPTIONS, key="dashboard_mode_selector")
        if mode not in MODE_OPTIONS:
            mode = st.session_state.get("dashboard_mode", "Standard Mode")
        st.session_state["dashboard_mode"] = mode
        _refresh_mode_flags()
        if mode != "Presentation Mode":
            theme = st.selectbox("Theme", THEME_OPTIONS, key="dashboard_theme_selector")
            if theme not in THEME_OPTIONS:
                theme = st.session_state.get("dashboard_theme", "Midnight Blue")
            st.session_state["dashboard_theme"] = theme
            refresh_choice = st.selectbox("Auto-refresh", AUTO_REFRESH_OPTIONS, key="dashboard_auto_refresh_selector")
            st.session_state["dashboard_auto_refresh"] = refresh_choice
        else:
            st.session_state["dashboard_theme"] = st.session_state.get("dashboard_theme", "Midnight Blue")
            st.session_state["dashboard_auto_refresh"] = "Off"
            st.info("Presentation Mode active")
        st.write(f"Environment: {friendly_status_text(os.getenv('TRADING_MODE', 'PAPER'))}")
        st.write(f"Database connected: {'yes' if payload.get('db_connected') else 'no'}")
        st.write(f"Last data refresh: {format_timestamp_eastern(st.session_state.get('dashboard_last_refresh'))}")
        st.write(f"Last DB refresh: {format_timestamp_eastern(st.session_state.get('dashboard_last_db_refresh'))}")
        st.caption("No trading controls")
        st.caption(f"Dashboard version {DASHBOARD_VERSION}")


def render_overview_page(payload, view):
    render_command_center_page(payload, view)


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
    _render_spy_chart_frame(payload, view)


def render_strategy_page(payload, view):
    st.markdown("<div class='dq-section-tag'>STRATEGY</div>", unsafe_allow_html=True)
    _render_signal_panel(view)
    st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)
    _render_spy_chart_frame(payload, view)


def render_account_page(payload, view):
    st.markdown("<div class='dq-section-tag'>PORTFOLIO</div>", unsafe_allow_html=True)
    acc_cols = st.columns(3)
    _metric_card(acc_cols[0], "Portfolio Value", format_currency(view["portfolio_value"]), "neutral")
    _metric_card(acc_cols[1], "Cash", format_currency(view["cash"]), "neutral")
    _metric_card(acc_cols[2], "Buying Power", format_currency(view["buying_power"]), "neutral")

    second_row = st.columns(3)
    _metric_card(second_row[0], "Unrealized P&L", format_currency(view["unrealized_paper_pl"]), "buy" if view["unrealized_paper_pl"] >= 0 else "sell")
    _metric_card(second_row[1], "Realized P&L", format_currency(view["realized_paper_pl"]), "buy" if view["realized_paper_pl"] >= 0 else "sell")
    _metric_card(second_row[2], "Account Status", view["account_status"], "healthy" if view["account_status"].lower() == "active" else "warning")

    st.markdown("### Portfolio Allocation", unsafe_allow_html=True)
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
    st.markdown("<div class='dq-section-tag'>RISK</div>", unsafe_allow_html=True)
    rows = view.get("risk_matrix") or []
    if not rows:
        _empty_state("Risk data is not available right now.")
        return

    def _risk_style(status):
        normalized = str(status or "").strip().lower()
        if normalized == "armed":
            return "dq-risk-armed"
        if normalized == "active":
            return "dq-risk-active"
        if normalized == "triggered":
            return "dq-risk-triggered"
        if normalized == "warning":
            return "dq-risk-warning"
        return "dq-risk-unavailable"

    st.markdown(
        "<div class='dq-panel'><div class='dq-panel-title'>INSTITUTIONAL RISK MATRIX</div><div class='dq-panel-subtitle'>All visible protections remain read only; none of these controls can place an order from the dashboard.</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='dq-matrix'>", unsafe_allow_html=True)
    st.markdown("<div class='dq-matrix-head'><div>Safeguard</div><div>Limit</div><div>Current usage</div><div>Status</div><div>Explanation</div></div>", unsafe_allow_html=True)
    for row in rows:
        status = str(row.get("status") or "Unavailable").strip()
        if status.lower() == "healthy":
            status = "Active"
        st.markdown(
            f"<div class='dq-matrix-row'><div class='dq-matrix-cell'>{_safe_text(row.get('safeguard'))}</div><div class='dq-matrix-cell'>{_safe_text(row.get('limit'))}</div><div class='dq-matrix-cell'>{_safe_text(row.get('current_usage'))}</div><div class='dq-matrix-cell'><span class='dq-risk-status {_risk_style(status)}'>{_safe_text(status)}</span></div><div class='dq-matrix-cell'>{_safe_text(row.get('explanation'))}</div></div>",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_orders_page(payload):
    rows = build_order_rows(payload.get("recent_orders") or [])
    if not rows:
        st.info("No paper orders yet")
        return

    filter_cols = st.columns(4)
    submitted_filter = filter_cols[0].selectbox("Submitted", ["All", "Submitted", "Not Submitted"], key="orders_submitted_filter")
    signal_filter = filter_cols[1].selectbox("Signal", ["All", "BUY", "HOLD", "SELL"], key="orders_signal_filter")
    stop_reason_filter = filter_cols[2].text_input("Stop reason contains", "", key="orders_stop_reason_filter")
    date_filter = filter_cols[3].text_input("Date contains", "", key="orders_date_filter")

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

    for idx, notice in enumerate(notices):
        notice_id = sanitize_identifier(f"{notice.get('severity', 'info')}-{notice.get('timestamp', '')}-{notice.get('message', '')}-{idx}")
        notice["id"] = notice_id or f"notice-{idx}"

    return notices


def render_notification_center(payload, view):
    st.subheader("Notification Center")
    severity = st.selectbox("Severity", ["All", "Info", "Warning", "Critical"], key="dashboard_alert_severity")
    notices = build_notification_items(payload, view)
    acknowledged = set(st.session_state.get("dashboard_acknowledged_alerts") or [])
    if severity != "All":
        notices = [n for n in notices if n["severity"] == severity]
    if not notices:
        _empty_state("No notifications for the selected severity")
        return
    for idx, notice in enumerate(notices):
        style_key, _ = _event_style(notice["severity"].lower())
        color = STATUS_COLORS.get(style_key, STATUS_COLORS["neutral"])
        cols = st.columns([8, 2])
        with cols[0]:
            marker = "Acknowledged" if notice.get("id") in acknowledged else "New"
            st.markdown(
                f"<div class='dq-card'><span class='dq-alert-pill' style='color:{color};'>{notice['severity']}</span><span class='dq-alert-pill'>{marker}</span><span class='dq-value'>{_safe_text(notice['message'])}</span><div class='dq-label'>{_safe_text(notice['timestamp'])}</div></div>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            if notice.get("id") in acknowledged:
                st.caption("Acknowledged")
            elif st.button("Acknowledge", key=f"ack_alert_{notice.get('id')}_{idx}"):
                acknowledged.add(notice.get("id"))
                st.session_state["dashboard_acknowledged_alerts"] = sorted(acknowledged)
                st.success("Alert acknowledged for this session")


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
    st.markdown("<div class='dq-section-tag'>OPERATIONS</div>", unsafe_allow_html=True)
    statuses = _component_status(payload, view)
    nodes = [
        ("GitHub", "Source control and release history", statuses.get("GitHub", "warning")),
        ("Railway worker", "Primary paper-trading worker", statuses.get("Railway worker", "warning")),
        ("Railway cron", "Scheduled execution path", statuses.get("Railway cron", "warning")),
        ("Railway volume", "Persistent monitoring state", statuses.get("Railway volume", "warning")),
        ("Alpaca Paper", "Broker connectivity and auth", statuses.get("Alpaca Paper", "warning")),
        ("PostgreSQL", "Read-only monitoring database", statuses.get("PostgreSQL", "warning")),
        ("Discord notifier", "Read-only alert delivery", statuses.get("Discord notifications", "warning")),
        ("Streamlit dashboard", "This command center UI", statuses.get("Streamlit dashboard", "warning")),
    ]

    def _node_style(status):
        normalized = str(status or "").strip().lower()
        if normalized == "healthy":
            return "dq-risk-armed", "Armed"
        if normalized == "warning":
            return "dq-risk-warning", "Warning"
        return "dq-risk-unavailable", "Unavailable"

    st.markdown(
        "<div class='dq-panel'><div class='dq-panel-title'>OPERATIONS MAP</div><div class='dq-panel-subtitle'>The visible stack below shows the read-only operational path; no controls here can place trades.</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='dq-ops-map'>", unsafe_allow_html=True)
    for idx, (title, description, status) in enumerate(nodes):
        status_class, status_text = _node_style(status)
        last_class = " last" if (idx % 4) == 3 else ""
        st.markdown(
            f"<div class='dq-ops-node{last_class}'><div class='dq-ops-node-title'>{title}</div><div class='dq-ops-node-meta'>{description}</div><div class='dq-ops-node-status'><span class='dq-risk-status {status_class}'>{status_text}</span></div></div>",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


render_operations_page = render_architecture_page
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
    st.markdown("### RESEARCH JOURNAL — READ ONLY")
    payload = st.session_state.get("dashboard_research_payload") or {
        "db_connected": False,
        "latest_research_run": {},
        "recent_research_runs": [],
        "selected_research_run_id": "",
        "selected_research_candidates": [],
        "research_analytics": {
            "total_research_runs": 0,
            "total_candidate_observations": 0,
            "average_candidates_per_run": 0.0,
            "average_overall_score": 0.0,
            "average_confidence": 0.0,
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
            "db_connected": False,
            "latest_labeling_run": {},
            "recent_labeled_observations": [],
            "recent_label_failures": [],
            "selected_horizon": "20d",
            "evaluation_analytics": {
                "benchmark_symbol": BENCHMARK_SYMBOL,
                "total_observations": 0,
                "labeled_candidates": 0,
                "status_counts": {"pending": 0, "partial": 0, "complete": 0, "unavailable": 0, "data_error": 0},
                "horizons": {},
                "score_buckets": {},
                "confidence_buckets": {},
                "regime_analysis": {},
                "sector_analysis": {},
                "signal_analysis": {},
                "rank_analysis": {},
                "recurring_symbol_analysis": {},
                "correlations": {},
                "latest_attempted_at": None,
            },
            "evaluation_config": {},
        },
    }
    analytics = payload.get("research_analytics") or {}
    latest_run = payload.get("latest_research_run") or {}
    recent_runs = list(payload.get("recent_research_runs") or [])
    selected_run_id = payload.get("selected_research_run_id") or latest_run.get("research_run_id") or ""
    if recent_runs:
        run_options = [run.get("research_run_id") for run in recent_runs if run.get("research_run_id")]
        if run_options:
            default_index = run_options.index(selected_run_id) if selected_run_id in run_options else 0
            selected_run_id = st.selectbox("Select research scan", run_options, index=default_index)
            if selected_run_id != payload.get("selected_research_run_id"):
                payload = load_research_summary(os.getenv("DATABASE_URL"), selected_run_id=selected_run_id)
                st.session_state["dashboard_research_payload"] = payload
                analytics = payload.get("research_analytics") or {}
                latest_run = payload.get("latest_research_run") or {}

    top_cols = st.columns(6)
    _metric_card(top_cols[0], "Stored Scans", analytics.get("total_research_runs", 0), "neutral")
    _metric_card(top_cols[1], "Stored Candidates", analytics.get("total_candidate_observations", 0), "neutral")
    _metric_card(top_cols[2], "Avg Score", f"{float(analytics.get('average_overall_score', 0.0)):.1f}", "healthy")
    _metric_card(top_cols[3], "Avg Confidence", f"{float(analytics.get('average_confidence', 0.0)):.1f}", "healthy")
    _metric_card(top_cols[4], "Avg Cand/Run", f"{float(analytics.get('average_candidates_per_run', 0.0)):.1f}", "warning")
    _metric_card(top_cols[5], "Latest Scan", _safe_text(latest_run.get("completed_at") or latest_run.get("started_at"), "N/A"), "neutral")

    st.markdown("### Latest Research Run")
    latest_cols = st.columns(4)
    _metric_card(latest_cols[0], "Benchmark", _safe_text(latest_run.get("benchmark_symbol"), "N/A"), "neutral")
    _metric_card(latest_cols[1], "Market Regime", _safe_text(latest_run.get("market_regime"), "unknown"), "neutral")
    _metric_card(latest_cols[2], "Universe Size", latest_run.get("universe_size", 0), "neutral")
    _metric_card(latest_cols[3], "Duration (s)", f"{float(latest_run.get('scanner_duration_seconds', 0.0)):.2f}", "warning")

    detail_cols = st.columns(4)
    _metric_card(detail_cols[0], "Eligible", latest_run.get("eligible_count", 0), "healthy")
    _metric_card(detail_cols[1], "Rejected", latest_run.get("rejected_count", 0), "warning")
    _metric_card(detail_cols[2], "Errors", latest_run.get("error_count", 0), "sell")
    _metric_card(detail_cols[3], "Data Source", _safe_text(latest_run.get("data_source"), "N/A"), "neutral")

    st.markdown("### Recent Scans")
    if recent_runs:
        st.dataframe(recent_runs)
    else:
        st.info("No research scans stored yet.")

    st.markdown("### Candidate Table")
    candidates = list(payload.get("selected_research_candidates") or [])
    candidate_rows = [
        {
            "rank": row.get("rank"),
            "symbol": row.get("symbol"),
            "sector": row.get("sector"),
            "signal": row.get("signal"),
            "score": row.get("overall_score"),
            "confidence": row.get("confidence"),
            "trend": row.get("trend_score"),
            "momentum": row.get("momentum_score"),
            "volume": row.get("volume_score"),
            "volatility": row.get("volatility_score"),
            "risk_quality": row.get("risk_quality_score"),
        }
        for row in candidates
    ]
    if candidate_rows:
        st.dataframe(candidate_rows[:100])
    else:
        st.info("No candidates available for the selected research scan.")

    st.markdown("### Analytics")
    analytics_cols = st.columns(3)
    _metric_card(analytics_cols[0], "Score Distribution", len(analytics.get("score_distribution", [])), "neutral")
    _metric_card(analytics_cols[1], "Confidence Distribution", len(analytics.get("confidence_distribution", [])), "neutral")
    _metric_card(analytics_cols[2], "Top Symbols", len(analytics.get("top_recurring_symbols", [])), "neutral")

    chart_cols = st.columns(2)
    with chart_cols[0]:
        st.dataframe(analytics.get("candidate_count_by_sector") or [])
    with chart_cols[1]:
        st.dataframe(analytics.get("candidate_count_by_regime") or [])

    st.markdown("### Strategy Evaluation")
    evaluation = payload.get("evaluation") or {}
    evaluation_analytics = evaluation.get("evaluation_analytics") or {}
    horizon_keys = list((evaluation_analytics.get("horizons") or {}).keys())
    if not horizon_keys:
        horizon_keys = ["1d", "5d", "10d", "20d"]
    selected_horizon = evaluation.get("selected_horizon") or horizon_keys[-1]
    selected_horizon = st.selectbox("Evaluation horizon", horizon_keys, index=horizon_keys.index(selected_horizon) if selected_horizon in horizon_keys else len(horizon_keys) - 1, key="dashboard_evaluation_horizon")
    selected_horizon_metrics = (evaluation_analytics.get("horizons") or {}).get(selected_horizon) or {}

    overview_cols = st.columns(5)
    status_counts = evaluation_analytics.get("status_counts") or {}
    _metric_card(overview_cols[0], "Total Observations", evaluation_analytics.get("total_observations", 0), "neutral")
    _metric_card(overview_cols[1], "Complete Labels", status_counts.get("complete", 0), "healthy")
    _metric_card(overview_cols[2], "Partial Labels", status_counts.get("partial", 0), "warning")
    _metric_card(overview_cols[3], "Pending Labels", status_counts.get("pending", 0), "neutral")
    _metric_card(overview_cols[4], "Unavailable Labels", status_counts.get("unavailable", 0), "sell")

    label_cols = st.columns(4)
    _metric_card(label_cols[0], "Latest Label Attempt", _safe_text(evaluation_analytics.get("latest_attempted_at"), "N/A"), "neutral")
    _metric_card(label_cols[1], "Benchmark", _safe_text(evaluation_analytics.get("benchmark_symbol"), "N/A"), "neutral")
    _metric_card(label_cols[2], "Labeled Candidates", evaluation_analytics.get("labeled_candidates", 0), "healthy")
    _metric_card(label_cols[3], "Data Errors", status_counts.get("data_error", 0), "warning")

    horizon_cols = st.columns(6)
    _metric_card(horizon_cols[0], f"{selected_horizon.upper()} Sample", selected_horizon_metrics.get("sample_size", 0), "neutral")
    _metric_card(horizon_cols[1], "Avg Return", f"{float(selected_horizon_metrics.get('average_raw_return') or 0.0):.4f}", "healthy")
    _metric_card(horizon_cols[2], "Avg Benchmark", f"{float(selected_horizon_metrics.get('average_benchmark_return') or 0.0):.4f}", "neutral")
    _metric_card(horizon_cols[3], "Avg Excess", f"{float(selected_horizon_metrics.get('average_excess_return') or 0.0):.4f}", "healthy")
    _metric_card(horizon_cols[4], "Positive Return", f"{100.0 * float(selected_horizon_metrics.get('positive_return_rate') or 0.0):.1f}%", "healthy")
    _metric_card(horizon_cols[5], "Positive Excess", f"{100.0 * float(selected_horizon_metrics.get('positive_excess_return_rate') or 0.0):.1f}%", "healthy")

    evaluation_tables = st.columns(2)
    with evaluation_tables[0]:
        st.markdown("#### Score Analysis")
        st.dataframe((evaluation_analytics.get("score_buckets") or {}).get(selected_horizon) or [])
        st.markdown("#### Regime Analysis")
        st.dataframe((evaluation_analytics.get("regime_analysis") or {}).get(selected_horizon) or [])
        st.markdown("#### Rank Analysis")
        st.dataframe((evaluation_analytics.get("rank_analysis") or {}).get(selected_horizon) or [])
    with evaluation_tables[1]:
        st.markdown("#### Confidence Analysis")
        st.dataframe((evaluation_analytics.get("confidence_buckets") or {}).get(selected_horizon) or [])
        st.markdown("#### Sector Analysis")
        st.dataframe((evaluation_analytics.get("sector_analysis") or {}).get(selected_horizon) or [])
        st.markdown("#### Signal Analysis")
        st.dataframe((evaluation_analytics.get("signal_analysis") or {}).get(selected_horizon) or [])

    st.markdown("#### Correlations")
    correlation_rows = []
    for key, value in ((evaluation_analytics.get("correlations") or {}).get(selected_horizon) or {}).items():
        if key == "sample_size":
            continue
        correlation_rows.append({"metric": key, "value": value})
    st.dataframe(correlation_rows)

    st.markdown("#### Recurring Symbols")
    recurring_rows = (evaluation_analytics.get("recurring_symbol_analysis") or {}).get(selected_horizon) or []
    st.dataframe(recurring_rows)

    st.markdown("#### Recent Labeled Observations")
    recent_labels = evaluation.get("recent_labeled_observations") or []
    recent_label_rows = [
        {
            "observation_date": row.get("observation_date"),
            "symbol": row.get("symbol"),
            "rank": row.get("rank"),
            "score": row.get("overall_score"),
            "confidence": row.get("confidence"),
            "sector": row.get("sector"),
            "regime": row.get("market_regime"),
            "1d_return": row.get("forward_1d_return"),
            "5d_return": row.get("forward_5d_return"),
            "10d_return": row.get("forward_10d_return"),
            "20d_return": row.get("forward_20d_return"),
            "1d_excess": row.get("forward_1d_excess_return"),
            "5d_excess": row.get("forward_5d_excess_return"),
            "10d_excess": row.get("forward_10d_excess_return"),
            "20d_excess": row.get("forward_20d_excess_return"),
            "label_status": row.get("label_status"),
        }
        for row in recent_labels
    ]
    st.dataframe(recent_label_rows)

    st.markdown("#### Recent Label Failures")
    failure_rows = [
        {
            "observation_date": row.get("observation_date"),
            "symbol": row.get("symbol"),
            "status": row.get("label_status"),
            "error_message": row.get("error_message"),
            "last_attempted_at": row.get("last_attempted_at"),
        }
        for row in (evaluation.get("recent_label_failures") or [])
    ]
    st.dataframe(failure_rows)

    st.markdown("#### Read-only evaluation exports")
    evaluation_export_payload = {
        "analytics": evaluation_analytics,
        "recent_labeled_observations": recent_label_rows,
        "recent_label_failures": failure_rows,
    }
    evaluation_export_blobs = {
        "analytics": json.dumps(evaluation_export_payload["analytics"], indent=2, sort_keys=True),
        "recent_labeled_observations": json.dumps(evaluation_export_payload["recent_labeled_observations"], indent=2, sort_keys=True),
        "recent_label_failures": json.dumps(evaluation_export_payload["recent_label_failures"], indent=2, sort_keys=True),
    }
    if hasattr(st, "download_button"):
        for label, rows, blob, file_name, key in [
            ("Evaluation analytics JSON", recent_label_rows, evaluation_export_blobs["analytics"], "strategy_evaluation_analytics.json", "download_strategy_evaluation_analytics"),
            ("Recent labeled observations JSON", recent_label_rows, evaluation_export_blobs["recent_labeled_observations"], "recent_labeled_observations.json", "download_recent_labeled_observations"),
            ("Recent label failures JSON", failure_rows, evaluation_export_blobs["recent_label_failures"], "recent_label_failures.json", "download_recent_label_failures"),
        ]:
            has_rows = bool(rows)
            if not has_rows:
                st.info(f"No data available for {label}")
            st.download_button(label, blob, file_name=sanitize_identifier(file_name.replace(".json", "")) + ".json", mime="application/json", key=key, disabled=not has_rows)

    st.markdown("### Read-only exports")
    export_payload = {
        "recent_runs": recent_runs,
        "candidates": candidate_rows,
        "analytics": analytics,
    }
    export_blobs = {
        "recent_runs": json.dumps(export_payload["recent_runs"], indent=2, sort_keys=True),
        "candidates": json.dumps(export_payload["candidates"], indent=2, sort_keys=True),
        "analytics": json.dumps(export_payload["analytics"], indent=2, sort_keys=True),
    }
    if hasattr(st, "download_button"):
        exports = [
            ("Recent research scans JSON", export_payload.get("recent_runs", []), export_blobs["recent_runs"], "research_runs.json", "application/json", "download_research_runs"),
            ("Research candidates JSON", export_payload.get("candidates", []), export_blobs["candidates"], "research_candidates.json", "application/json", "download_research_candidates"),
            ("Research analytics JSON", export_payload.get("recent_runs", []) or export_payload.get("candidates", []), export_blobs["analytics"], "research_analytics.json", "application/json", "download_research_analytics"),
        ]
        for label, rows, blob, file_name, mime_type, key in exports:
            has_rows = bool(rows)
            if not has_rows:
                st.info(f"No data available for {label}")
            st.download_button(
                label,
                blob,
                file_name=sanitize_identifier(file_name.replace(".json", "")) + ".json",
                mime=mime_type,
                key=key,
                disabled=not has_rows,
            )


def render_dashboard(database_url: str | None = None):
    if st is None:
        raise RuntimeError("streamlit is required to run the dashboard")

    enforce_paper_mode(os.getenv("TRADING_MODE", "PAPER"))
    st.set_page_config(page_title="DEAL QUANT COMMAND CENTER", layout="wide")

    initialize_dashboard_session_state()
    _refresh_mode_flags()

    if st.session_state.pop("dashboard_password_clear_requested", False):
        st.session_state.pop("dashboard_password_input", None)

    apply_dashboard_css(st.session_state.get("dashboard_theme", "Midnight Blue"))

    expected_password = os.getenv("DASHBOARD_PASSWORD", "")
    if not _ensure_authenticated(expected_password):
        st.stop()

    if st.session_state.get("dashboard_force_refresh"):
        clear_dashboard_cache()
        st.session_state["dashboard_force_refresh"] = False

    try:
        with st.spinner("Loading command center..."):
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
    st.session_state["dashboard_last_refresh"] = datetime.now(timezone.utc).isoformat()
    st.session_state["dashboard_export_payload"] = {
        "activity": build_activity_feed(payload, view),
        "signals": payload.get("signal_history") or [],
        "orders": payload.get("recent_orders") or [],
        "health": [{"component": component, "status": status, "timestamp": view.get("last_run_timestamp"), "reason": "Read only"} for component, status in _component_status(payload, view).items()],
        "performance": [{"metric": "portfolio_value", "value": view.get("portfolio_value")}, {"metric": "today_pl", "value": view.get("today_pl")}, {"metric": "total_pl", "value": view.get("total_pl")}],
    }
    st.session_state["dashboard_research_payload"] = payload.get("research") or {}
    render_sidebar(payload)
    if st.session_state.get("dashboard_presentation_mode"):
        if st.button("Exit Presentation Mode", key="dashboard_exit_presentation"):
            st.session_state["dashboard_mode"] = "Standard Mode"
            st.session_state["dashboard_mode_selector"] = "Standard Mode"
            _refresh_mode_flags()
            st.rerun()
    apply_dashboard_css(st.session_state.get("dashboard_theme", "Midnight Blue"))

    refresh_mapping = {
        "Off": 0,
        "30 seconds": 30,
        "60 seconds": 60,
        "5 minutes": 300,
    }
    refresh_seconds = refresh_mapping.get(st.session_state.get("dashboard_auto_refresh", "Off"), 0)
    if refresh_seconds > 0 and st_autorefresh is not None:
        st_autorefresh(interval=refresh_seconds * 1000, key="dashboard_auto_refresh_tick")

    _render_with_error_guard("Header", render_header, payload, view)
    selected_page = _render_navigation(PAGE_OPTIONS)
    st.session_state["dashboard_page"] = selected_page

    page_renderers = {
        "Command Center": render_overview_page,
        "Strategy": render_strategy_page,
        "Risk": render_risk_page,
        "Portfolio": render_account_page,
        "Orders": render_orders_page,
        "Performance": render_performance_page,
        "Operations": render_operations_page,
        "Alerts": render_alerts_page,
        "Research": render_research_page,
        "Factor Attribution": render_factor_attribution_page,
    }
    page_renderer = page_renderers.get(selected_page, render_overview_page)
    if selected_page == "Research":
        _render_with_error_guard("Research", page_renderer)
    elif selected_page in {"Orders", "Performance"}:
        _render_with_error_guard(selected_page, page_renderer, payload)
    else:
        _render_with_error_guard(selected_page, page_renderer, payload, view)


def main():
    render_dashboard()


if __name__ == "__main__":
    main()
