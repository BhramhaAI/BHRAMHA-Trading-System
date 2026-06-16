# -*- coding: utf-8 -*-
"""
calibrate.py — data-driven calibration for the BHRAMHA strategy.
================================================================

The live-log win rates (e.g. "Revati 92.6%") came from tiny samples and do not
reproduce. This harness builds an honest, larger sample by pooling walk-forward
backtests across several liquid symbols and timeframes, then answers two
questions:

  1. Which (timeframe, min_score) configuration has the best out-of-sample
     expectancy?
  2. Across the *pooled* trades, which nakshatras actually show edge — i.e. a
     data-driven replacement for the hand-set win-rate buckets?

History for each (symbol, interval) is fetched once and reused across every
threshold, so the API cost stays small.

    python calibrate.py                 # default sweep
    python calibrate.py --days 365 --min-trades 15
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from strategy_core import StrategyConfig
from backtester import fetch_history, run_backtest

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]
DEFAULT_TFS = ["1h", "4h"]
DEFAULT_SCORES = [78.0, 82.0, 86.0, 90.0]


def _wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score lower bound on win rate — penalizes small samples so a 2/2
    nakshatra doesn't masquerade as a 100% edge."""
    if n == 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * ((phat * (1 - phat) + z * z / (4 * n)) / n) ** 0.5
    return (centre - margin) / denom


def run(symbols, timeframes, days, scores, min_trades, max_hold):
    # 1) Fetch each (symbol, tf) once.
    cache: dict[tuple, object] = {}
    for sym in symbols:
        for tf in timeframes:
            try:
                print(f"[fetch] {sym} {tf} ({days}d)...")
                cache[(sym, tf)] = fetch_history(sym, tf, days)
            except Exception as exc:
                print(f"[fetch] failed {sym} {tf}: {exc}")

    # 2) Sweep configs; pool trades per (tf, score).
    config_results = []
    pooled_trades_by_cfg: dict[tuple, list] = defaultdict(list)

    for tf in timeframes:
        for score in scores:
            cfg = StrategyConfig(min_score=score)
            agg_trades, agg_r = [], 0.0
            for sym in symbols:
                df = cache.get((sym, tf))
                if df is None:
                    continue
                stats = run_backtest(sym, tf, days, config=cfg,
                                     max_hold=max_hold, df=df.copy())
                if stats.get("total_trades"):
                    agg_trades.extend(stats["trades"])
                    agg_r += stats["total_return_r"]
            n = len(agg_trades)
            if n == 0:
                continue
            wins = sum(1 for t in agg_trades if t["outcome"] == "WIN")
            config_results.append({
                "timeframe": tf, "min_score": score, "trades": n,
                "win_rate": round(100 * wins / n, 1),
                "total_r": round(agg_r, 1),
                "expectancy_r": round(agg_r / n, 3),
            })
            pooled_trades_by_cfg[(tf, score)] = agg_trades

    config_results.sort(key=lambda r: r["expectancy_r"], reverse=True)

    # 3) Data-driven nakshatra edge, pooled across ALL configs (largest sample).
    all_trades = [t for trades in pooled_trades_by_cfg.values() for t in trades]
    nak_stats: dict[str, list[int]] = defaultdict(list)
    for t in all_trades:
        nak_stats[t.get("nakshatra") or "?"].append(1 if t["outcome"] == "WIN" else 0)

    nak_table = []
    for nak, res in nak_stats.items():
        n = len(res)
        wins = sum(res)
        nak_table.append({
            "nakshatra": nak, "trades": n,
            "win_rate": round(100 * wins / n, 1),
            "wilson_lb": round(100 * _wilson_lower_bound(wins, n), 1),
        })
    nak_table.sort(key=lambda r: r["wilson_lb"], reverse=True)

    return config_results, nak_table, min_trades


def report(config_results, nak_table, min_trades):
    print("\n" + "=" * 70)
    print(" CONFIG SWEEP  (pooled across symbols, ranked by expectancy)")
    print("=" * 70)
    print(f" {'tf':<5}{'min_score':>10}{'trades':>9}{'win%':>8}{'totalR':>9}{'exp_R':>8}")
    for r in config_results:
        print(f" {r['timeframe']:<5}{r['min_score']:>10}{r['trades']:>9}"
              f"{r['win_rate']:>8}{r['total_r']:>9}{r['expectancy_r']:>8}")

    print("\n" + "=" * 70)
    print(f" NAKSHATRA EDGE  (pooled; Wilson lower bound, >= {min_trades} trades)")
    print(" ranked by statistically-defensible win rate, not raw win rate")
    print("=" * 70)
    print(f" {'nakshatra':<18}{'trades':>8}{'win%':>8}{'wilson_lb%':>12}  tier")
    strong, weak = [], []
    for r in nak_table:
        if r["trades"] < min_trades:
            continue
        if r["wilson_lb"] >= 45:
            tier = "STRONG"; strong.append(r["nakshatra"])
        elif r["wilson_lb"] <= 20:
            tier = "AVOID"; weak.append(r["nakshatra"])
        else:
            tier = "neutral"
        print(f" {r['nakshatra']:<18}{r['trades']:>8}{r['win_rate']:>8}"
              f"{r['wilson_lb']:>12}  {tier}")

    if config_results:
        best = config_results[0]
        print("\n" + "-" * 70)
        print(f" RECOMMENDED CONFIG: interval={best['timeframe']} "
              f"min_score={best['min_score']} "
              f"(exp {best['expectancy_r']}R/trade over {best['trades']} trades)")
    print(f" Data-supported STRONG nakshatras: {', '.join(strong) or 'none'}")
    print(f" Data-supported AVOID nakshatras : {', '.join(weak) or 'none'}")
    print("-" * 70 + "\n")


def main():
    ap = argparse.ArgumentParser(description="BHRAMHA strategy calibration")
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--timeframes", nargs="+", default=DEFAULT_TFS)
    ap.add_argument("--scores", nargs="+", type=float, default=DEFAULT_SCORES)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--min-trades", type=int, default=12)
    ap.add_argument("--max-hold", type=int, default=96)
    args = ap.parse_args()

    cfgs, naks, mt = run(args.symbols, args.timeframes, args.days,
                         args.scores, args.min_trades, args.max_hold)
    report(cfgs, naks, mt)


if __name__ == "__main__":
    main()
