# Controlled micro-live launch

This runner is separate from the PAPER runner. It supports long US stocks only,
whole-share GTC bracket entries only, and starts unable to place orders.

## Hard risk controls

- Account equity must remain at or below the explicitly configured live ceiling.
- There is no daily entry-count cap. Each new entry may use at most 25% of
  current account equity and can never exceed available cash, preserving buying
  power for additional qualifying stocks.
- A $3 or 1% daily account loss (whichever is smaller) stops new entries.
- A 3% weekly realized drawdown stops new entries.
- Three consecutive realized losses trigger a 60-minute cooldown.
- Every order is whole-share sized from stop distance and at most 0.5% account
  risk; the allocation cap can only reduce that size.
- Correlation data is required when positions are already open. Highly
  correlated positions share a 20% account-exposure ceiling.
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
LIVE_FULL_STOCK_UNIVERSE=true
LIVE_ENTRY_CASH_ALLOCATION_PERCENT=25
```

Run one foreground cycle first. Only after inspecting the broker order and both
bracket legs should the service be enabled. To stop new entries immediately,
set `LIVE_KILL_SWITCH=true` and restart the service. Broker-held protective
orders remain at Alpaca and must be reviewed there.

The live state file also records once-per-cycle price observations for MFE/MAE
review. The dashboard reads optional backtest and paper JSON artifacts and
compares them with live results on every refresh. Missing history is labeled
unknown and never invented or used to justify more risk.

When `LIVE_TRAILING_STOP_RATCHET_SHADOW_ENABLED=true`, the runner also writes
counterfactual exit evidence to `SHADOW_EXIT_TRIAL_LEDGER_PATH`. The ledger is
observation-only: it has no broker client and cannot submit, replace, cancel, or
close orders. It records hypothetical ratchets and reconciles them with later
verified broker fills so the dashboard can evaluate a meaningful completed
sample.

`LIVE_TRAILING_STOP_RATCHET_ENABLED=false` is the safe default. When it is
explicitly set to `true`, the runner may raise an existing, verified Alpaca
bracket stop after that strategy's profit-trailing threshold has been reached.
This is the pre-stop profit-protection plan: the original broker stop remains
the emergency floor, while the raised stop can exit a profitable trade before
it gives back the entire move. The runner cannot loosen a stop, remove bracket
protection, act on an unattributed position, or make a stop executable above
the current market price. A failed replacement leaves the original stop in
force. Strategy invalidation and time-based exits remain observation-only until
they have enough paper/shadow evidence for a separate, testable promotion.

Only verified parent identities are admitted, gap-through-stop simulations use
the worse of the stop and observed trigger price, and ledger writes are atomic
and cross-process locked. The dashboard keeps non-comparable (censored) closes
visible and never treats this monitoring checkpoint as authorization to change
live exit behavior.
