from __future__ import annotations

from typing import Any

from self_improving_repository import SelfImprovingRepository


def fetch_self_improving_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repo = SelfImprovingRepository(database_url=database_url)
    try:
        return repo.fetch_dashboard_payload()
    finally:
        repo.close()
