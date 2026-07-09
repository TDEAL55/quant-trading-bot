from data_validation import validate_price_data
from performance_metrics import calculate_metrics
from walk_forward import run_walk_forward


def evaluate_strategy_gate(backtest_result, data, min_trades=3, max_drawdown=0.3, min_sharpe=0.5):
    """Evaluate whether a strategy meets basic research requirements."""
    reasons = []

    try:
        validate_price_data(data)
    except Exception as exc:
        reasons.append(f"data validation failed: {exc}")

    metrics = calculate_metrics(backtest_result)
    trade_count = backtest_result.get("number_of_trades", 0)
    drawdown = backtest_result.get("max_drawdown", 0.0)
    sharpe = metrics.get("sharpe_ratio", 0.0)

    if trade_count < min_trades:
        reasons.append(f"insufficient trades: {trade_count} < {min_trades}")

    if drawdown > max_drawdown:
        reasons.append(f"drawdown too high: {drawdown:.2%} > {max_drawdown:.2%}")

    if sharpe < min_sharpe:
        reasons.append(f"Sharpe ratio too low: {sharpe:.2f} < {min_sharpe:.2f}")

    try:
        walk_forward_results = run_walk_forward()
        if not walk_forward_results:
            reasons.append("walk-forward testing produced no periods")
    except Exception as exc:
        reasons.append(f"walk-forward testing failed: {exc}")

    if reasons:
        return "REJECTED", reasons

    return "APPROVED", ["strategy meets the basic gate criteria"]
