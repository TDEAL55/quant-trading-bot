# OVERNIGHT_COST_SENSITIVITY_2023

Research/backtest-only SPY overnight cost sensitivity analysis.

## Scope

- Strategy timing fixed: Buy 3:58 PM ET, sell 9:32 AM ET next trading day.
- Symbol: SPY only.
- Data: Alpaca SIP minute bars.
- Execution: no order submission, no Railway integration, LIVE mode blocked.
- Calendar year: 2023.
- Processing model: month-by-month with resumable progress and per-month cache directories.

## Cost Model

- No Alpaca stock commissions added by default.
- Regulatory fees modeled separately: 0.0000 bps round trip.
- Remaining scenario cost is modeled as slippage.

## Gross Metrics (No Modeled Costs)

- trade count: 248
- gross compounded return: 9.370786%
- average gross trade: 0.037340%
- win rate: 53.2258%
- maximum drawdown: -7.954197%
- Sharpe ratio: 1.199725
- break-even round-trip cost: 3.611839 bps

## Net Metrics by Cost Scenario

| Total Round-Trip Cost (bps) | Slippage (bps) | Regulatory Fees (bps) | Net Compounded Return | Average Net Trade | Win Rate | Maximum Drawdown | Sharpe Ratio |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.0000 | 0.0000 | 0.0000 | 9.370786% | 0.037340% | 53.2258% | -7.954197% | 1.199725 |
| 2.0000 | 2.0000 | 0.0000 | 4.078332% | 0.017335% | 50.8065% | -8.760647% | 0.557070 |
| 5.0000 | 5.0000 | 0.0000 | -3.384053% | -0.012666% | 48.3871% | -10.007090% | -0.407154 |
| 10.0000 | 10.0000 | 0.0000 | -14.651422% | -0.062647% | 46.7742% | -18.489579% | -2.014837 |
| 20.0000 | 20.0000 | 0.0000 | -33.397398% | -0.162535% | 37.9032% | -33.707130% | -5.232617 |

## Monthly Completion

- 2023-01: trades=20 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-01
- 2023-02: trades=19 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-02
- 2023-03: trades=23 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-03
- 2023-04: trades=19 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-04
- 2023-05: trades=22 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-05
- 2023-06: trades=21 skipped_nonfixed_timing=1 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-06
- 2023-07: trades=19 skipped_nonfixed_timing=1 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-07
- 2023-08: trades=23 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-08
- 2023-09: trades=20 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-09
- 2023-10: trades=22 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-10
- 2023-11: trades=20 skipped_nonfixed_timing=1 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-11
- 2023-12: trades=20 skipped_nonfixed_timing=0 feed=sip cache=C:\Users\dealt\OneDrive\quant-trading-bot\.overnight_cache\cost_sensitivity_2023\alpaca_1m\2023-12
