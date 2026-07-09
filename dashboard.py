from datetime import datetime
from pathlib import Path

from config import TRADING_MODE
from logger_setup import logger
from simulation_runner import run_simulation


def read_recent_logs(log_file="bot.log", lines=10):
    """Read the most recent log entries from the log file."""
    path = Path(log_file)
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").strip().splitlines()[-lines:]


def render_dashboard():
    """Render a simple terminal dashboard with recent simulation information."""
    try:
        latest_result = run_simulation()
    except Exception as exc:
        latest_result = {"error": str(exc)}

    recent_logs = read_recent_logs()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=== Trading Dashboard ===")
    print(f"Timestamp: {now}")
    print(f"Trading Mode: {TRADING_MODE}")
    print(f"Last Simulation Run: {now}")
    print(f"Latest Signal: {latest_result.get('signal', 'N/A')}")
    print(f"Latest Decision: {latest_result.get('decision', 'N/A')}")
    print(f"Portfolio Value: {latest_result.get('buying_power', 'N/A')}")
    print(f"Latest Close: {latest_result.get('latest_close', 'N/A')}")
    print("Recent Log Entries:")
    for entry in recent_logs:
        print(f"- {entry}")


if __name__ == "__main__":
    render_dashboard()
