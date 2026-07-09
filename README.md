# Quant Trading Bot

This project is a research-only Python bot for exploring quantitative trading ideas with historical data. It does not connect to any broker, does not place real trades, and keeps LIVE mode blocked.

## Current project status
- Verified working modules: market data download, moving-average strategy, backtest, paper broker adapter, simulation runner, dashboard, trade journal, replay engine, and error handling.
- Safety protections: LIVE mode is blocked, the broker adapter is read-only, and simulation-only decisions are logged locally.
- Verification: the full test suite currently passes with 31 tests.

## What it does
- Downloads historical price data
- Runs a simple moving-average crossover strategy
- Simulates a paper-trading backtest
- Records simulated decisions in a local journal
- Replays historical data deterministically for inspection
- Prints a simple terminal dashboard

## Project structure
- main.py - main workflow entry point
- simulation_runner.py - simulation workflow runner
- strategy.py - moving-average crossover strategy
- market_data.py - downloads historical price data
- backtest.py - paper-trading backtest simulation
- paper_broker.py - read-only paper broker adapter
- trade_journal.py - local CSV journal for simulated decisions
- replay_engine.py - historical replay engine
- error_handler.py - centralized error handling
- dashboard.py - terminal dashboard
- config.py - configuration settings
- logger_setup.py - logging utilities

## Install dependencies
Create and activate a virtual environment, then run:

```bash
pip install pandas numpy matplotlib python-dotenv yfinance pytest
```

## Run the main workflow
```bash
python main.py
```

## Run the simulation workflow
```bash
python simulation_runner.py
```

## Run the dashboard
```bash
python dashboard.py
```

## Notes
This software is for research and education only. It does not guarantee profits and should not be used as financial advice.

## Railway deployment
This project can run as a Railway scheduled worker in paper-trading mode only.

### Safety defaults for cloud
- PAPER mode only (`TRADING_MODE=PAPER`)
- LIVE mode blocked (`TRADING_MODE=LIVE` raises and exits)
- Real-money trading is blocked (paper client only)
- No automatic strategy changes
- No automatic parameter optimization
- Daily summaries are still written to `daily_summaries/`
- Final report is written to `TWO_WEEK_REPORT.md`

### Files used by Railway
- `Procfile` with worker entrypoint: `python railway_start.py`
- `railway_start.py` runs exactly one safe market-day cycle by calling `run_two_week_paper_runner(days=1)`
- `railway_start.py` uses a marker file (`.railway_last_run.json`) to avoid duplicate same-day runs

### Required Railway environment variables
Set these in Railway service variables (do not commit credentials):

- `TRADING_MODE=PAPER`
- `ALPACA_API_KEY=<your_paper_key>`
- `ALPACA_API_SECRET=<your_paper_secret>`

Optional:

- `RAILWAY_RUN_MARKER_PATH=.railway_last_run.json`

### Scheduler setup in Railway
Create a Railway cron/scheduled job that triggers once per market day. Recommended schedule:

- Weekdays once per day before/around market open, e.g. `55 9 * * 1-5` (configure timezone in Railway)

The runner performs a market-open check and skips safely when closed.
