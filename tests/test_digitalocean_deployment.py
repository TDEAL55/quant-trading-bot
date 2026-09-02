from __future__ import annotations

from pathlib import Path

import pytest

import dashboard_app
from deployment_config import DeploymentConfigError, load_deployment_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_cloud_worker_entrypoint_removed():
    legacy_name = f"{'rai' + 'lway'}_start.py"
    assert not (REPO_ROOT / legacy_name).exists()
    assert not (REPO_ROOT / "Procfile").exists()


def test_no_cloud_vendor_environment_guard_in_continuous_runner():
    text = (REPO_ROOT / "continuous_paper_runner.py").read_text(encoding="utf-8")
    banned_tokens = [
        "ALLOW_" + "RAIL" + "WAY_TRADING_WORKER",
        "RAIL" + "WAY_ENVIRONMENT",
        "RAIL" + "WAY_PROJECT_ID",
        "RAIL" + "WAY_SERVICE_ID",
        "RAIL" + "WAY_DEPLOYMENT_ID",
    ]
    for token in banned_tokens:
        assert token not in text


def test_continuous_service_targets_continuous_runner():
    text = (REPO_ROOT / "deployment" / "quant-bot-continuous.service").read_text(encoding="utf-8")
    assert "ExecStart=" in text
    assert "continuous_paper_runner.py" in text
    assert "User=quantbot" in text


def test_hardened_micro_services_write_logs_only_to_state_directory():
    for service_name, log_name in (
        ("quant-bot-paper-micro.service", "paper-micro.log"),
        ("quant-bot-live-micro.service", "live-micro.log"),
    ):
        text = (REPO_ROOT / "deployment" / service_name).read_text(encoding="utf-8")
        assert f"Environment=BOT_LOG_FILE=/var/lib/quant-bot/{log_name}" in text
        assert "ReadWritePaths=/var/lib/quant-bot" in text
        assert "ProtectSystem=strict" in text


def test_dashboard_service_targets_streamlit_dashboard():
    text = (REPO_ROOT / "deployment" / "quant-bot-dashboard.service").read_text(encoding="utf-8")
    assert "ExecStart=" in text
    assert "streamlit run" in text
    assert "dashboard_app.py" in text
    assert "--server.address 127.0.0.1" in text
    assert "--server.port 8501" in text
    assert "User=quantbot" in text
    assert "Restart=on-failure" in text
    assert "Environment=DASHBOARD_APP_AUTH_ENABLED=false" in text
    assert "Environment=DASHBOARD_EXTERNAL_AUTH_ENABLED=true" in text
    assert "Environment=DASHBOARD_BROKER_ACCOUNT_FALLBACK_ENABLED=false" in text


def test_public_mobile_dashboard_has_separate_read_only_service():
    text = (REPO_ROOT / "deployment" / "quant-bot-mobile-dashboard.service").read_text(encoding="utf-8")
    assert "User=quantbot" in text
    assert "streamlit run" in text
    assert "dashboard_app.py" in text
    assert "--server.address 127.0.0.1" in text
    assert "--server.port 8502" in text
    assert "--server.baseUrlPath mobile" in text
    assert "Environment=DASHBOARD_FORCE_MOBILE=true" in text
    assert "continuous_paper_runner.py" not in text


def test_micro_paper_dashboard_is_a_separate_service_and_url():
    service = (REPO_ROOT / "deployment" / "quant-bot-paper-micro-dashboard.service").read_text(encoding="utf-8")
    nginx = (REPO_ROOT / "deployment" / "nginx-quant-bot-dashboard.conf").read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/quant-bot/paper-micro.env" in service
    assert "Environment=PAPER_MICRO_DASHBOARD_MODE=true" in service
    assert "--server.port 8503" in service
    assert "--server.baseUrlPath trial" in service
    assert "location /trial/" in nginx
    assert "proxy_pass http://127.0.0.1:8503" in nginx
    trial_block = nginx.split("location /trial/", 1)[1].split("location /", 1)[0]
    assert "auth_basic off" not in trial_block


