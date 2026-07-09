from pathlib import Path

from dotenv import load_dotenv

from alpaca_client import create_alpaca_client


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def get_account_report(env_path=None, client_factory=create_alpaca_client):
    """Return a safe paper-account summary without exposing credentials."""
    dotenv_path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    report = {
        "account_status": "unavailable",
        "buying_power": "unavailable",
        "cash": "unavailable",
        "portfolio_value": "unavailable",
        "positions_count": 0,
    }

    try:
        client = client_factory(mode="PAPER")
        account = client._trading_client.get_account()  # read-only account fetch
        positions = client.get_current_positions()

        report["account_status"] = client.get_account_status()
        report["buying_power"] = client.get_buying_power()
        report["cash"] = _safe_float(getattr(account, "cash", "unavailable"))
        report["portfolio_value"] = _safe_float(getattr(account, "portfolio_value", "unavailable"))
        report["positions_count"] = len(positions)
    except Exception:
        # Keep output safe and non-sensitive for operational failures.
        return report

    return report


def main():
    report = get_account_report()
    print(f"account status: {report['account_status']}")
    print(f"buying power: {report['buying_power']}")
    print(f"cash: {report['cash']}")
    print(f"portfolio value: {report['portfolio_value']}")
    print(f"number of positions: {report['positions_count']}")


if __name__ == "__main__":
    main()