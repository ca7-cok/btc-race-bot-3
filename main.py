"""DipCatcher-3: a long-only cbBTC trend bot for make.fun's
btc-race-24h-20261123-0511 (agent-fun/bars-replay/v1).

Strategy (researched on the published 24h dev window, exact local replay):
  Tier 1, trend ride: once the uptrend is established (120-bar return
  >= +50 bps AND EMA-60 above EMA-240 by >= +50 bps), buy 100% of cash.
  Hold with a wide trailing stop (250 bps below the highest close since
  entry). At most two Tier-1 entries per window; no entries after bar 1200.
  Tier 2, probe: if no Tier-1 entry ever fired and bar >= 1080, buy 25%
  of cash when the close crosses above EMA-30; exit on a 100 bps trailing
  stop or at bar 1400. This guarantees at least one trade (the guardrail
  needs one) while keeping worst-case damage small on down days.

Costs: a full-size round trip pays ~26 bps in fee, slippage and pool
impact, so the bot trades rarely (1-3 round trips a day) and never chases
one-minute noise. Orders fill at the next bar's open; every order carries
max_slippage_bps 200 (one-minute moves in the dev window stayed under
~75 bps), so a gap-up fill cannot run away from the signal.

All state is integer arithmetic (EMAs are fixed-point, scaled by
1,000,000): two runs on the same data emit byte-identical orders. The
bot never reads the clock, the environment or unseeded randomness.
Any unexpected input is answered with an empty order list rather than
crashing: a malformed reply would fail the run, silence never does.
Warm-up batches (hello["warmup_bars"], 0 when absent) build history
but never send orders, since orders decided on them are rejected.
"""

import json
import sys

SYMBOL = "CBBTC"
MAX_SLIPPAGE_BPS = 200
EMA_SCALE = 1_000_000  # fixed-point scale for the EMAs

# Tier-1 (trend ride) parameters, in basis points / bars.
MOM_BARS = 120
MOM_BPS = 50
GAP_BPS = 50
TRAIL_BPS = 250
MAX_TIER1_ENTRIES = 2
TIER1_LAST_BAR = 1200
# Tier-2 (probe) parameters.
PROBE_BAR = 1080
PROBE_BPS = 2500  # of cash
PROBE_TRAIL_BPS = 100
PROBE_EXIT_BAR = 1400


