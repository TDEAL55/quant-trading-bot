from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None

from dashboard_components import build_palette
from dashboard_status import EASTERN_TZ


CHART_LAYOUT = {
    "template": "plotly_dark",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "margin": {"l": 8, "r": 8, "t": 18, "b": 8},
    "height": 420,
}


def _apply_chart_style(fig):
    if fig is None:
        return None
    palette = build_palette("Midnight Blue")
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=palette.chart_bg,
        plot_bgcolor=palette.chart_bg,
        font={"family": "Inter, Segoe UI, sans-serif", "color": palette.primary_text},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        xaxis={"gridcolor": palette.chart_grid, "showline": False, "zeroline": False},
        yaxis={"gridcolor": palette.chart_grid, "showline": False, "zeroline": False},
    )
    return fig


def build_line_chart(points: list[dict[str, Any]], title: str, y_key: str, x_key: str = "timestamp"):
    if go is None:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[p[x_key] for p in points], y=[p[y_key] for p in points], mode="lines", name=title))
    fig.update_layout(**CHART_LAYOUT)
    return _apply_chart_style(fig)


def build_market_chart(candles: list[dict[str, Any]], title: str = "SPY"):
    if go is None:
        return None
    if not candles:
        return None
    fig = go.Figure()
    if candles and all(key in candles[0] for key in ("open", "high", "low", "close")):
        fig.add_trace(
            go.Candlestick(
                x=[c["timestamp"] for c in candles],
                open=[c["open"] for c in candles],
                high=[c["high"] for c in candles],
                low=[c["low"] for c in candles],
                close=[c["close"] for c in candles],
                name=title,
            )
        )
    else:
        fig.add_trace(go.Scatter(x=[c["timestamp"] for c in candles], y=[c["close"] for c in candles], mode="lines", name=title))
    fig.update_layout(**CHART_LAYOUT)
    return _apply_chart_style(fig)
