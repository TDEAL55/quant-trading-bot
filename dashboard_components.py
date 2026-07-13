from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DashboardPalette:
    page_bg: str
    panel_bg: str
    elevated_bg: str
    border: str
    positive: str
    negative: str
    neutral: str
    warning: str
    critical: str
    muted_text: str
    primary_text: str
    secondary_text: str
    accent_glow: str
    radius_lg: str
    spacing_sm: str
    spacing_md: str
    spacing_lg: str
    shadow_soft: str
    chart_bg: str
    chart_grid: str


def build_palette(theme_name: str) -> DashboardPalette:
    theme = theme_name.lower().strip()
    if theme == "black terminal":
        return DashboardPalette(
            page_bg="#050505",
            panel_bg="#0b0f0d",
            elevated_bg="#111715",
            border="rgba(46, 203, 112, 0.18)",
            positive="#2ecb70",
            negative="#ff6b6b",
            neutral="#9aa8b2",
            warning="#d8b15c",
            critical="#ff5c5c",
            muted_text="#8ea196",
            primary_text="#edf6ef",
            secondary_text="#b8c7be",
            accent_glow="rgba(46, 203, 112, 0.16)",
            radius_lg="16px",
            spacing_sm="0.45rem",
            spacing_md="0.85rem",
            spacing_lg="1.25rem",
            shadow_soft="0 8px 22px rgba(0, 0, 0, 0.28)",
            chart_bg="#0b0f0d",
            chart_grid="rgba(46, 203, 112, 0.08)",
        )
    if theme == "arctic glass":
        return DashboardPalette(
            page_bg="#eef6fb",
            panel_bg="#f7fbff",
            elevated_bg="#ffffff",
            border="rgba(66, 108, 147, 0.18)",
            positive="#0f8a64",
            negative="#c84d4d",
            neutral="#506070",
            warning="#9a6d1f",
            critical="#c84d4d",
            muted_text="#607086",
            primary_text="#102030",
            secondary_text="#405065",
            accent_glow="rgba(100, 154, 201, 0.16)",
            radius_lg="16px",
            spacing_sm="0.45rem",
            spacing_md="0.85rem",
            spacing_lg="1.25rem",
            shadow_soft="0 8px 22px rgba(62, 96, 133, 0.12)",
            chart_bg="#f7fbff",
            chart_grid="rgba(66, 108, 147, 0.08)",
        )
    return DashboardPalette(
        page_bg="#090c13",
        panel_bg="#111724",
        elevated_bg="#161f31",
        border="rgba(86, 121, 181, 0.18)",
        positive="#21c46b",
        negative="#ff5c5c",
        neutral="#8ea0ba",
        warning="#f1c75b",
        critical="#ff5c5c",
        muted_text="#97a5bb",
        primary_text="#eef3fb",
        secondary_text="#b5c2d6",
        accent_glow="rgba(68, 163, 255, 0.18)",
        radius_lg="16px",
        spacing_sm="0.45rem",
        spacing_md="0.85rem",
        spacing_lg="1.25rem",
        shadow_soft="0 8px 22px rgba(0, 0, 0, 0.26)",
        chart_bg="#111724",
        chart_grid="rgba(86, 121, 181, 0.08)",
    )


def status_style(status: str, palette: DashboardPalette) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"healthy", "armed", "active"}:
        return palette.positive
    if normalized in {"warning", "waiting"}:
        return palette.warning
    if normalized in {"critical", "triggered", "error", "offline"}:
        return palette.critical
    if normalized in {"neutral", "unknown", "unavailable"}:
        return palette.neutral
    return palette.neutral


def metric_payload(label: str, value: Any, status: str = "neutral", detail: str | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "status": status, "detail": detail}
