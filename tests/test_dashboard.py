from dashboard import read_recent_logs, render_dashboard


def test_read_recent_logs_returns_list():
    logs = read_recent_logs("bot.log", 5)
    assert isinstance(logs, list)


def test_render_dashboard_runs_without_error(capsys):
    render_dashboard()
    captured = capsys.readouterr()
    assert "=== Trading Dashboard ===" in captured.out
