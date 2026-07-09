from strategy_analysis import compare_strategies


def rank_strategies(results):
    """Rank strategies by return, drawdown, and risk-adjusted performance."""
    ranked = []
    for name, metrics in results.items():
        return_value = metrics.get("return", metrics.get("total_return", 0))
        drawdown = metrics.get("drawdown", metrics.get("max_drawdown", 0))
        trades = metrics.get("trades", metrics.get("number_of_trades", 0))
        risk_adjusted = return_value / (drawdown + 1e-9)
        ranked.append(
            {
                "name": name,
                "return": return_value,
                "drawdown": drawdown,
                "trades": trades,
                "risk_adjusted": risk_adjusted,
            }
        )

    ranked.sort(key=lambda item: (-item["return"], item["drawdown"], -item["risk_adjusted"]))
    return ranked


def print_leaderboard(results):
    """Print a simple strategy leaderboard to the terminal."""
    ranked = rank_strategies(results)
    print("Strategy Leaderboard")
    print("====================")
    print("Past performance does not guarantee future results.")
    for index, item in enumerate(ranked, start=1):
        print(
            f"{index}. {item['name']} | return={item['return']:.2%} | drawdown={item['drawdown']:.2%} | risk_adjusted={item['risk_adjusted']:.2f}"
        )


if __name__ == "__main__":
    comparison_results = compare_strategies()
    print_leaderboard(comparison_results)
