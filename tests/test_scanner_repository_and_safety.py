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
    for file_name in ["market_scanner.py", "portfolio_selector.py", "scanner_runner.py", "research_journal.py", "research_repository.py", "forward_return_labeler.py", "strategy_evaluator.py", "evaluation_repository.py", "factor_attribution.py", "walk_forward_validator.py", "walk_forward_data.py", "walk_forward_repository.py", "stability_analyzer.py"]:
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
    for file_name in ["forward_return_labeler.py", "strategy_evaluator.py", "evaluation_repository.py", "evaluation_data.py", "factor_attribution.py", "walk_forward_validator.py", "walk_forward_data.py", "walk_forward_repository.py", "stability_analyzer.py"]:
        text = (REPO_ROOT / file_name).read_text(encoding="utf-8")
        assert "order_submission" not in text.lower()
        assert "live trading" not in text.lower()
        assert "submit_order(" not in text
        assert "place_order(" not in text
