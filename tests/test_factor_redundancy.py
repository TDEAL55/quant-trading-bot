from factor_redundancy import compute_factor_redundancy


def _row(factor_id, value, cid):
    return {
        "factor_id": factor_id,
        "factor_version": "v1",
        "candidate_id": cid,
        "snapshot_id": "s1",
        "factor_value": value,
    }


def test_near_duplicate_pair_detected():
    rows = []
    for i in range(1, 30):
        rows.append(_row("a", float(i), i))
        rows.append(_row("b", float(i) + 0.000001, i))
    out = compute_factor_redundancy(rows, 10)
    pair = out[0]
    assert pair["redundancy_classification"] in {"high", "near_duplicate"}


def test_self_pairs_excluded_and_order_deterministic():
    rows = []
    for i in range(1, 20):
        rows.append(_row("a", float(i), i))
        rows.append(_row("c", float(i % 3), i))
    out = compute_factor_redundancy(rows, 5)
    assert all(row["factor_a_id"] < row["factor_b_id"] for row in out)
