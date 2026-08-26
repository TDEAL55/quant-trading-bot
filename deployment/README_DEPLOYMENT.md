# Deployment Readiness

This profile runs autonomous PAPER trading only on DigitalOcean. LIVE mode is hard-blocked.

For intraday autonomous mode, use the continuous service (`continuous_paper_runner.py`) so the process remains active and scans every `SCAN_INTERVAL_MINUTES` during market hours.

## Components reused

- `quant-bot.service`: existing oneshot systemd service.
- `quant-bot.timer`: weekday once-daily cadence at 09:30 America/New_York.
- `unattended_daily_runner.py`: single unattended entrypoint.
- `daily_research_runner.py`: scanner, shortlist, risk, execution, and reconciliation orchestration.
- Existing lock file behavior and `backup_daily_database.sh` post-run backup hook.

## Environment file

Path: `/etc/quant-bot/quant-bot.env`

Minimum required values:

```bash
APP_ENV=production
DATABASE_URL=sqlite:////var/lib/quant-bot/quant-bot.db
DASHBOARD_APP_AUTH_ENABLED=false
DASHBOARD_EXTERNAL_AUTH_ENABLED=true
TRADING_MODE=PAPER
PAPER_BROKER_BACKEND=ALPACA
ALPACA_API_KEY=REPLACE_ME
ALPACA_API_SECRET=REPLACE_ME
ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets
ALPACA_ORDER_SUBMISSION_ENABLED=true
AUTO_APPROVE_PAPER=true
NOTIFICATIONS_ENABLED=false
KILL_SWITCH=false
RUN_TIMEZONE=America/New_York
SCAN_INTERVAL_MINUTES=5
SCAN_ONLY_DURING_MARKET_HOURS=true
CONTINUOUS_RUNNER_DRY_RUN=true
PAPER_EXECUTION_ENABLED=false
CONTROLLED_PAPER_VALIDATION=false
MAX_DAILY_ORDERS=5
MAX_OPEN_POSITIONS=10
MAX_POSITION_EQUITY_PERCENT=10
SCAN_SYMBOLS=

PAPER_DAILY_CYCLE_ENABLED=true
PAPER_DAILY_AUTO_ENTRY_EXECUTION=true
PAPER_DAILY_MAX_NEW_POSITIONS=0
PAPER_MAX_OPEN_POSITIONS=0
PAPER_DAILY_REQUIRE_LEDGER_INTEGRITY_PASS=true

DISCORD_WEBHOOK_URL=
DISCORD_NO_TRADE_NOTIFICATION_MINUTES=60
```

## Exact Ubuntu commands

1. Pull latest code

```bash
cd /home/quantbot/quant-trading-bot
git fetch --all --prune
git checkout main
git pull --ff-only
```

2. Activate virtual environment

```bash
cd /home/quantbot/quant-trading-bot
source .venv/bin/activate
```

3. Install dependencies

```bash
cd /home/quantbot/quant-trading-bot
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

4. Create or update `.env` for app runtime

```bash
sudo install -d -m 0750 -o root -g quantbot /etc/quant-bot
sudo cp /home/quantbot/quant-trading-bot/deployment/deploy.example.env /etc/quant-bot/quant-bot.env
sudo chown root:quantbot /etc/quant-bot/quant-bot.env
sudo chmod 0600 /etc/quant-bot/quant-bot.env
sudo nano /etc/quant-bot/quant-bot.env
```

5. Read-only Alpaca paper connection check

```bash
cd /home/quantbot/quant-trading-bot
source .venv/bin/activate
python alpaca_paper_connection_check.py
```

6. Run one dry cycle (submission disabled)

```bash
cd /home/quantbot/quant-trading-bot
source .venv/bin/activate
python unattended_daily_runner.py
```

7. Enable submission only after checks pass

```bash
sudo sed -i 's/^ALPACA_ORDER_SUBMISSION_ENABLED=.*/ALPACA_ORDER_SUBMISSION_ENABLED=true/' /etc/quant-bot/quant-bot.env
sudo systemctl daemon-reload
```

8. Run a controlled paper cycle

```bash
cd /home/quantbot/quant-trading-bot
source .venv/bin/activate
python unattended_daily_runner.py
```

9. Run a second same-session cycle to verify duplicate protection

```bash
cd /home/quantbot/quant-trading-bot
source .venv/bin/activate
python unattended_daily_runner.py
```

10. Install/refresh systemd services

```bash
cd /home/quantbot/quant-trading-bot
sudo cp deployment/quant-bot-continuous.service /etc/systemd/system/quant-bot-continuous.service
sudo cp deployment/quant-bot-dashboard.service /etc/systemd/system/quant-bot-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable quant-bot-continuous.service
sudo systemctl restart quant-bot-continuous.service
sudo systemctl enable quant-bot-dashboard.service
```

11. (Optional) Keep existing daily oneshot service/timer for end-of-day summary/backup workflows

```bash
cd /home/quantbot/quant-trading-bot
sudo cp deployment/quant-bot.service /etc/systemd/system/quant-bot.service
sudo cp deployment/quant-bot.timer /etc/systemd/system/quant-bot.timer
sudo systemctl daemon-reload
sudo systemctl restart quant-bot.timer
sudo systemctl start quant-bot.service
```

12. Verify continuous service status

```bash
systemctl status quant-bot-continuous.service --no-pager
```

13. Verify dashboard service status

```bash
systemctl status quant-bot-dashboard.service --no-pager
```

14. Verify daily service status (optional)

```bash
systemctl status quant-bot.service --no-pager
```

15. Verify timer status and next run

```bash
systemctl status quant-bot.timer --no-pager
systemctl list-timers quant-bot.timer --all
```

16. View live logs

```bash
journalctl -u quant-bot-continuous.service -f -n 200
journalctl -u quant-bot-dashboard.service -f -n 200
journalctl -u quant-bot.service -f -n 200
```

17. Confirm no live broker order path was called

```bash
journalctl -u quant-bot.service -n 500 --no-pager | grep -Ei "broker execution blocked|no broker orders were submitted|Trading mode must be exactly PAPER|LIVE trading is hard-blocked"
```

## One-command remote deployment

Authorize a dedicated deployment key for `quantbot` and allow that account to restart only the two PAPER services. Then run:

```powershell
powershell -ExecutionPolicy Bypass -File deployment/deploy_remote.ps1 -IdentityFile C:\path\to\deployment-key
```

The script performs a fast-forward-only update, restarts the PAPER runner and dashboard, verifies both services, checks Streamlit health, and prints the deployed commit. It never enables LIVE trading.

## Security notes

- The DigitalOcean dashboard disables its in-app password gate only because nginx Basic Auth is the mandatory external authentication layer.
- Never commit secrets; set `DISCORD_WEBHOOK_URL` only in server env.
- Keep `/etc/quant-bot/quant-bot.env` mode `0600` owned by `root:quantbot`.
- Service must run as non-root `quantbot`.
- `TRADING_MODE=LIVE` is blocked by deployment config and runtime checks.
