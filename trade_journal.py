import csv
from datetime import datetime, UTC
from pathlib import Path

from config import TRADING_MODE


JOURNAL_PATH = Path(__file__).resolve().parent / "trade_journal.csv"


def _ensure_journal_path(path=None):
    journal_path = Path(path or JOURNAL_PATH)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    return journal_path


def save_trade_decision(
    symbol,
    signal,
    decision,
    price,
    reason,
    risk_result,
    portfolio_value,
    timestamp=None,
    path=None,
):
    """Persist a simulation-only trade decision to a local CSV journal."""
    if TRADING_MODE.upper() != "SIMULATION":
        raise ValueError("Trade journal only supports SIMULATION mode")

    journal_path = _ensure_journal_path(path)
    record = {
        "timestamp": timestamp or datetime.now(UTC).isoformat(timespec="seconds"),
        "symbol": symbol,
        "signal": signal,
        "decision": decision,
        "price": price,
        "reason": reason,
        "risk_result": risk_result,
        "portfolio_value": portfolio_value,
    }

    file_exists = journal_path.exists()
    existing_records = read_journal(journal_path) if file_exists else []
    if any(
        existing.get("timestamp") == record["timestamp"]
        and existing.get("symbol") == record["symbol"]
        and existing.get("decision") == record["decision"]
        and existing.get("price") == str(record["price"])
        for existing in existing_records
    ):
        return record

    with journal_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(record.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)

    return record


def read_journal(path=None):
    """Read the local trade journal CSV file and return a list of records."""
    journal_path = _ensure_journal_path(path)
    if not journal_path.exists():
        return []

    with journal_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
