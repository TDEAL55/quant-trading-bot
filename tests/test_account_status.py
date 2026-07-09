import account_status


class MockAccount:
    def __init__(self, status="ACTIVE", buying_power="15000.5", cash="5000.25", portfolio_value="22000.0"):
        self.status = status
        self.buying_power = buying_power
        self.cash = cash
        self.portfolio_value = portfolio_value


class MockTradingClient:
    def __init__(self):
        self._account = MockAccount()

    def get_account(self):
        return self._account

    def submit_order(self, *args, **kwargs):
        raise AssertionError("submit_order should never be called")


class MockAlpacaClient:
    def __init__(self):
        self._trading_client = MockTradingClient()

    def get_account_status(self):
        return "ACTIVE"

    def get_buying_power(self):
        return 15000.5

    def get_current_positions(self):
        return [
            {"symbol": "SPY", "qty": "2"},
            {"symbol": "AAPL", "qty": "1"},
        ]


def test_get_account_report_reads_paper_data_with_mock_client(monkeypatch):
    captured = {"mode": None}

    def fake_factory(mode=None):
        captured["mode"] = mode
        return MockAlpacaClient()

    report = account_status.get_account_report(client_factory=fake_factory)

    assert captured["mode"] == "PAPER"
    assert report["account_status"] == "ACTIVE"
    assert report["buying_power"] == 15000.5
    assert report["cash"] == 5000.25
    assert report["portfolio_value"] == 22000.0
    assert report["positions_count"] == 2


def test_get_account_report_handles_errors_safely():
    def failing_factory(mode=None):
        raise ValueError("Missing required Alpaca credentials: ALPACA_API_KEY")

    report = account_status.get_account_report(client_factory=failing_factory)

    assert report["account_status"] == "unavailable"
    assert report["buying_power"] == "unavailable"
    assert report["cash"] == "unavailable"
    assert report["portfolio_value"] == "unavailable"
    assert report["positions_count"] == 0


def test_main_prints_only_expected_fields(monkeypatch, capsys):
    monkeypatch.setattr(
        account_status,
        "get_account_report",
        lambda: {
            "account_status": "ACTIVE",
            "buying_power": 15000.5,
            "cash": 5000.25,
            "portfolio_value": 22000.0,
            "positions_count": 2,
        },
    )

    account_status.main()
    output_lines = capsys.readouterr().out.strip().splitlines()

    assert output_lines == [
        "account status: ACTIVE",
        "buying power: 15000.5",
        "cash: 5000.25",
        "portfolio value: 22000.0",
        "number of positions: 2",
    ]