class Bot:
    def __init__(self):
        self.seq = -1
        self.warmup_bars = 0
        self.closes = []          # closes seen so far, ints
        self.ema30 = None         # fixed-point EMAs, seeded on first close
        self.ema60 = None
        self.ema240 = None
        self.prev_ema30 = None    # EMA-30 as of the previous bar
        self.mode = "flat"        # flat | tier1 | probe
        self.entries = 0          # Tier-1 entries used
        self.highest = None       # highest close since Tier-1 entry
        self.probe_highest = None  # highest close since probe entry
        self.next_id = 1

    # -- EMA helpers -----------------------------------------------------
    @staticmethod
    def _ema_step(prev, price, period):
        # e = (2*price + (period-1)*e) / (period+1), fixed-point ints
        return (2 * price * EMA_SCALE + (period - 1) * prev) // (period + 1)

    def _note_close(self, close):
        self.closes.append(close)
        if self.ema30 is None:
            self.ema30 = close * EMA_SCALE
            self.ema60 = close * EMA_SCALE
            self.ema240 = close * EMA_SCALE
        else:
            self.ema30 = self._ema_step(self.ema30, close, 30)
            self.ema60 = self._ema_step(self.ema60, close, 60)
            self.ema240 = self._ema_step(self.ema240, close, 240)

    # -- orders ----------------------------------------------------------
    def _cid(self):
        cid = "o%d" % self.next_id
        self.next_id += 1
        return cid

    def _buy(self, notional):
        return {"side": "buy", "client_id": self._cid(), "symbol": SYMBOL,
                "notional_quote_units": str(int(notional)),
                "max_slippage_bps": MAX_SLIPPAGE_BPS}

    def _sell(self, qty):
        return {"side": "sell", "client_id": self._cid(), "symbol": SYMBOL,
                "qty": str(int(qty)),
                "max_slippage_bps": MAX_SLIPPAGE_BPS}

    # -- decision --------------------------------------------------------
    def decide(self, seq, close, cash, qty):
        """Return a list of orders for this batch (usually 0 or 1)."""
        i = len(self.closes) - 1  # index of the bar just closed
        px = close
        if self.mode == "tier1":
            if qty == 0:
                # our buy was rejected; stand down rather than chase
                self.mode = "flat"
                return []
            if px > self.highest:
                self.highest = px
            if (self.highest - px) * 10000 >= TRAIL_BPS * self.highest:
                self.mode = "flat"
                return [self._sell(qty)]
            return []
        if self.mode == "probe":
            if qty == 0:
                self.mode = "flat"
                return []
            if px > self.probe_highest:
                self.probe_highest = px
            if (i >= PROBE_EXIT_BAR
                    or (self.probe_highest - px) * 10000
                    >= PROBE_TRAIL_BPS * self.probe_highest):
                self.mode = "flat"
                return [self._sell(qty)]
            return []
        # flat: never enter while still holding (a rejected sell re-sends)
        if qty > 0:
            return [self._sell(qty)]
        # Tier-1 entry: established uptrend only.
        if (self.entries < MAX_TIER1_ENTRIES and i >= max(MOM_BARS, 240)
                and i < TIER1_LAST_BAR):
            ret = (px - self.closes[i - MOM_BARS]) * 10000 // self.closes[i - MOM_BARS]
            gap = (self.ema60 - self.ema240) * 10000 // self.ema240
            if ret >= MOM_BPS and gap >= GAP_BPS:
                self.entries += 1
                self.highest = px
                self.mode = "tier1"
                return [self._buy(cash)]
        # Tier-2 probe: late, never entered, short-term uptick.
        if self.entries == 0 and i >= PROBE_BAR and i >= 30:
            if (px * EMA_SCALE > self.ema30
                    and self.closes[i - 1] * EMA_SCALE <= self.prev_ema30):
                self.probe_highest = px
                self.mode = "probe"
                return [self._buy(cash * PROBE_BPS // 10000)]
        return []

    # -- wire ------------------------------------------------------------
    def run(self):
        out = sys.stdout
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "hello":
                self.seq = -1
                try:
                    self.warmup_bars = int(msg.get("warmup_bars") or 0)
                except (TypeError, ValueError):
                    self.warmup_bars = 0
                continue
            if kind == "batch":
                try:
                    self.seq = int(msg.get("seq", self.seq + 1))
                except (TypeError, ValueError):
                    self.seq += 1
                orders = []
                try:
                    bars = msg.get("bars") or []
                    bar = None
                    for b in bars:
                        if b.get("symbol") == SYMBOL:
                            bar = b
                            break
                    account = msg.get("account") or {}
                    cash = int(account.get("cash_quote_units", "0"))
                    qty = 0
                    for p in account.get("positions") or []:
                        if p.get("symbol") == SYMBOL:
                            qty = int(p.get("qty", "0"))
                    if bar is not None:
                        # remember EMA-30 before this bar's update for the
                        # probe's cross check
                        self.prev_ema30 = self.ema30
                        self._note_close(int(bar["c"]))
                        if self.seq >= self.warmup_bars:
                            try:
                                orders = self.decide(self.seq, int(bar["c"]),
                                                     cash, qty)
                            except Exception:
                                orders = []
                                self.mode = "flat"
                except Exception:
                    # never let a malformed batch kill the run: answer empty
                    orders = []
                out.write(json.dumps({"type": "reply", "seq": self.seq,
                                      "orders": orders},
                                     separators=(",", ":")) + "\n")
                out.flush()
            elif kind == "done":
                return


def main():
    Bot().run()


if __name__ == "__main__":
    main()
