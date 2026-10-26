from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


EVENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "election_or_policy": (
        " election",
        "election ",
        "run-off",
        "runoff",
        "referendum",
        "sanctions",
        "tariff",
    ),
    "earnings_or_guidance": (
        "earnings",
        "reports results",
        "report results",
        "guidance cut",
        "guidance raise",
    ),
    "regulatory_or_legal": (
        "fda decision",
        "court ruling",
        "regulatory decision",
        "antitrust",
        "investigation",
    ),
    "capital_or_solvency": (
        "bankruptcy",
        "default",
        "secondary offering",
        "public offering",
        "going concern",
    ),
    "corporate_action": (
        "merger vote",
        "tender offer",
        "acquisition vote",
        "spin-off",
        "spinoff",
    ),
    "market_shock": (
        "record crash",
        "record single-day crash",
        "plunged premarket",
        "trading halt",
    ),
}


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_news_event_risk(
    articles: Iterable[Mapping[str, Any]] | None,
    *,
    now: datetime,
    lookback_hours: int,
) -> dict[str, dict[str, Any]]:
    """Classify objective binary-event phrases; never infer risk from sentiment alone."""
    observed_at = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    by_symbol: dict[str, dict[str, Any]] = {}
    for raw in list(articles or []):
        article = dict(raw or {})
        published = _timestamp(
            article.get("created_at") or article.get("updated_at") or article.get("timestamp")
        )
        if published is None:
            continue
        age_hours = max((observed_at - published).total_seconds() / 3600.0, 0.0)
        if age_hours > max(int(lookback_hours), 1):
            continue
        text = f" {article.get('headline') or ''} {article.get('summary') or ''} ".lower()
        matched_categories = sorted(
            category
            for category, patterns in EVENT_PATTERNS.items()
            if any(pattern in text for pattern in patterns)
        )
        if not matched_categories:
            continue
        for raw_symbol in list(article.get("symbols") or []):
            symbol = str(raw_symbol or "").strip().upper()
            if not symbol:
                continue
            record = by_symbol.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "entry_blocked": True,
                    "categories": [],
                    "evidence": [],
                    "source": "recent_broker_news",
                },
            )
            record["categories"] = sorted(
                set(record.get("categories") or []).union(matched_categories)
            )
            evidence = list(record.get("evidence") or [])
            if len(evidence) < 3:
                evidence.append(
                    {
                        "headline": str(article.get("headline") or ""),
                        "published_at": published.isoformat(),
                        "categories": matched_categories,
                    }
                )
            record["evidence"] = evidence
    return by_symbol


def build_event_risk_reviews(
    event_risk_by_symbol: Mapping[str, Mapping[str, Any]] | None,
    *,
    held_symbols: Iterable[str] = (),
) -> list[dict[str, Any]]:
    held = {str(symbol or "").strip().upper() for symbol in held_symbols}
    reviews: list[dict[str, Any]] = []
    for raw_symbol, raw in sorted(dict(event_risk_by_symbol or {}).items()):
        symbol = str(raw_symbol or "").strip().upper()
        row = dict(raw or {})
        is_held = symbol in held
        reviews.append(
            {
                **row,
                "symbol": symbol,
                "held_position": is_held,
                "mode": "ENTRY_BLOCK_AND_HELD_POSITION_REVIEW" if is_held else "ENTRY_BLOCK",
                "recommendation": (
                    "REVIEW_BEFORE_NEXT_CLOSE" if is_held else "BLOCK_NEW_ENTRY"
                ),
                "actionable": False,
                "orders_changed": False,
            }
        )
    return reviews


def build_premarket_gap_shadow_reviews(
    positions: Mapping[str, Mapping[str, Any]],
    open_orders: Iterable[Mapping[str, Any]],
    latest_prices: Mapping[str, float],
    *,
    observed_at: datetime,
    alert_percent: float,
) -> list[dict[str, Any]]:
    """Report premarket stop gaps without changing or canceling live orders."""
    now = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    market_now = now.astimezone(ZoneInfo("America/New_York"))
    if market_now.weekday() >= 5 or not (4 <= market_now.hour < 9 or (market_now.hour == 9 and market_now.minute < 30)):
        return []
    stops: dict[str, float] = {}
    for raw in list(open_orders or []):
        order = dict(raw or {})
        if str(order.get("side") or "").lower() != "sell":
            continue
        if str(order.get("type") or order.get("order_type") or "").lower() not in {"stop", "stop_limit"}:
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        try:
            stop = float(order.get("stop_price") or 0.0)
        except (TypeError, ValueError):
            stop = 0.0
        if symbol and stop > 0:
            stops[symbol] = stop

    reviews: list[dict[str, Any]] = []
    for raw_symbol, raw_position in sorted(dict(positions or {}).items()):
        symbol = str(raw_symbol or "").strip().upper()
        position = dict(raw_position or {})
        try:
            latest = float(dict(latest_prices or {}).get(symbol) or 0.0)
            previous_close = float(position.get("lastday_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if latest <= 0 or previous_close <= 0:
            continue
        gap_percent = (latest - previous_close) / previous_close * 100.0
        stop = float(stops.get(symbol) or 0.0)
        below_stop = stop > 0 and latest < stop
        if gap_percent > -abs(float(alert_percent)) and not below_stop:
            continue
        reviews.append(
            {
                "symbol": symbol,
                "mode": "SHADOW_ONLY",
                "status": "alert",
                "previous_close": round(previous_close, 6),
                "latest_premarket_price": round(latest, 6),
                "gap_percent": round(gap_percent, 6),
                "protective_stop_price": round(stop, 6) if stop > 0 else None,
                "below_protective_stop": below_stop,
                "recommendation": (
                    "CONTROLLED_OPENING_EXIT_REVIEW" if below_stop else "PREMARKET_GAP_REVIEW"
                ),
                "actionable": False,
                "orders_changed": False,
            }
        )
    return reviews
