from __future__ import annotations

import argparse
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from config import BENCHMARK_SYMBOL, FORWARD_RETURN_HORIZONS, FORWARD_RETURN_MAX_LABEL_BATCH_SIZE, FORWARD_RETURN_PRICE_LOOKBACK_DAYS, FORWARD_RETURN_RETRY_LIMIT
from error_handler import MarketDataError
from evaluation_repository import MonitoringEvaluationRepository, save_evaluation_results
from logger_setup import logger
from market_data import download_price_data


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_date() -> date:
    return datetime.now(timezone.utc).date()


def _log(event: str, **fields: Any) -> None:
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date()
    except Exception:
        return None


def _normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise MarketDataError("No price data returned")
    data = frame.copy()
    data.index = pd.to_datetime(data.index, utc=True, errors="coerce")
    data = data.loc[~data.index.isna()].copy()
    data.index = pd.DatetimeIndex(data.index).tz_convert(None).normalize()
    data = data.sort_index()
    data = data[~data.index.duplicated(keep="last")]
    return data


def _price_column(frame: pd.DataFrame) -> str:
    if "adj_close" in frame.columns:
        return "adj_close"
    if "close" in frame.columns:
        return "close"
    raise MarketDataError("Price data did not contain an adjusted close or close column")


def _match_index(frame: pd.DataFrame, observation_date: date) -> int | None:
    if frame is None or frame.empty:
        return None
    timestamp = pd.Timestamp(observation_date)
    position = frame.index.searchsorted(timestamp, side="right") - 1
    if position < 0:
        return None
    return int(position)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _return_ratio(future_price: float | None, observation_price: float | None) -> float | None:
    if future_price is None or observation_price in {None, 0.0}:
        return None
    try:
        return round(float(future_price) / float(observation_price) - 1.0, 6)
    except Exception:
        return None


