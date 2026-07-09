from scheduler import run_scheduler


def test_scheduler_runs_in_simulation_mode():
    results = run_scheduler(interval_seconds=0, max_runs=1)
    assert isinstance(results, list)