def test_micro_paper_mobile_dashboard_is_a_fourth_isolated_service_and_url():
    service = (REPO_ROOT / "deployment" / "quant-bot-paper-micro-mobile-dashboard.service").read_text(encoding="utf-8")
    nginx = (REPO_ROOT / "deployment" / "nginx-quant-bot-dashboard.conf").read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/quant-bot/paper-micro.env" in service
    assert "Environment=PAPER_MICRO_DASHBOARD_MODE=true" in service
    assert "Environment=DASHBOARD_FORCE_MOBILE=true" in service
    assert "Environment=DASHBOARD_MOBILE_DESKTOP_URL=/trial/" in service
    assert "--server.port 8504" in service
    assert "--server.baseUrlPath trial-mobile" in service
    assert "location /trial-mobile/" in nginx
    assert "proxy_pass http://127.0.0.1:8504" in nginx
    trial_mobile_block = nginx.split("location /trial-mobile/", 1)[1].split("location /", 1)[0]
    assert "auth_basic off" in trial_mobile_block


def test_original_dashboards_cannot_fall_back_to_micro_broker_account():
    for service_name in ("quant-bot-dashboard.service", "quant-bot-mobile-dashboard.service"):
        service = (REPO_ROOT / "deployment" / service_name).read_text(encoding="utf-8")
        assert "Environment=DASHBOARD_BROKER_ACCOUNT_FALLBACK_ENABLED=false" in service
        assert "PAPER_MICRO_DASHBOARD_MODE=true" not in service


def test_dashboard_path_is_read_only_for_trading_actions():
    module_text = (REPO_ROOT / "dashboard_app.py").read_text(encoding="utf-8")
    assert dashboard_app.has_write_capability(module_text) is False

    service_text = (REPO_ROOT / "deployment" / "quant-bot-dashboard.service").read_text(encoding="utf-8")
    assert "continuous_paper_runner.py" not in service_text
    assert "unattended_daily_runner.py" not in service_text


def test_nginx_dashboard_proxy_keeps_streamlit_localhost_only():
    text = (REPO_ROOT / "deployment" / "nginx-quant-bot-dashboard.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:8501" in text
    assert "proxy_set_header Upgrade $http_upgrade" in text
    assert "auth_basic" in text
    assert "auth_basic_user_file" in text
    assert "location /mobile/" in text
    assert "auth_basic off" in text
    assert "proxy_pass http://127.0.0.1:8502" in text


def test_nginx_dashboard_proxy_does_not_expose_runner():
    text = (REPO_ROOT / "deployment" / "nginx-quant-bot-dashboard.conf").read_text(encoding="utf-8")
    assert "continuous_paper_runner.py" not in text
    assert "127.0.0.1:8501" in text
    assert "127.0.0.1:8502" in text


def test_paper_and_dry_run_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.delenv("CONTINUOUS_RUNNER_DRY_RUN", raising=False)

    cfg = load_deployment_config()
    assert cfg.trading_mode == "PAPER"
    assert cfg.continuous_runner_dry_run is True
    assert cfg.sector_enrichment_enabled is True
    assert cfg.sector_enrichment_max_symbols == 30
    assert cfg.sector_enrichment_total_timeout_seconds == 25


def test_live_mode_remains_hard_blocked(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(DeploymentConfigError):
        load_deployment_config()


def test_env_examples_include_safe_defaults_and_no_real_secrets():
    root_env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    deploy_env = (REPO_ROOT / "deployment" / "deploy.example.env").read_text(encoding="utf-8")

    for text in (root_env, deploy_env):
        assert "TRADING_MODE=PAPER" in text
        assert "CONTINUOUS_RUNNER_DRY_RUN=true" in text
        assert "PAPER_EXECUTION_ENABLED=false" in text
        assert "CONTROLLED_PAPER_VALIDATION=false" in text

    assert "REPLACE_ME" in deploy_env
    assert "your_" in root_env


def test_no_vendor_specific_references_in_tracked_docs_and_runtime_files():
    token = "rai" + "lway"
    allowed = {
        "OVERNIGHT_COST_SENSITIVITY_2023.md",
    }

    include_suffixes = {".py", ".md", ".env", ".service", ".sh"}
    skip_dirs = {".git", ".venv", ".pytest_cache", "__pycache__", ".vscode"}

    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.name in allowed:
            continue
        if path.suffix not in include_suffixes and path.name not in {"README"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if token in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []
