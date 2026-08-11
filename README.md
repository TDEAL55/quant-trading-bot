# Quant Trading Bot

This project is a research-only Python bot for exploring quantitative trading ideas with historical data. It does not connect to any broker, does not place real trades, and keeps LIVE mode blocked.

## Current project status
- Verified working modules: market data download, moving-average strategy, backtest, paper broker adapter, simulation runner, dashboard, trade journal, replay engine, and error handling.
- Safety protections: LIVE mode is blocked, the broker adapter is read-only, and simulation-only decisions are logged locally.
- Verification: the full test suite currently passes in CI/local for the active branch.

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

## DigitalOcean deployment
DigitalOcean VPS is the single supported production deployment target.

### Deployment architecture

```text
DigitalOcean VPS
|- quant-bot-continuous.service
|  `- continuous_paper_runner.py
|- quant-bot-dashboard.service
|  `- streamlit dashboard_app.py
|- SQLite database (/var/lib/quant-bot/quant-bot.db)
|- Discord notifications
`- journalctl logs
```

### Safety defaults for production
- PAPER mode only (`TRADING_MODE=PAPER`)
- Dry-run enabled by default (`CONTINUOUS_RUNNER_DRY_RUN=true`)
- Controlled execution disabled by default (`PAPER_EXECUTION_ENABLED=false`, `CONTROLLED_PAPER_VALIDATION=false`)
- LIVE mode blocked (`TRADING_MODE=LIVE` raises and exits)

### Initial server deployment
1. Clone and update repository:

```bash
cd /home/quantbot
git clone https://github.com/TDEAL55/quant-trading-bot.git
cd quant-trading-bot
git checkout sprint14-continuous-runner
git pull --ff-only
```

2. Create virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Configure runtime environment:

```bash
sudo install -d -m 0750 -o root -g quantbot /etc/quant-bot
sudo cp deployment/deploy.example.env /etc/quant-bot/quant-bot.env
sudo chown root:quantbot /etc/quant-bot/quant-bot.env
sudo chmod 0600 /etc/quant-bot/quant-bot.env
sudo nano /etc/quant-bot/quant-bot.env
```

4. Install migrations:

```bash
source .venv/bin/activate
python monitoring_db.py --migrate
```

5. Install systemd units:

```bash
sudo cp deployment/quant-bot-continuous.service /etc/systemd/system/
sudo cp deployment/quant-bot-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Continuous runner

```bash
sudo systemctl enable quant-bot-continuous.service
sudo systemctl start quant-bot-continuous.service
```

### Dashboard

```bash
sudo systemctl enable quant-bot-dashboard.service
sudo systemctl start quant-bot-dashboard.service
```

### Health checks

```bash
systemctl status quant-bot-continuous.service
systemctl status quant-bot-dashboard.service
```

### Logs

```bash
journalctl -u quant-bot-continuous.service
journalctl -u quant-bot-dashboard.service
```

## Read-only monitoring dashboard (v1)

### Architecture
- Worker writes sanitized run snapshots to PostgreSQL after each run.
- Dashboard reads PostgreSQL only.
- Dashboard never receives Alpaca API credentials.
- Existing JSON safety state remains active for order limits/cooldowns.

### Security guarantees
- Read-only UI. No buy/sell/cancel/submit actions.
- PAPER mode only. LIVE remains blocked.
- Password gate via `DASHBOARD_PASSWORD` env variable only.
- Sanitized monitoring fields (no API keys/secrets/authorization headers/account numbers/full order IDs).
- Monitoring DB write failures are non-blocking and cannot trigger extra orders.

### Database setup
Schema migration file:
- `migrations/001_monitoring_schema.sql`

Tables:
- `bot_runs`
- `signal_snapshots`
- `paper_account_snapshots`
- `sanitized_order_events`

Retention helper SQL is available in `MonitoringDatabase.retention_sql()` and is not auto-executed.

### Local run (dashboard only default)
1. Set environment variables:
	- `DATABASE_URL=...`
	- `DASHBOARD_PASSWORD=...`
2. Start dashboard:
	- `streamlit run dashboard_app.py`

The dashboard must not be connected to LIVE mode or any order-submission endpoint.

## Sprint 10: Factor Intelligence Engine (research-only)

### What it does
- Registers existing scanner factors in a versioned factor registry.
- Builds normalized historical factor observations while preserving raw values.
- Measures predictive relationships versus stored forward-return labels.
- Computes bucket/decile behavior, walk-forward stability, regime behavior, and factor redundancy.
- Produces a descriptive factor scorecard and security-level explanation payloads.
- Exposes read-only CLI and dashboard views for historical research interpretation.

