# Baseline Status

## Working modules
- Market data download via historical data providers
- Moving-average crossover strategy
- Paper-only backtest simulation
- Read-only paper broker adapter
- Simulation runner
- Terminal dashboard
- Local trade journal for simulated decisions
- Historical replay engine
- Centralized error handling

## Safety protections
- LIVE mode remains blocked
- The broker adapter does not submit orders
- Trading actions are simulation-only and never reach a real broker
- The project uses local files for logs and journal entries

## Test count
- Full suite: 31 tests passing

## Known limitations
- This is a research-only project and should not be treated as a production trading system
- Strategy logic is intentionally simple
- Market data availability depends on network access and external provider behavior
- No real broker connection or live order execution is implemented
