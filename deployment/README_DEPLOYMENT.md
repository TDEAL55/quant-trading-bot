# Deployment Readiness

This deployment profile is paper-only and keeps LIVE trading hard-blocked.

## Files

- `quant-bot.service`: oneshot systemd service run as `quantbot`.
- `quant-bot.timer`: daily timer in `America/New_York` with `Persistent=true`.
- `install_server.sh`: installs the service and timer on Linux.
- `deploy.example.env`: template environment file with no secrets.

## Required environment settings

- `APP_ENV`
- `DATABASE_URL`
- `TRADING_MODE=PAPER`
- `AUTO_APPROVE_PAPER=true|false`
- `MAX_DAILY_ORDERS=1`
- `RUN_TIMEZONE=America/New_York`
- `RUN_HOUR`
- `RUN_MINUTE`
- `NOTIFICATIONS_ENABLED`
- `KILL_SWITCH`

## Security instructions

- Never commit secrets.
- Keep `/etc/quant-bot/quant-bot.env` mode `600` and owned by `root:quantbot`.
- Run the service as the dedicated non-root `quantbot` user.
- Do not expose the dashboard publicly.

## Database

- Use SQLite at a persistent path such as `/var/lib/quant-bot/quant-bot.db`.
- Keep WAL mode enabled through the application database layer.
- Back up the database daily and retain 14 backups.
- The service runs `deployment/backup_daily_database.sh` after each unattended run.

## Operational notes

- Missed timer runs may execute after recovery because the timer uses `Persistent=true`.
- The runner still refuses stale market data.
- `KILL_SWITCH=true` stops execution immediately.
