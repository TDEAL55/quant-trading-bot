from walk_forward import run_walk_forward


def test_walk_forward_returns_period_results():
    results = run_walk_forward("SPY", "2020-01-01", "2020-12-31", window_size=60, step=30)
    assert len(results) > 0
    assert all("train_period" in item for item in results)
    assert all("test_period" in item for item in results)
