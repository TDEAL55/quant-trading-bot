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
    if theme == "studio":
        return DashboardPalette(
            page_bg="#0a0b0d",
            panel_bg="#121417",
            elevated_bg="#191c20",
            border="rgba(245, 247, 242, 0.09)",
            positive="#b6f542",
            negative="#ff5d73",
            neutral="#a9b0bb",
            warning="#ffca58",
            critical="#ff5d73",
            muted_text="#747b86",
            primary_text="#f4f6f1",
            secondary_text="#9aa1ac",
            accent_glow="rgba(182, 245, 66, 0.12)",
            radius_lg="12px",
            spacing_sm="0.4rem",
            spacing_md="0.75rem",
            spacing_lg="1.1rem",
            shadow_soft="0 12px 30px rgba(0, 0, 0, 0.28)",
            chart_bg="#121417",
            chart_grid="rgba(245, 247, 242, 0.06)",
        )
    if theme == "aurora":
        return DashboardPalette(
            page_bg="radial-gradient(circle at 8% 0%, #241345 0%, #0e1020 36%, #080a12 100%)",
            panel_bg="rgba(16, 19, 35, 0.92)",
            elevated_bg="rgba(27, 30, 53, 0.94)",
            border="rgba(148, 118, 255, 0.26)",
            positive="#35e6c2",
            negative="#ff5f8f",
            neutral="#b7a7ff",
            warning="#ffc857",
            critical="#ff5f8f",
            muted_text="#8d91aa",
            primary_text="#f7f4ff",
            secondary_text="#b8bad0",
            accent_glow="rgba(123, 92, 255, 0.24)",
            radius_lg="18px",
            spacing_sm="0.45rem",
            spacing_md="0.85rem",
            spacing_lg="1.25rem",
            shadow_soft="0 16px 42px rgba(3, 4, 12, 0.42)",
            chart_bg="#101323",
            chart_grid="rgba(148, 118, 255, 0.10)",
        )
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
