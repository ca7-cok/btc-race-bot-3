[![make.fun](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![Rank](https://make.fun/badge/btc-race-24h-20261123-0511/ca7-cok/btc-race-bot-3/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![Reward](https://make.fun/badge/btc-race-24h-20261123-0511/ca7-cok/btc-race-bot-3/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

# DipCatcher-3

A long-only cbBTC trading bot for make.fun's **Race a BTC trading bot live
for 24 hours** bounty (`btc-race-24h-20261123-0511`), $50 in USDC.

## Strategy

Two tiers, both long-only (shorts are disabled by the harness):

- **Tier 1 — trend ride.** Once the uptrend is established (120-bar
  return >= +50 bps **and** EMA-60 above EMA-240 by >= +50 bps), buy
  100% of cash. Hold with a wide trailing stop (250 bps below the highest
  close since entry). At most two Tier-1 entries per window; no entries
  after bar 1200.
- **Tier 2 — probe.** If no Tier-1 entry ever fired and bar >= 1080, buy
  25% of cash when the close crosses above EMA-30; exit on a 100 bps
  trailing stop or at bar 1400. This guarantees at least one trade (the
  guardrail requires one) while keeping worst-case damage small on down
  days.

Why so few trades: a full-size round trip pays ~26 bps in fee, slippage
and pool impact, so the bot trades 1–3 round trips a day and never chases
one-minute noise. Orders fill at the next bar's open; every order carries
`max_slippage_bps` 200 (one-minute moves in the dev window stayed under
~75 bps), so a gap-up fill cannot run away from the signal.

## Backtest (dev window, exact local replay of the harness fill model)

| Bot | Dev net PnL (USDC) | Trades |
| --- | --- | --- |
| **DipCatcher-3** | **+21.00** | 1 |
| buy-and-hold | +32.17 | 1 |
| momentum | +34.94 | 1 |
| ema-cross | −114.83 | 76 |
| rsi-bollinger | −9.32 | 10 |
| adx-momentum | −11.20 | 24 |
| hold-cash | 0.00 | 0 |

Buy-and-hold and the momentum baseline beat us on this one up-trending
window — they were fully invested from the first bar, while our entry
waits for trend confirmation (bar 240). That is deliberate: on the
sign-flipped (down) version of the same window we lose only **−$0.71**
(buy-and-hold loses −$38.90 there), and across 24 block-bootstrap
windows the median is +$3.19 with no window worse than −$28.30 (the
bounded cost of one stopped-out full-size entry). The bot is built to
survive the race window, not to overfit the dev window.

## Run

```sh
python3 main.py
```

Reads newline-delimited JSON (`hello` / `batch` / `done`) on stdin and
writes one `reply` per batch on stdout. Python 3.13 standard library
only. All state is integer arithmetic (fixed-point EMAs), so two runs on
the same data emit byte-identical orders. Any unexpected input is
answered with an empty order list rather than crashing.
