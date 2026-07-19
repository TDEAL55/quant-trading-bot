from backtest import run_backtest
import math

from performance_metrics import (
    build_benchmark_metrics,
    build_equity_curve_metrics,
    build_exposure_metrics,
    build_risk_ratios,
    build_trade_statistics,
    calculate_metrics,
)


def test_calculate_metrics_returns_expected_keys():
    result = run_backtest("SPY", "2020-01-01", "2020-01-10")
    metrics = calculate_metrics(result)
    assert set(metrics.keys()) == {
        "annualized_return",
        "volatility",
        "sharpe_ratio",
        "win_rate",
        "average_winning_trade",
        "average_losing_trade",
        "profit_factor",
    }


def test_equity_curve_metrics_deterministic():
    rows = [
        {"portfolio_value": 100.0, "cash": 100.0, "buying_power": 100.0},
        {"portfolio_value": 110.0, "cash": 95.0, "buying_power": 95.0},
        {"portfolio_value": 99.0, "cash": 90.0, "buying_power": 90.0},
        {"portfolio_value": 120.0, "cash": 120.0, "buying_power": 120.0},
    ]

    metrics = build_equity_curve_metrics(rows)

    assert math.isclose(metrics["total_return"], 0.2, rel_tol=1e-9)
    assert math.isclose(metrics["maximum_drawdown"], -0.1, rel_tol=1e-9)
    assert metrics["volatility"] > 0


def test_trade_statistics_deterministic():
    stats = build_trade_statistics([10.0, -5.0, 7.0, -2.0], [1.0, 3.0])
    assert math.isclose(stats["win_rate"], 0.5, rel_tol=1e-9)
    assert math.isclose(stats["loss_rate"], 0.5, rel_tol=1e-9)
    assert math.isclose(stats["profit_factor"], 17.0 / 7.0, rel_tol=1e-9)
    assert math.isclose(stats["average_hold_time"], 2.0, rel_tol=1e-9)


def test_exposure_metrics_deterministic():
    exposure = build_exposure_metrics(
        [
            {"symbol": "A", "market_value": 30.0, "sector": "Tech"},
            {"symbol": "B", "market_value": 20.0, "sector": "Energy"},
        ],
        portfolio_value=100.0,
    )
    assert math.isclose(exposure["exposure_pct"], 0.5, rel_tol=1e-9)
    assert math.isclose(exposure["position_concentration"], 0.3, rel_tol=1e-9)
    assert math.isclose(exposure["sector_allocation"]["Tech"], 0.3, rel_tol=1e-9)


def test_benchmark_and_risk_ratios_deterministic():
    portfolio = [0.01, 0.02, -0.01, 0.015]
    benchmark = [0.008, 0.015, -0.012, 0.010]

    benchmark_metrics = build_benchmark_metrics(portfolio, benchmark)
    risk_metrics = build_risk_ratios(portfolio, max_drawdown=-0.10)

    assert set(["alpha", "beta", "tracking_error", "excess_return", "information_ratio"]).issubset(benchmark_metrics.keys())
    assert set(["sharpe_ratio", "sortino_ratio", "calmar_ratio"]).issubset(risk_metrics.keys())
    assert risk_metrics["calmar_ratio"] != 0
