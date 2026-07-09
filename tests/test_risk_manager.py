from risk_manager import RiskManager


def test_risk_manager_approves_safe_trade():
    manager = RiskManager(max_position_size=0.5, max_daily_loss=1000, daily_loss_limit=1000)
    assert manager.approve_trade(10000, 4000)


def test_risk_manager_rejects_large_trade():
    manager = RiskManager(max_position_size=0.25, max_daily_loss=1000, daily_loss_limit=1000)
    assert not manager.approve_trade(10000, 3000)
