from strategy_lab import run_strategy_lab


def test_run_strategy_lab_dry_run(monkeypatch):
    def fake_run_strategy_laboratory(**kwargs):
        return {
            "summary": {"comparison_mode": "common_snapshots"},
            "leaderboard": [],
            "strategy_results": [],
            "pairwise": [],
            "cost_configuration": {},
            "normalization_warnings": {},
        }

    monkeypatch.setattr("strategy_lab.run_strategy_laboratory", fake_run_strategy_laboratory)
    result = run_strategy_lab(dry_run=True, persist=False)
    assert "summary" in result
    assert "performance" in result
    assert result["persistence"]["storage"] == "dry_run"
