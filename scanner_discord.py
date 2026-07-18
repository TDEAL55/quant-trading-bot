from __future__ import annotations

from typing import Any


def build_scanner_completion_summary(summary: dict[str, Any], top_candidates: list[dict[str, Any]]) -> str:
    lines = [
        "RESEARCH SCANNER SUMMARY",
        f"Universe: {summary.get('symbol_count', 0)}",
        f"Scored: {summary.get('success_count', 0)}",
        f"Rejected: {summary.get('rejection_count', 0)}",
        f"Errors: {summary.get('error_count', 0)}",
        f"Eligible: {summary.get('eligible_count', 0)}",
        "",
        "Top Research Candidates:",
    ]
    for item in top_candidates[:5]:
        lines.append(
            f"- #{item.get('rank')} {item.get('symbol')} score={float(item.get('overall_score', 0.0)):.1f} conf={float(item.get('confidence', 0.0)):.1f} signal={item.get('signal')}"
        )
    lines.append("No trade instructions included. Research-only output.")
    return "\n".join(lines)


def build_scanner_failure_summary(message: str, error_count: int) -> str:
    return "\n".join(
        [
            "RESEARCH SCANNER FAILURE",
            f"Errors: {error_count}",
            f"Message: {message}",
            "No trade instructions included. Research-only output.",
        ]
    )


def build_position_warning_summary(position_reviews: list[dict[str, Any]]) -> str:
    flagged = [item for item in position_reviews if item.get("recommendation") in {"REDUCE", "EXIT"}]
    if not flagged:
        return "RESEARCH POSITION REVIEW: no major position warnings."
    lines = ["RESEARCH POSITION REVIEW WARNINGS"]
    for item in flagged[:5]:
        lines.append(
            f"- {item.get('symbol')}: {item.get('recommendation')} (score={float(item.get('score', 0.0)):.1f}, confidence={float(item.get('confidence', 0.0)):.1f})"
        )
    lines.append("Recommendations only. No orders submitted.")
    return "\n".join(lines)
