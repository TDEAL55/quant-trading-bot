import factor_intelligence
from factor_intelligence import FactorIntelligenceConfig, FactorIntelligenceEngine


def _synthetic_rows():
    rows = []
    for day in ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"]:
        for idx in range(1, 25):
            trend = float(idx)
            row = {
                "research_candidate_id": idx + int(day[5:7]) * 100,
                "research_run_id": f"run-{day}",
                "symbol": f"S{idx:03d}",
                "observation_date": day,
                "candidate_created_at": day + "T12:00:00+00:00",
                "market_regime": "strong_bull" if idx % 2 == 0 else "strong_bear",
                "sector": "Tech" if idx % 3 else "Energy",
                "overall_score": trend,
                "confidence": trend,
                "trend_score": trend,
                "momentum_score": trend * 0.9,
                "volume_score": trend * 0.8,
                "volatility_score": trend * 0.7,
                "liquidity_score": trend * 0.6,
                "market_regime_score": trend * 0.5,
                "risk_quality_score": trend * 0.4,
                "rank": 30 - idx,
                "forward_20d_status": "complete",
                "forward_20d_return": trend / 1000.0,
                "forward_20d_benchmark_return": 0.001,
                "forward_20d_excess_return": trend / 1000.0 - 0.001,
            }
            rows.append(row)
    return rows


def test_run_and_idempotent_rerun(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'fi_engine.db'}"
    engine = FactorIntelligenceEngine(database_url=db_url)
    monkeypatch.setattr(engine.eval_repo, "fetch_evaluation_rows_for_dashboard", lambda limit=50000: _synthetic_rows())

    cfg = FactorIntelligenceConfig(
        start_date="2024-01-01",
        end_date="2024-12-31",
        forward_horizon=20,
        factor_ids=["overall_score", "trend_score", "momentum_score"],
        factor_versions={"overall_score": "v1", "trend_score": "v1", "momentum_score": "v1"},
        minimum_sample_size=10,
        bucket_count=5,
        regime_filter=None,
        universe_filter=None,
        benchmark_mode="excess",
        force_recompute=False,
    )
    first = engine.run(cfg)
    second = engine.run(cfg)
    assert first["status"] in {"completed", "insufficient_data"}
    assert second.get("reused") is True
    engine.close()


def test_live_rejected(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'fi_live.db'}"
    engine = FactorIntelligenceEngine(database_url=db_url)
    monkeypatch.setattr(factor_intelligence, "TRADING_MODE", "LIVE")
    cfg = FactorIntelligenceConfig(
        start_date="2024-01-01",
        end_date="2024-12-31",
        forward_horizon=20,
        factor_ids=["overall_score"],
        factor_versions={"overall_score": "v1"},
        minimum_sample_size=10,
        bucket_count=5,
        regime_filter=None,
        universe_filter=None,
        benchmark_mode="excess",
        force_recompute=False,
    )
    try:
        engine.run(cfg)
        assert False
    except RuntimeError:
        assert True
    finally:
        engine.close()


def test_deterministic_verification_scenario(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'fi_verify.db'}"
    engine = FactorIntelligenceEngine(database_url=db_url)

    rows = []
    periods = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"]
    for pidx, day in enumerate(periods):
        for idx in range(1, 25):
            base = float(idx) + float(pidx)
            rows.append(
                {
                    "research_candidate_id": pidx * 1000 + idx,
                    "research_run_id": f"run-{day}",
                    "symbol": f"S{idx:03d}",
                    "observation_date": day,
                    "candidate_created_at": day + "T12:00:00+00:00",
                    "market_regime": "strong_bull" if idx % 2 == 0 else "strong_bear",
                    "sector": "Tech",
                    "overall_score": base,
                    "confidence": base + 0.0001,
                    "trend_score": base,
                    "momentum_score": 50.0,
                    "volume_score": base,
                    "volatility_score": 100.0 - base,
                    "liquidity_score": base,
                    "market_regime_score": base,
                    "risk_quality_score": base,
                    "rank": 30 - idx,
                    "forward_20d_status": "complete",
                    "forward_20d_return": base / 1000.0,
                    "forward_20d_benchmark_return": 0.001,
                    "forward_20d_excess_return": base / 1000.0 - 0.001,
                }
            )
    # Inject missing values for non-predictive/noisy factor behavior.
    rows[0]["momentum_score"] = None
    rows[7]["momentum_score"] = None

    monkeypatch.setattr(engine.eval_repo, "fetch_evaluation_rows_for_dashboard", lambda limit=50000: rows)
    cfg = FactorIntelligenceConfig(
        start_date="2024-01-01",
        end_date="2024-12-31",
        forward_horizon=20,
        factor_ids=["overall_score", "confidence", "momentum_score"],
        factor_versions={"overall_score": "v1", "confidence": "v1", "momentum_score": "v1"},
        minimum_sample_size=10,
        bucket_count=5,
        regime_filter=None,
        universe_filter=None,
        benchmark_mode="excess",
        force_recompute=False,
    )

    first = engine.run(cfg)
    second = engine.run(cfg)
    leaderboard = first["dashboard_payload"]["leaderboard"]
    redundancy = first["dashboard_payload"]["redundancy"]
    assert first["run_id"]
    assert first["run_fingerprint"]
    assert first["factor_count"] == 3
    assert first["total_observation_count"] > 0
    assert first["valid_observation_count"] > 0
    assert first["excluded_observation_count"] >= 0
    assert leaderboard
    assert leaderboard[0]["factor_id"] in {"overall_score", "confidence"}
    assert any(row.get("redundancy_classification") in {"high", "near_duplicate"} for row in redundancy)
    assert second.get("reused") is True
    engine.close()
