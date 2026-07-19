from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def short_hash(parts: list[Any], length: int = 24) -> str:
    encoded = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        numeric = float(value)
        if not math.isfinite(numeric):
            return default
        return numeric
    except (TypeError, ValueError):
        return default


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return round(ordered[mid], 6)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 6)


def stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    variance = sum((v - m) ** 2 for v in values) / len(values)
    return round(math.sqrt(variance), 6)


def pearson(xs: list[float], ys: list[float], minimum_sample_size: int) -> float | None:
    if len(xs) != len(ys) or len(xs) < minimum_sample_size:
        return None
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return None
    covariance = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denom = math.sqrt(x_var * y_var)
    if denom == 0:
        return None
    return round(covariance / denom, 6)


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted([(value, idx) for idx, value in enumerate(values)], key=lambda item: (item[0], item[1]))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][0] == indexed[i][0]:
            j += 1
        average_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][1]] = average_rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float], minimum_sample_size: int) -> float | None:
    if len(xs) != len(ys) or len(xs) < minimum_sample_size:
        return None
    x_ranks = _average_ranks(xs)
    y_ranks = _average_ranks(ys)
    return pearson(x_ranks, y_ranks, minimum_sample_size)


def percentile_ranks(values: list[float]) -> list[float]:
    if not values:
        return []
    ranks = _average_ranks(values)
    n = len(values)
    if n == 1:
        return [1.0]
    return [round((rank - 1.0) / (n - 1.0), 6) for rank in ranks]
