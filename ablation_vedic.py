# -*- coding: utf-8 -*-
"""
ablation_vedic.py — does the Vedic layer actually add value?
============================================================

Runs the identical strategy across several symbols with the Vedic layer fully ON
vs. fully OFF, pooling the trades. This is the honest test of whether the
headline feature contributes, rather than the technical layer carrying it alone.

  FULL    : Vedic hard-blocks + nakshatra/hora/tithi overlays active.
  NO_VEDIC: blocks disabled AND overlays zeroed (technical-only baseline).
"""

from __future__ import annotations

import copy
from strategy_core import StrategyConfig
import strategy_core
from backtester import fetch_history, run_backtest

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]
INTERVAL, DAYS, MIN_SCORE = "4h", 365, 92.0


def _pool(cache, vedic_on: bool):
    # Toggle overlays by temporarily zeroing the adjustment tables.
    saved = (copy.deepcopy(strategy_core._NAK_ACTION_ADJ),
             copy.deepcopy(strategy_core._HORA_ADJ))
    if not vedic_on:
        strategy_core._NAK_ACTION_ADJ = {k: (0.0, 0.0) for k in saved[0]}
        strategy_core._HORA_ADJ = {k: (0.0, 0.0) for k in saved[1]}
    cfg = StrategyConfig(
        min_score=MIN_SCORE,
        enforce_vedic_blocks=vedic_on,
        block_high_lunar=vedic_on,
        block_saturn_hora=vedic_on,
    )
    trades = []
    for sym in SYMBOLS:
        stats = run_backtest(sym, INTERVAL, DAYS, config=cfg, df=cache[sym].copy())
        trades.extend(stats.get("trades", []))
    strategy_core._NAK_ACTION_ADJ, strategy_core._HORA_ADJ = saved  # restore
    return trades


def _summary(name, trades):
    n = len(trades)
    if n == 0:
        return f"  {name:<10}: no trades"
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    total_r = sum(t["pnl_r"] for t in trades)
    return (f"  {name:<10}: {n:>4} trades | win {100*wins/n:>5.1f}% | "
            f"total {total_r:>6.1f}R | exp {total_r/n:>+.3f}R/trade")


def main():
    cache = {}
    for sym in SYMBOLS:
        print(f"[fetch] {sym} {INTERVAL} ({DAYS}d)...")
        cache[sym] = fetch_history(sym, INTERVAL, DAYS)

    full = _pool(cache, vedic_on=True)
    none = _pool(cache, vedic_on=False)

    print("\n" + "=" * 64)
    print(f" VEDIC ABLATION  ({INTERVAL}, {DAYS}d, min_score {MIN_SCORE}, pooled)")
    print("=" * 64)
    print(_summary("FULL", full))
    print(_summary("NO_VEDIC", none))
    fe = sum(t["pnl_r"] for t in full) / max(1, len(full))
    ne = sum(t["pnl_r"] for t in none) / max(1, len(none))
    print("-" * 64)
    print(f"  Vedic contribution: {fe-ne:+.3f}R/trade expectancy")
    if fe - ne > 0.01:
        print("  -> Vedic layer ADDS value (data-supported headline).")
    elif fe - ne < -0.01:
        print("  -> Vedic layer SUBTRACTS value (headline is decorative, not edge).")
    else:
        print("  -> Vedic layer is ~neutral (original framing, no measurable edge).")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
