import time

from logger_setup import logger
from simulation_runner import run_simulation
from config import TRADING_MODE


def run_scheduler(interval_seconds=60, max_runs=1):
    """Run the simulation workflow on a configurable interval for a limited number of cycles."""
    if TRADING_MODE != "SIMULATION":
        logger.warning("Scheduler only supports SIMULATION mode for safety")
        return []

    results = []
    for run_index in range(max_runs):
        try:
            logger.info(f"scheduler run {run_index + 1} started")
            result = run_simulation()
            results.append(result)
            logger.info(f"scheduler run {run_index + 1} completed")
        except Exception as exc:
            logger.error(f"scheduler run {run_index + 1} failed: {exc}")
            break

        if run_index < max_runs - 1:
            time.sleep(interval_seconds)

    logger.info("scheduler stopped")
    return results
