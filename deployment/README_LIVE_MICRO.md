# Controlled micro-live launch

This runner is separate from the PAPER runner. It supports long US stocks only,
whole-share GTC bracket entries only, and starts unable to place orders.

## Hard limits

- Account equity must remain at or below $500.
- One new entry per UTC day, at most $30 and 10% of equity.
- Three positions and 30% gross exposure maximum.
- At least 70% of equity remains in cash.
- A $3 or 1% daily account loss (whichever is smaller) stops new entries.
- Shorts, crypto, options, margin borrowing, and extended hours are disabled.
- Existing positions must have both protective sell legs visible at Alpaca.

The broker-held bracket is a protection, not a guarantee: gaps, halts, slippage,
and outages can produce a worse fill than the stop price.

## Install without enabling

Copy `live-micro.example.env` to `/etc/quant-bot/quant-bot-live.env`, put the
LIVE credentials directly into that server file, and set ownership to
`root:quantbot` with mode `640`. Never put credentials in Git, the dashboard,
terminal screenshots, Discord, or chat.

Copy `quant-bot-live-micro.service` into `/etc/systemd/system/`, run
`systemctl daemon-reload`, and leave the service disabled and stopped.

The read-only check does not require any activation flags and cannot submit:

```bash
sudo -u quantbot bash -lc 'set -a; source /etc/quant-bot/quant-bot-live.env; set +a; /home/quantbot/quant-trading-bot/.venv/bin/python /home/quantbot/quant-trading-bot/controlled_live_runner.py --check-account'
```

Before activation, reset the PAPER account to the same $300 capital and run the
same allowlist and limits for at least two weeks. Keep the public mobile
dashboard PAPER-only; live balances must not be exposed until authentication is
restored.

## Deliberate activation

Activation requires all of the following in the separate live environment:

```text
LIVE_TRADING_ENABLED=true
ALPACA_LIVE_ORDER_SUBMISSION_ENABLED=true
LIVE_TRADING_CONFIRMATION=ENABLE_LIVE_MICRO_TRADING
LIVE_KILL_SWITCH=false
LIVE_PRIVATE_DASHBOARD_CONFIRMED=true
LIVE_ALLOWED_SYMBOLS=<reviewed whole-share symbols>
```

Run one foreground cycle first. Only after inspecting the broker order and both
bracket legs should the service be enabled. To stop new entries immediately,
set `LIVE_KILL_SWITCH=true` and restart the service. Broker-held protective
orders remain at Alpaca and must be reviewed there.
