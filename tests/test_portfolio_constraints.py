from portfolio_constraints import apply_portfolio_constraints


def _row_by_symbol():
    return {
        "AAA": {"sector": "Tech"},
        "BBB": {"sector": "Tech"},
        "CCC": {"sector": "Energy"},
        "DDD": {"sector": "Health"},
    }


def test_max_position_cap_and_redistribution_and_full_allocation():
    raw = {"AAA": 0.9, "BBB": 0.1}
    result = apply_portfolio_constraints(raw, _row_by_symbol(), max_position_weight=0.6, sector_cap=1.0, allow_cash=True)
    assert result["status"] == "ok"
    assert result["weights"]["AAA"] <= 0.6 + 1e-9
    assert round(result["invested_weight"] + result["cash_weight"], 6) == 1.0


def test_sector_cap_with_cash_residual_and_impossible_allocation_warning():
    raw = {"AAA": 0.7, "BBB": 0.3}
    result = apply_portfolio_constraints(raw, _row_by_symbol(), max_position_weight=0.8, sector_cap=0.4, allow_cash=True)
    assert result["status"] in {"ok", "insufficient_holdings"}
    assert result["cash_weight"] > 0
    assert any("sector-cap" in warning for warning in result["warnings"])


def test_zero_weight_removal_normalization_tolerance_and_negative_rejection():
    raw = {"AAA": 0.7, "BBB": -0.1, "CCC": 0.0, "DDD": 0.3}
    result = apply_portfolio_constraints(raw, _row_by_symbol(), max_position_weight=0.7, sector_cap=1.0, normalization_tolerance=1e-8)
    assert "BBB" not in result["weights"]
    assert "CCC" not in result["weights"]
    assert round(sum(result["weights"].values()) + result["cash_weight"], 6) == 1.0


def test_max_iterations_bound_path():
    raw = {"AAA": 0.5, "BBB": 0.5}
    result = apply_portfolio_constraints(raw, _row_by_symbol(), max_position_weight=0.5, sector_cap=0.1, max_iterations=1)
    assert result["status"] in {"ok", "insufficient_holdings"}
    assert any("sector-cap" in warning or "iterations" in warning for warning in result["warnings"])


def test_gross_exposure_limit_and_min_max_holdings():
    raw = {"AAA": 0.4, "BBB": 0.3, "CCC": 0.2, "DDD": 0.1}
    result = apply_portfolio_constraints(raw, _row_by_symbol(), max_position_weight=0.4, sector_cap=1.0, min_holdings=3, max_holdings=3, max_gross_exposure=0.8)
    assert len(result["weights"]) == 3
    assert result["invested_weight"] <= 0.8 + 1e-9
