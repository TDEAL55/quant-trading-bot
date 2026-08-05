from sector_manager import SectorPolicy, apply_sector_constraint, normalize_sector


def test_normalize_sector_names():
    assert normalize_sector("tech") == "Information Technology"
    assert normalize_sector("health care") == "Healthcare"
    assert normalize_sector(None) == "Unknown"


def test_sector_cap_enforcement_with_existing_exposure():
    policy = SectorPolicy(max_sector_percent=30.0, unknown_sector_max_percent=10.0)
    result = apply_sector_constraint(
        "Information Technology",
        target_notional=3000.0,
        account_equity=10000.0,
        current_sector_notional={"Information Technology": 2500.0},
        policy=policy,
    )
    assert result["adjusted_notional"] == 500.0
    assert result["reduced"] is True
    assert result["rejected"] is False


def test_unknown_sector_limit():
    policy = SectorPolicy(max_sector_percent=30.0, unknown_sector_max_percent=10.0)
    result = apply_sector_constraint(
        "Unknown",
        target_notional=2000.0,
        account_equity=10000.0,
        current_sector_notional={"Unknown": 900.0},
        policy=policy,
    )
    assert result["adjusted_notional"] == 100.0
    assert result["reduced"] is True


def test_reduction_before_rejection():
    policy = SectorPolicy(max_sector_percent=30.0, unknown_sector_max_percent=10.0)
    reduced = apply_sector_constraint(
        "Energy",
        target_notional=1000.0,
        account_equity=10000.0,
        current_sector_notional={"Energy": 2500.0},
        policy=policy,
    )
    assert reduced["adjusted_notional"] == 500.0

    rejected = apply_sector_constraint(
        "Energy",
        target_notional=1000.0,
        account_equity=10000.0,
        current_sector_notional={"Energy": 3000.0},
        policy=policy,
    )
    assert rejected["rejected"] is True
