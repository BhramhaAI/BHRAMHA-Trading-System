# -*- coding: utf-8 -*-
"""
validate_nakshatra.py — does the nakshatra signal survive out-of-sample?
========================================================================

Pooled in-sample win rates always show *some* spread between nakshatras — that
is expected even from pure noise. The only honest test is out-of-sample:

  1. Pool trades across symbols (one config).
  2. Split chronologically: TRAIN = first 60% by entry time, TEST = last 40%.
  3. On TRAIN only, rank nakshatras and label the worst third as "AVOID".
  4. On TEST, compare expectancy of avoiding those nakshatras vs. trading all.

If the TRAIN-derived AVOID list also improves the TEST set, the timing signal
is (weakly) real and worth keeping as a filter. If it doesn't, the nakshatra
layer is noise and should be demoted to an honest, low-weight overlay.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from strategy_core import StrategyConfig
from backtester import fetch_history, run_backtest

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]


def _expectancy(trades):
    if not trades:
        return 0.0, 0
    return sum(t["pnl_r"] for t in trades) / len(trades), len(trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="4h")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--min-score", type=float, default=80.0)
    ap.add_argument("--min-train", type=int, default=8,
                    help="min TRAIN trades for a nakshatra to be judged")
    args = ap.parse_args()

    cfg = StrategyConfig(min_score=args.min_score)
    all_trades = []
    for sym in SYMBOLS:
        print(f"[fetch] {sym} {args.interval} ({args.days}d)...")
        df = fetch_history(sym, args.interval, args.days)
        stats = run_backtest(sym, args.interval, args.days, config=cfg, df=df)
        all_trades.extend(stats.get("trades", []))

    all_trades.sort(key=lambda t: t["entry_time"])
    if len(all_trades) < 40:
        print(f"Only {len(all_trades)} trades — too few to validate.")
        return

    split = int(len(all_trades) * 0.6)
    train, test = all_trades[:split], all_trades[split:]
    print(f"\nPooled trades: {len(all_trades)}  (train {len(train)} / test {len(test)})")
    print(f"Train window : {train[0]['entry_time'][:10]} -> {train[-1]['entry_time'][:10]}")
    print(f"Test window  : {test[0]['entry_time'][:10]} -> {test[-1]['entry_time'][:10]}")

    # Rank nakshatras on TRAIN only.
    tr = defaultdict(list)
    for t in train:
        tr[t.get("nakshatra") or "?"].append(1 if t["outcome"] == "WIN" else 0)
    ranked = sorted(
        [(k, sum(v) / len(v), len(v)) for k, v in tr.items() if len(v) >= args.min_train],
        key=lambda x: x[1])
    if not ranked:
        print("Not enough per-nakshatra train data.")
        return
    cutoff = max(1, len(ranked) // 3)
    avoid = {k for k, _, _ in ranked[:cutoff]}

    print(f"\nTRAIN-derived AVOID list (worst third): {', '.join(sorted(avoid))}")

    # Apply to TEST.
    base_exp, base_n = _expectancy(test)
    kept = [t for t in test if (t.get("nakshatra") or "?") not in avoid]
    filt_exp, filt_n = _expectancy(kept)
    removed = base_n - filt_n

    print("\n" + "=" * 60)
    print(" OUT-OF-SAMPLE TEST")
    print("=" * 60)
    print(f"  Trade all nakshatras : {base_exp:+.3f}R/trade  ({base_n} trades)")
    print(f"  Avoid TRAIN-bad ones : {filt_exp:+.3f}R/trade  ({filt_n} trades, "
          f"{removed} filtered)")
    delta = filt_exp - base_exp
    print(f"  Improvement          : {delta:+.3f}R/trade")
    print("=" * 60)
    if delta > 0.02:
        print("  VERDICT: nakshatra AVOID filter HOLDS out-of-sample — keep it.")
    elif delta < -0.02:
        print("  VERDICT: filter HURTS out-of-sample — the signal is noise/overfit.")
    else:
        print("  VERDICT: no meaningful out-of-sample effect — treat as weak overlay.")
    print()


if __name__ == "__main__":
    main()
