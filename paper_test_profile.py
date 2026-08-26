from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


AGGRESSIVE_PROFILE_MARKER = Path(__file__).resolve().parent / "deployment" / "PAPER_AGGRESSIVE_TEST_MODE"
AGGRESSIVE_MAX_DAILY_ORDERS = 1_000_000
AGGRESSIVE_MAX_OPEN_POSITIONS = 1_000_000
AGGRESSIVE_MAX_POSITION_EQUITY_PERCENT = 10.0


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def aggressive_paper_test_enabled(
    environ: Mapping[str, str] | None = None,
    *,
    allow_marker: bool = True,
) -> bool:
    source = os.environ if environ is None else environ
    if str(source.get("TRADING_MODE", "SIMULATION")).strip().upper() != "PAPER":
        return False
    explicit = source.get("PAPER_AGGRESSIVE_TEST_MODE")
    if explicit is not None:
        return _enabled(explicit)
    return bool(allow_marker and AGGRESSIVE_PROFILE_MARKER.is_file())
