from experiment_runner import run_experiment


def test_experiment_runner_returns_ranked_results():
    results = run_experiment("SPY", "2020-01-01", "2020-01-10")
    assert len(results) > 0
    assert all("params" in item for item in results)
    assert all("total_return" in item for item in results)
