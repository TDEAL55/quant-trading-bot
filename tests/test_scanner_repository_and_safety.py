from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

from scanner_repository import JsonScannerRepository, ScannerPersistencePayload


def test_json_repository_saves_scan_payload(tmp_path):
    repo = JsonScannerRepository(root_path=tmp_path)
    payload = ScannerPersistencePayload(
        run={"run_id": "scan-test", "status": "completed"},
        results=[{"symbol": "AAA"}],
        candidates=[{"symbol": "AAA", "rank": 1}],
        position_reviews=[{"symbol": "AAA", "recommendation": "HOLD"}],
    )
    saved = repo.save_scan(payload)
    assert saved["storage"] == "json"
    path = Path(saved["path"])
    assert path.exists()


def test_scanner_modules_do_not_import_broker_order_submission():
    for file_name in ["market_scanner.py", "portfolio_selector.py", "scanner_runner.py", "research_journal.py", "research_repository.py", "forward_return_labeler.py", "strategy_evaluator.py", "evaluation_repository.py", "factor_attribution.py", "walk_forward_validator.py", "walk_forward_data.py", "walk_forward_repository.py", "stability_analyzer.py", "portfolio_research.py", "portfolio_research_data.py", "portfolio_research_repository.py", "portfolio_weighting.py", "portfolio_constraints.py", "portfolio_analytics.py", "strategy_definitions.py", "strategy_costs.py", "strategy_scorecard.py", "strategy_comparison.py", "strategy_lab_data.py", "strategy_lab_repository.py", "strategy_lab.py", "paper_approval.py", "paper_order_planner.py", "paper_execution_repository.py", "paper_reconciliation.py", "paper_validation_data.py"]:
        text = (REPO_ROOT / file_name).read_text(encoding="utf-8")
        assert "submit_order(" not in text
        assert "place_order(" not in text
        assert "buy_order" not in text.lower()
        assert "sell_order" not in text.lower()


def test_scanner_runner_has_research_only_banner():
    text = (REPO_ROOT / "scanner_runner.py").read_text(encoding="utf-8")
    assert "RESEARCH SCANNER ONLY - NO ORDERS WILL BE SUBMITTED" in text


def test_live_block_remains_absent_from_scanner_config_paths():
    text = (REPO_ROOT / "config.py").read_text(encoding="utf-8")
    assert "SCANNER_ENABLE_LIVE" not in text


def test_evaluation_modules_remain_research_only():
    for file_name in ["forward_return_labeler.py", "strategy_evaluator.py", "evaluation_repository.py", "evaluation_data.py", "factor_attribution.py", "walk_forward_validator.py", "walk_forward_data.py", "walk_forward_repository.py", "stability_analyzer.py", "portfolio_research.py", "portfolio_research_data.py", "portfolio_research_repository.py", "portfolio_weighting.py", "portfolio_constraints.py", "portfolio_analytics.py", "strategy_definitions.py", "strategy_costs.py", "strategy_scorecard.py", "strategy_comparison.py", "strategy_lab_data.py", "strategy_lab_repository.py", "strategy_lab.py", "paper_approval.py", "paper_order_planner.py", "paper_execution_repository.py", "paper_reconciliation.py", "paper_validation_data.py"]:
        text = (REPO_ROOT / file_name).read_text(encoding="utf-8")
        assert "order_submission" not in text.lower()
        assert "live trading" not in text.lower()
        assert "submit_order(" not in text
        assert "place_order(" not in text


def test_paper_validation_modules_block_live_routing_patterns():
    sprint9_modules = [
        "paper_approval.py",
        "paper_order_planner.py",
        "paper_execution_repository.py",
        "paper_reconciliation.py",
        "paper_validation_data.py",
        "paper_validation.py",
    ]
    blocked_patterns = [
        "robinhood",
        "alpaca.trading.client",
        "ib_insync",
        "tradier",
        "live broker sdk",
        "enable_live",
        "auto_promote",
        "promote_to_live",
    ]
    for file_name in sprint9_modules:
        text = (REPO_ROOT / file_name).read_text(encoding="utf-8").lower()
        for pattern in blocked_patterns:
            assert pattern not in text
