import json
from datetime import datetime, timezone

from export_paper_performance import build_paper_performance_artifact
from performance_comparison import build_performance_comparison


def _fill(order_id, symbol, side, price, minute, client_id):
    return {
        "order_id": order_id,
        "client_order_id": client_id,
        "symbol": symbol,
        "asset_class": "us_equity",
        "side": side,
        "status": "filled",
        "filled_quantity": 1,
        "average_fill_price": price,
        "filled_at": f"2026-09-09T14:{minute:02d}:00+00:00",
    }


def test_export_excludes_manual_exits_and_labels_historical_sample():
    orders = [
        _fill("early-entry", "OLD", "buy", 20, 0, "qtb-early-entry"),
        _fill("manual-exit", "OLD", "sell", 19, 1, "manual-exit"),
        _fill("entry", "NEW", "buy", 50, 2, "qtb-entry"),
        _fill("exit", "NEW", "sell", 55, 3, "qtb-exit-new"),
    ]
    artifact = build_paper_performance_artifact(
        orders,
        [{"order_id": "entry", "strategy_id": "stock_trend_ensemble_v2"}],
        generated_at=datetime(2026, 11, 26, tzinfo=timezone.utc),
    )

    assert artifact["metrics"]["number_of_trades"] == 1
    assert artifact["metrics"]["total_pnl"] == 5
    assert artifact["data_quality"]["reconstruction_confidence"] == "partial"
    assert artifact["provenance"]["manual_stock_fill_count"] == 1
    assert artifact["provenance"]["unattributed_bot_entry_count"] == 1
    assert artifact["provenance"]["comparable_to_live"] is False
    json.dumps(artifact, allow_nan=False)
    comparison = build_performance_comparison(backtest={}, paper=artifact, live={})
    assert comparison["comparison_ready"] is False
    assert "not_like_for_like:paper" in comparison["flags"]