def _group_selected_candidates(rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    symbol_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    benchmark_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol_groups[str(row.get("symbol") or "").upper()].append(row)
        benchmark_groups[str(row.get("research_benchmark_symbol") or BENCHMARK_SYMBOL).upper()].append(row)
    return symbol_groups, benchmark_groups


def _download_with_retry(
    data_loader: Callable[[str, str, str], pd.DataFrame],
    symbol: str,
    start_date: str,
    end_date: str,
    retry_limit: int,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retry_limit + 1):
        try:
            return _normalize_price_frame(data_loader(symbol, start_date, end_date))
        except MarketDataError as exc:
            last_error = exc
        except Exception as exc:
            last_error = MarketDataError(f"Unable to load prices for {symbol}: {exc}")
        if attempt < retry_limit:
            time.sleep(min(0.2 * (attempt + 1), 1.0))
    assert last_error is not None
    raise last_error


def _series_slice(frame: pd.DataFrame, position: int) -> tuple[date, float] | None:
    if position < 0 or position >= len(frame):
        return None
    column = _price_column(frame)
    row = frame.iloc[position]
    price = _safe_float(row.get(column), None)
    if price is None:
        return None
    return frame.index[position].date(), price


def _horizon_result(
    symbol_frame: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    observation_position: int,
    benchmark_observation_position: int,
    horizon: int,
    today: date,
) -> dict[str, Any]:
    symbol_target_position = observation_position + horizon
    benchmark_target_position = benchmark_observation_position + horizon
    symbol_complete = symbol_target_position < len(symbol_frame)
    benchmark_complete = benchmark_target_position < len(benchmark_frame)

    status = "pending"
    if symbol_complete and benchmark_complete:
        status = "complete"
    elif not symbol_complete or not benchmark_complete:
        latest_date = min(symbol_frame.index.max().date(), benchmark_frame.index.max().date())
        status = "pending" if latest_date >= today else "unavailable"

    if status != "complete":
        return {
            "status": status,
            "target_date": None,
            "actual_date": None,
            "future_price": None,
            "benchmark_future_price": None,
            "return": None,
            "benchmark_return": None,
            "excess_return": None,
        }

    symbol_actual = _series_slice(symbol_frame, symbol_target_position)
    benchmark_actual = _series_slice(benchmark_frame, benchmark_target_position)
    if symbol_actual is None or benchmark_actual is None:
        return {
            "status": "unavailable",
            "target_date": None,
            "actual_date": None,
            "future_price": None,
            "benchmark_future_price": None,
            "return": None,
            "benchmark_return": None,
            "excess_return": None,
        }

    symbol_date, symbol_future_price = symbol_actual
    benchmark_date, benchmark_future_price = benchmark_actual
    return {
        "status": "complete",
        "target_date": symbol_date.isoformat(),
        "actual_date": symbol_date.isoformat(),
        "future_price": symbol_future_price,
        "benchmark_future_price": benchmark_future_price,
        "return": None,
        "benchmark_return": None,
        "excess_return": None,
    }


def _build_candidate_label_record(
    candidate: dict[str, Any],
    symbol_frame: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    today: date,
    observation_date: date,
) -> dict[str, Any]:
    symbol_price_column = _price_column(symbol_frame)
    benchmark_price_column = _price_column(benchmark_frame)
    observation_position = _match_index(symbol_frame, observation_date)
    benchmark_observation_position = _match_index(benchmark_frame, observation_date)
    if observation_position is None or benchmark_observation_position is None:
        record = {
            "research_candidate_id": candidate["research_candidate_id"],
            "research_run_id": candidate["research_run_id"],
            "symbol": candidate["symbol"],
            "observation_date": observation_date.isoformat(),
            "observation_price": _safe_float(candidate.get("candidate_latest_price"), None),
            "benchmark_symbol": str(candidate.get("research_benchmark_symbol") or BENCHMARK_SYMBOL).upper(),
            "benchmark_observation_price": None,
            "label_status": "unavailable",
            "data_source": "market_data",
            "last_attempted_at": _utc_iso(),
            "completed_at": _utc_iso(),
            "error_message": "observation date could not be matched to available trading data",
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
        }
        for horizon in FORWARD_RETURN_HORIZONS:
            prefix = f"forward_{horizon}d"
            record[f"{prefix}_target_date"] = None
            record[f"{prefix}_actual_date"] = None
            record[f"{prefix}_future_price"] = None
            record[f"{prefix}_benchmark_future_price"] = None
            record[f"{prefix}_return"] = None
            record[f"{prefix}_benchmark_return"] = None
            record[f"{prefix}_excess_return"] = None
            record[f"{prefix}_status"] = "unavailable"
        return record

    observation_row = symbol_frame.iloc[observation_position]
    benchmark_observation_row = benchmark_frame.iloc[benchmark_observation_position]
    observation_price = _safe_float(candidate.get("candidate_latest_price"), None)
    if observation_price is None or observation_price == 0.0:
        observation_price = _safe_float(observation_row.get(symbol_price_column), None)
    benchmark_observation_price = _safe_float(benchmark_observation_row.get(benchmark_price_column), None)

    horizon_payload: dict[int, dict[str, Any]] = {}
    for horizon in FORWARD_RETURN_HORIZONS:
        horizon_payload[horizon] = _horizon_result(symbol_frame, benchmark_frame, observation_position, benchmark_observation_position, horizon, today)

    horizon_statuses = [payload["status"] for payload in horizon_payload.values()]
    if all(status == "pending" for status in horizon_statuses):
        overall_status = "pending"
    elif all(status == "complete" for status in horizon_statuses):
        overall_status = "complete"
    elif all(status == "unavailable" for status in horizon_statuses):
        overall_status = "unavailable"
    elif any(status == "data_error" for status in horizon_statuses):
        overall_status = "data_error"
    else:
        overall_status = "partial"

    completed_at = _utc_iso() if overall_status != "pending" else None
    error_message = None
    if overall_status == "unavailable":
        error_message = "historical forward label could not be completed with available trading sessions"

    record: dict[str, Any] = {
        "research_candidate_id": candidate["research_candidate_id"],
        "research_run_id": candidate["research_run_id"],
        "symbol": candidate["symbol"],
        "observation_date": observation_date.isoformat(),
        "observation_price": observation_price,
        "benchmark_symbol": str(candidate.get("research_benchmark_symbol") or BENCHMARK_SYMBOL).upper(),
        "benchmark_observation_price": benchmark_observation_price,
        "label_status": overall_status,
        "data_source": "market_data",
        "last_attempted_at": _utc_iso(),
        "completed_at": completed_at,
        "error_message": error_message,
        "created_at": candidate.get("evaluation_completed_at") or candidate.get("last_attempted_at") or _utc_iso(),
        "updated_at": _utc_iso(),
    }

    for horizon in FORWARD_RETURN_HORIZONS:
        payload = horizon_payload[horizon]
        prefix = f"forward_{horizon}d"
        record[f"{prefix}_target_date"] = payload["target_date"]
        record[f"{prefix}_actual_date"] = payload["actual_date"]
        record[f"{prefix}_future_price"] = payload["future_price"]
        record[f"{prefix}_benchmark_future_price"] = payload["benchmark_future_price"]
        if payload["status"] == "complete":
            symbol_return = _return_ratio(payload["future_price"], observation_price)
            benchmark_return = _return_ratio(payload["benchmark_future_price"], benchmark_observation_price)
            excess_return = None if symbol_return is None or benchmark_return is None else round(symbol_return - benchmark_return, 6)
            record[f"{prefix}_return"] = symbol_return
            record[f"{prefix}_benchmark_return"] = benchmark_return
            record[f"{prefix}_excess_return"] = excess_return
        else:
            record[f"{prefix}_return"] = None
            record[f"{prefix}_benchmark_return"] = None
            record[f"{prefix}_excess_return"] = None
        record[f"{prefix}_status"] = payload["status"]

    return record


@dataclass
class ForwardReturnLabeler:
    database_url: str | None = None
    data_loader: Callable[[str, str, str], pd.DataFrame] = download_price_data

    def _repository(self) -> MonitoringEvaluationRepository:
        return MonitoringEvaluationRepository(database_url=self.database_url)

    def label_candidates(
        self,
        research_run_id: str | None = None,
        symbol: str | None = None,
        limit: int = FORWARD_RETURN_MAX_LABEL_BATCH_SIZE,
        dry_run: bool = False,
        retry_data_errors: int = FORWARD_RETURN_RETRY_LIMIT,
    ) -> dict[str, Any]:
        repository = self._repository()
        started = time.perf_counter()
        market_fetch_started = time.perf_counter()
        calculation_started = 0.0
        db_write_started = 0.0
        try:
            repository.db.ensure_schema()
            selected_candidates = repository.fetch_labeling_candidates(research_run_id=research_run_id, symbol=symbol, limit=int(limit))
            _log("LABELING_BATCH_STARTED", research_run_id=research_run_id or "all", symbol=symbol or "all", candidate_count=len(selected_candidates), dry_run=dry_run)
            if not selected_candidates:
                return {
                    "status": "empty",
                    "candidates_processed": 0,
                    "label_status_counts": {"pending": 0, "partial": 0, "complete": 0, "unavailable": 0, "data_error": 0},
                    "storage": "dry_run" if dry_run else "database",
                    "market_data_retrieval_seconds": 0.0,
                    "labeling_calculation_seconds": 0.0,
                    "database_write_seconds": 0.0,
                    "total_duration_seconds": round(time.perf_counter() - started, 4),
                    "average_time_per_candidate": 0.0,
                    "records": [],
                }

            symbol_groups, benchmark_groups = _group_selected_candidates(selected_candidates)
            current_day = _current_date()
            end_date = (current_day + timedelta(days=1)).isoformat()
            symbol_frames: dict[str, pd.DataFrame] = {}
            benchmark_frames: dict[str, pd.DataFrame] = {}
            market_errors: dict[str, str] = {}

            for symbol_name, rows in symbol_groups.items():
                earliest = min(filter(None, (_parse_date(row.get("candidate_created_at") or row.get("research_completed_at") or row.get("research_started_at")) for row in rows)), default=current_day)
                start_date = (earliest - timedelta(days=FORWARD_RETURN_PRICE_LOOKBACK_DAYS)).isoformat()
                try:
                    symbol_frames[symbol_name] = _download_with_retry(self.data_loader, symbol_name, start_date, end_date, retry_data_errors)
                except Exception as exc:
                    market_errors[symbol_name] = f"{type(exc).__name__}: {exc}"

            for benchmark_name, rows in benchmark_groups.items():
                earliest = min(filter(None, (_parse_date(row.get("candidate_created_at") or row.get("research_completed_at") or row.get("research_started_at")) for row in rows)), default=current_day)
                start_date = (earliest - timedelta(days=FORWARD_RETURN_PRICE_LOOKBACK_DAYS)).isoformat()
                try:
                    benchmark_frames[benchmark_name] = _download_with_retry(self.data_loader, benchmark_name, start_date, end_date, retry_data_errors)
                except Exception as exc:
                    market_errors[benchmark_name] = f"{type(exc).__name__}: {exc}"

            market_fetch_seconds = round(time.perf_counter() - market_fetch_started, 4)
            calculation_started = time.perf_counter()

            records: list[dict[str, Any]] = []
            status_counts = {"pending": 0, "partial": 0, "complete": 0, "unavailable": 0, "data_error": 0}
            for row in selected_candidates:
                symbol_name = str(row.get("symbol") or "").upper()
                benchmark_name = str(row.get("research_benchmark_symbol") or BENCHMARK_SYMBOL).upper()
                observation_date = _parse_date(row.get("candidate_created_at") or row.get("research_completed_at") or row.get("research_started_at"))
                if observation_date is None:
                    record = {
                        "research_candidate_id": row["research_candidate_id"],
                        "research_run_id": row["research_run_id"],
                        "symbol": symbol_name,
                        "observation_date": None,
                        "observation_price": _safe_float(row.get("candidate_latest_price"), None),
                        "benchmark_symbol": benchmark_name,
                        "benchmark_observation_price": None,
                        "label_status": "unavailable",
                        "data_source": "market_data",
                        "last_attempted_at": _utc_iso(),
                        "completed_at": _utc_iso(),
                        "error_message": "candidate observation date could not be parsed",
                        "created_at": _utc_iso(),
                        "updated_at": _utc_iso(),
                    }
                    for horizon in FORWARD_RETURN_HORIZONS:
                        prefix = f"forward_{horizon}d"
                        record[f"{prefix}_target_date"] = None
                        record[f"{prefix}_actual_date"] = None
                        record[f"{prefix}_future_price"] = None
                        record[f"{prefix}_benchmark_future_price"] = None
                        record[f"{prefix}_return"] = None
                        record[f"{prefix}_benchmark_return"] = None
                        record[f"{prefix}_excess_return"] = None
                        record[f"{prefix}_status"] = "unavailable"
                elif symbol_name in market_errors or benchmark_name in market_errors:
                    error_message = market_errors.get(symbol_name) or market_errors.get(benchmark_name) or "market data retrieval failed"
                    record = {
                        "research_candidate_id": row["research_candidate_id"],
                        "research_run_id": row["research_run_id"],
                        "symbol": symbol_name,
                        "observation_date": observation_date.isoformat(),
                        "observation_price": _safe_float(row.get("candidate_latest_price"), None),
                        "benchmark_symbol": benchmark_name,
                        "benchmark_observation_price": None,
                        "label_status": "data_error",
                        "data_source": "market_data",
                        "last_attempted_at": _utc_iso(),
                        "completed_at": _utc_iso(),
                        "error_message": error_message,
                        "created_at": row.get("evaluation_completed_at") or row.get("last_attempted_at") or _utc_iso(),
                        "updated_at": _utc_iso(),
                    }
                    for horizon in FORWARD_RETURN_HORIZONS:
                        prefix = f"forward_{horizon}d"
                        record[f"{prefix}_target_date"] = None
                        record[f"{prefix}_actual_date"] = None
                        record[f"{prefix}_future_price"] = None
                        record[f"{prefix}_benchmark_future_price"] = None
                        record[f"{prefix}_return"] = None
                        record[f"{prefix}_benchmark_return"] = None
                        record[f"{prefix}_excess_return"] = None
                        record[f"{prefix}_status"] = "data_error"
                else:
                    symbol_frame = symbol_frames[symbol_name]
                    benchmark_frame = benchmark_frames[benchmark_name]
                    record = _build_candidate_label_record(row, symbol_frame, benchmark_frame, current_day, observation_date)

                records.append(record)
                status_counts[str(record.get("label_status") or "pending").lower()] += 1
                _log(
                    "LABELING_CANDIDATE_PROCESSED",
                    candidate_id=row["research_candidate_id"],
                    symbol=symbol_name,
                    research_run_id=row["research_run_id"],
                    status=record.get("label_status"),
                )
                if record.get("label_status") == "partial":
                    _log("LABELING_CANDIDATE_PARTIAL", candidate_id=row["research_candidate_id"], symbol=symbol_name)
                elif record.get("label_status") == "pending":
                    _log("LABELING_CANDIDATE_PENDING", candidate_id=row["research_candidate_id"], symbol=symbol_name)
                elif record.get("label_status") == "unavailable":
                    _log("LABELING_CANDIDATE_UNAVAILABLE", candidate_id=row["research_candidate_id"], symbol=symbol_name)
                elif record.get("label_status") == "data_error":
                    _log("LABELING_CANDIDATE_DATA_ERROR", candidate_id=row["research_candidate_id"], symbol=symbol_name)

            calculation_seconds = round(time.perf_counter() - calculation_started, 4)
            write_started = time.perf_counter()
            write_result: dict[str, Any]
            if dry_run:
                write_result = {"storage": "dry_run", "stored_evaluation_count": 0, "saved_at": _utc_iso()}
            else:
                write_result = save_evaluation_results(records, database_url=self.database_url)
            database_write_seconds = round(time.perf_counter() - write_started, 4)
            total_seconds = round(time.perf_counter() - started, 4)

            _log(
                "LABELING_BATCH_COMPLETED",
                research_run_id=research_run_id or "all",
                symbol=symbol or "all",
                candidate_count=len(records),
                complete_count=status_counts["complete"],
                partial_count=status_counts["partial"],
                pending_count=status_counts["pending"],
                unavailable_count=status_counts["unavailable"],
                data_error_count=status_counts["data_error"],
                market_data_seconds=market_fetch_seconds,
                calculation_seconds=calculation_seconds,
                database_write_seconds=database_write_seconds,
                total_seconds=total_seconds,
            )

            return {
                "status": "completed",
                "candidates_processed": len(records),
                "label_status_counts": status_counts,
                "storage": write_result.get("storage"),
                "stored_evaluation_count": write_result.get("stored_evaluation_count", 0),
                "market_data_retrieval_seconds": market_fetch_seconds,
                "labeling_calculation_seconds": calculation_seconds,
                "database_write_seconds": database_write_seconds,
                "total_duration_seconds": total_seconds,
                "average_time_per_candidate": round(total_seconds / len(records), 6) if records else 0.0,
                "records": records,
            }
        finally:
            repository.close()


def label_research_candidates(
    database_url: str | None = None,
    research_run_id: str | None = None,
    symbol: str | None = None,
    limit: int = FORWARD_RETURN_MAX_LABEL_BATCH_SIZE,
    dry_run: bool = False,
    retry_data_errors: int = FORWARD_RETURN_RETRY_LIMIT,
    data_loader: Callable[[str, str, str], pd.DataFrame] = download_price_data,
) -> dict[str, Any]:
    labeler = ForwardReturnLabeler(database_url=database_url, data_loader=data_loader)
    return labeler.label_candidates(
        research_run_id=research_run_id,
        symbol=symbol,
        limit=limit,
        dry_run=dry_run,
        retry_data_errors=retry_data_errors,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Label stored research candidates with forward returns")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--research-run-id", default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=FORWARD_RETURN_MAX_LABEL_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-data-errors", type=int, default=FORWARD_RETURN_RETRY_LIMIT)
    args = parser.parse_args()

    result = label_research_candidates(
        database_url=args.database_url,
        research_run_id=args.research_run_id,
        symbol=args.symbol,
        limit=args.limit,
        dry_run=args.dry_run,
        retry_data_errors=args.retry_data_errors,
    )
    print(result)


if __name__ == "__main__":
    main()