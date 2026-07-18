from portfolio_weighting import build_raw_weights


def _rows():
    return [
        {"symbol": "AAA", "overall_score": 80.0, "confidence": 70.0, "rank": 1, "volatility_measure": 0.2, "volatility_score": 70.0},
        {"symbol": "BBB", "overall_score": 60.0, "confidence": 50.0, "rank": 2, "volatility_measure": 0.4, "volatility_score": 50.0},
        {"symbol": "CCC", "overall_score": 40.0, "confidence": 30.0, "rank": 4, "volatility_measure": 0.8, "volatility_score": 30.0},
    ]


def test_equal_weighting_is_deterministic():
    result = build_raw_weights(_rows(), method="equal_weight")
    assert result["status"] == "ok"
    assert result["weights"] == {"AAA": 1 / 3, "BBB": 1 / 3, "CCC": 1 / 3}


def test_score_and_confidence_proportional_weighting():
    score = build_raw_weights(_rows(), method="score_proportional")
    confidence = build_raw_weights(_rows(), method="confidence_proportional")

    assert score["weights"]["AAA"] > score["weights"]["BBB"] > score["weights"]["CCC"]
    assert confidence["weights"]["AAA"] > confidence["weights"]["BBB"] > confidence["weights"]["CCC"]
    assert round(sum(score["weights"].values()), 10) == 1.0
    assert round(sum(confidence["weights"].values()), 10) == 1.0


def test_rank_based_inverse_rank_weighting():
    result = build_raw_weights(_rows(), method="rank_based")
    assert result["status"] == "ok"
    assert result["weights"]["AAA"] > result["weights"]["BBB"] > result["weights"]["CCC"]


def test_inverse_volatility_and_risk_parity_like_weighting():
    inv = build_raw_weights(_rows(), method="inverse_volatility")
    rp = build_raw_weights(_rows(), method="risk_parity_like")

    assert inv["status"] == "ok"
    assert rp["status"] == "ok"
    assert rp["method_detail"] == "simplified inverse-volatility approximation"
    assert inv["weights"]["AAA"] > inv["weights"]["BBB"] > inv["weights"]["CCC"]


def test_invalid_volatility_is_unavailable():
    rows = [{"symbol": "AAA", "overall_score": 60.0, "confidence": 50.0, "rank": 1, "volatility_measure": 0.0}]
    result = build_raw_weights(rows, method="inverse_volatility")
    assert result["status"] == "unavailable"


def test_all_zero_scores_and_missing_confidence_are_insufficient_data():
    rows = [
        {"symbol": "AAA", "overall_score": 0.0, "confidence": None, "rank": 1},
        {"symbol": "BBB", "overall_score": 0.0, "confidence": None, "rank": 2},
    ]
    score = build_raw_weights(rows, method="score_proportional")
    confidence = build_raw_weights(rows, method="confidence_proportional")
    assert score["status"] == "insufficient_data"
    assert confidence["status"] == "insufficient_data"


def test_volatility_targeted_scales_without_default_leverage():
    result = build_raw_weights(_rows(), method="volatility_targeted", target_volatility=0.50, max_gross_exposure=1.0, allow_leverage=False)
    assert result["status"] == "ok"
    assert sum(result["weights"].values()) <= 1.0


def test_one_holding_many_holdings_paths():
    one = build_raw_weights([{"symbol": "ONLY", "overall_score": 90.0, "confidence": 90.0, "rank": 1}], method="equal_weight")
    many = build_raw_weights(_rows() + [{"symbol": "DDD", "overall_score": 20.0, "confidence": 20.0, "rank": 5}], method="equal_weight")
    assert one["weights"] == {"ONLY": 1.0}
    assert len(many["weights"]) == 4
