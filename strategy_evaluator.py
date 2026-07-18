from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evaluation_data import build_evaluation_analytics
from evaluation_repository import MonitoringEvaluationRepository


@dataclass
class StrategyEvaluator:
    database_url: str | None = None

    def _repository(self) -> MonitoringEvaluationRepository:
        return MonitoringEvaluationRepository(database_url=self.database_url)

    def evaluate(
        self,
        selected_run_id: str | None = None,
        selected_symbol: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        repository = self._repository()
        try:
            repository.db.ensure_schema()
            rows = repository.fetch_evaluation_rows_for_dashboard(limit=limit)
            if selected_run_id:
                rows = [row for row in rows if str(row.get("research_run_id") or "") == str(selected_run_id)]
            if selected_symbol:
                rows = [row for row in rows if str(row.get("symbol") or "").upper() == str(selected_symbol).upper()]
            return {
                "rows": rows,
                "evaluation_analytics": build_evaluation_analytics(rows),
                "latest_labeling_run": repository.fetch_latest_labeling_timestamp() or {},
            }
        finally:
            repository.close()


def evaluate_strategy_performance(
    database_url: str | None = None,
    selected_run_id: str | None = None,
    selected_symbol: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    return StrategyEvaluator(database_url=database_url).evaluate(
        selected_run_id=selected_run_id,
        selected_symbol=selected_symbol,
        limit=limit,
    )