### What it does not prove
- It does not prove profitability.
- It does not prove causation.
- It does not auto-promote factors into production weights.
- It does not submit broker orders and does not enable LIVE mode.

### Factor registry rules
- Factor IDs are deterministic and versioned.
- Duplicate factor_id + version pairs are rejected.
- Category and direction values are validated.
- Existing Sprint 1-9 factor calculations remain unchanged.

### Predictive metrics
- Sample count, valid count, missing count.
- Pearson and Spearman correlations.
- Mean/median forward return and optional excess-return equivalents.
- Top-minus-bottom spread and positive-return rate.
- Confidence labels and insufficient-data status.

### Bucket analysis
- Deterministic bucket assignment with tie-safe ordering.
- Default deciles when sample size allows, fallback to fewer buckets when needed.
- Monotonicity, direction consistency, spread, and coverage metrics.

### Stability and walk-forward
- Reuses existing walk-forward window generation.
- Keeps training and validation windows separated.
- Reports per-window metrics and aggregate stability classifications.

### Regime analysis
- Uses observed regime labels at observation time.
- Preserves unknown regimes.
- Marks low-sample regime slices as insufficient_data.

### Redundancy analysis
- Computes pairwise aligned factor correlations.
- Uses deterministic pair ordering (A/B only, not B/A duplicates).
- Flags possible redundancy without deleting factors.

### Scorecard interpretation
- Score is descriptive research evidence only.
- Component weights are explicit and inspectable in export payloads.
- Missing evidence lowers confidence and adds warnings.

### Look-ahead prevention
- Forward returns are only consumed from completed stored labels.
- Factor values are derived from candidate-time observations, not future returns.

### Data-quality controls
- Counts and reports duplicates, missing values, invalid values, and excluded rows.
- Never silently maps missing values to zero.

### Dashboard usage
- New read-only Factor Intelligence page in Streamlit dashboard.
- Shows latest run summary, leaderboard, predictive table, buckets, stability, regimes, redundancy, and warnings.

### CLI usage
Run commands from repository root:

```bash
python factor_intelligence.py run --start-date 2024-01-01 --end-date 2024-12-31 --forward-horizon 20 --factor-id overall_score --factor-id trend_score --minimum-sample-size 30 --bucket-count 10
python factor_intelligence.py latest
python factor_intelligence.py leaderboard --run-id <RUN_ID>
python factor_intelligence.py factor --factor-id overall_score
python factor_intelligence.py explain --symbol AAPL --snapshot-id <RUN:DATE>
python factor_intelligence.py export --run-id <RUN_ID> --output factor_intelligence.json
```

### Safety reminder
- Results are historical research analytics only.
- Strategy weights are not auto-modified.
- Paper validation behavior remains unchanged.
- LIVE remains blocked globally.

## Sprint 10.1: Controlled Paper Trading Validation

### Scope
- Adds a dedicated Sprint 10.1 orchestration module in `sprint_10_1_validation.py`.
- Reuses existing scanner, research journaling, approval, paper validation, reconciliation, persistence, and dashboard payload readers.
- Does not modify scanner algorithms, factor engine logic, portfolio construction internals, risk engine internals, or paper broker internals.

### Test profile behavior
- Uses a strict paper-safe `PaperTestProfile` with `maximum_orders=1`.
- Requires manual approval simulation (`--manual-approval YES`) before execution.
- Rejects LIVE mode at the orchestration boundary.

### End-to-end flow
- Runs scanner and shortlist using existing modules.
- Captures a compact explainability block from selected candidate factor components.
- Records a research journal entry for the scan payload.
- Creates a dedicated paper approval record for the controlled profile.
- Executes exactly one paper-validation run path using `run_paper_validation`.
- Fetches the paper-validation dashboard payload for post-run visibility.

### No-candidate behavior
- Returns structured reason counts from scanner rejection reasons.
- Returns the closest candidate payload when available.
- Keeps manual approval and safety controls active even when no trade is executed.

### CLI usage

```bash
python sprint_10_1_validation.py --database-url <DATABASE_URL> --manual-approval YES --execute
```

Optional symbol override:

```bash
python sprint_10_1_validation.py --database-url <DATABASE_URL> --manual-approval YES --execute --symbols SPY,QQQ,AAPL
```
