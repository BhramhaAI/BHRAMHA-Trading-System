# -*- coding: utf-8 -*-
"""
backtester.py — BHRAMHA's real walk-forward backtester.
=======================================================

BNB Hack Track 2 requires a *backtestable strategy spec*. The legacy
`backtest_engine.py` only read a CSV of already-closed live trades and divided
wins by total — it could not reproduce anything. This module is the real thing:

  1. Pulls deep historical OHLCV from Binance (paginated klines).
  2. Walks the candles forward one bar at a time, calling the pure
     `strategy_core.evaluate()` on the data available *up to that bar only*
     (no lookahead) — with the bar's real timestamp, so the Vedic layer is
     evaluated correctly for that historical moment.
  3. When a signal fires, it simulates the trade against subsequent candles:
     stop-loss vs. take-profit (TP2), bar-by-bar, conservative on ambiguous bars.
  4. Aggregates honest statistics, including the breakdowns that make BHRAMHA
     distinctive: win rate by nakshatra, by hora, and by trading session.

Because the Vedic engine (`vedic_core`) is fully time-parameterized, the exact
same timing logic the live bot uses is reproduced for every historical bar.

CMC note: CoinMarketCap's free plan has no historical Fear & Greed, so the
sentiment overlay is disabled during backtests (the strategy still runs — it
simply loses that one overlay for historical bars). On the live path CMC is
fully active. See README for details.

Usage:
    python backtester.py --symbol BTCUSDT --interval 1h --days 180
    python backtester.py --symbol ETHUSDT --interval 15m --days 60 --min-score 80
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from indicators import add_indicators
from strategy_core import evaluate, StrategyConfig
from vedic_core import get_vedic_context

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
_MAX_LIMIT = 1000


# ──────────────────────────────────────────────────────────────────────────────
# Historical data (paginated)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_history(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch `days` of OHLCV for `symbol`/`interval`, paginating Binance klines."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    rows: list = []
    cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cursor, "endTime": end_ms, "limit": _MAX_LIMIT}
        r = requests.get(BINANCE_KLINES, params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        last_open = batch[-1][0]
        nxt = last_open + 1
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < _MAX_LIMIT:
            break
        time.sleep(0.25)  # be polite to the API

    if not rows:
        raise RuntimeError(f"No history returned for {symbol} {interval}")

    df = pd.DataFrame(rows, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "num_trades", "taker_base", "taker_quote", "ignore"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="time").set_index("time").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


# ──────────────────────────────────────────────────────────────────────────────
# Trade simulation
# ──────────────────────────────────────────────────────────────────────────────
def _simulate_trade(df: pd.DataFrame, entry_idx: int, signal: dict,
                    max_hold: int) -> dict:
    """
    Simulate a single trade from `entry_idx` forward.

    Entry at the close of the decision bar. Each later bar is checked for SL/TP2.
    If a bar's range straddles both, we assume the stop is hit first (conservative,
    avoids over-stating performance). Returns the closed-trade record.
    """
    direction = signal["direction"]
    entry = signal["entry"]
    sl = signal["stop_loss"]
    tp = signal["take_profit"].get("tp2") or signal["take_profit"].get("tp1")
    risk = abs(entry - sl)

    n = len(df)
    for j in range(entry_idx + 1, min(entry_idx + 1 + max_hold, n)):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])

        if direction == "LONG":
            hit_sl = lo <= sl
            hit_tp = hi >= tp
        else:
            hit_sl = hi >= sl
            hit_tp = lo <= tp

        if hit_sl and hit_tp:
            outcome, exit_price = "LOSS", sl       # conservative
        elif hit_sl:
            outcome, exit_price = "LOSS", sl
        elif hit_tp:
            outcome, exit_price = "WIN", tp
        else:
            continue

        pnl_r = (exit_price - entry) / risk if direction == "LONG" else (entry - exit_price) / risk
        return {"outcome": outcome, "exit_price": exit_price,
                "exit_time": df.index[j].isoformat(), "bars_held": j - entry_idx,
                "pnl_r": round(pnl_r, 3)}

    # Timed out — close at last available bar.
    last = float(df["close"].iloc[min(entry_idx + max_hold, n - 1)])
    pnl_r = (last - entry) / risk if direction == "LONG" else (entry - last) / risk
    return {"outcome": "WIN" if pnl_r > 0 else "LOSS", "exit_price": last,
            "exit_time": df.index[min(entry_idx + max_hold, n - 1)].isoformat(),
            "bars_held": min(max_hold, n - 1 - entry_idx), "pnl_r": round(pnl_r, 3)}


# ──────────────────────────────────────────────────────────────────────────────
# Session helper (IST, matching BHRAMHA's research)
# ──────────────────────────────────────────────────────────────────────────────
def _session(ts: datetime) -> str:
    ist = ts.astimezone(timezone(timedelta(hours=5, minutes=30)))
    h = ist.hour
    if 14 <= h < 20:
        return "LONDON"
    if 6 <= h < 10:
        return "ASIA_OPEN"
    if 0 <= h < 4:
        return "NY_LATE"
    return "OTHER"


# ──────────────────────────────────────────────────────────────────────────────
# Backtest driver
# ──────────────────────────────────────────────────────────────────────────────
def run_backtest(symbol: str, interval: str, days: int,
                 config: StrategyConfig | None = None,
                 warmup: int = 210, max_hold: int = 96,
                 df: pd.DataFrame | None = None) -> dict:
    """
    Walk-forward backtest. Returns a stats dict with overall metrics, the equity
    curve (in R), and Vedic/session breakdowns.
    """
    cfg = config or StrategyConfig()
    if df is None:
        df = fetch_history(symbol, interval, days)
    df = add_indicators(df)

    trades: list[dict] = []
    i = warmup
    n = len(df)
    while i < n - 1:
        window = df.iloc[: i + 1]
        ts = df.index[i].to_pydatetime()
        sig = evaluate(window, timestamp=ts, cmc_context=None, config=cfg)
        if sig["fired"]:
            result = _simulate_trade(df, i, sig, max_hold)
            trades.append({
                "entry_time": ts.isoformat(),
                "direction": sig["direction"],
                "score": sig["score"],
                "rr": sig["rr"],
                "nakshatra": sig["vedic"].get("nakshatra"),
                "hora": sig["vedic"].get("hora"),
                "session": _session(ts),
                **result,
            })
            # No overlapping trades: skip ahead past the hold period.
            i += result["bars_held"] + 1
        else:
            i += 1

    return _aggregate(symbol, interval, days, trades)


def _bucket_winrate(trades: list[dict], key: str) -> dict:
    buckets: dict[str, list[int]] = defaultdict(list)
    for t in trades:
        buckets[t.get(key) or "?"].append(1 if t["outcome"] == "WIN" else 0)
    out = {}
    for k, wins in buckets.items():
        out[k] = {"trades": len(wins), "win_rate": round(100 * sum(wins) / len(wins), 1)}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["win_rate"], reverse=True))


def _aggregate(symbol: str, interval: str, days: int, trades: list[dict]) -> dict:
    total = len(trades)
    if total == 0:
        return {"symbol": symbol, "interval": interval, "days": days,
                "total_trades": 0, "note": "no signals fired in this window"}

    wins = [t for t in trades if t["outcome"] == "WIN"]
    total_r = sum(t["pnl_r"] for t in trades)

    # Equity curve in R and max drawdown.
    equity, peak, max_dd = 0.0, 0.0, 0.0
    curve = []
    for t in trades:
        equity += t["pnl_r"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        curve.append(round(equity, 3))

    return {
        "symbol": symbol,
        "interval": interval,
        "days": days,
        "total_trades": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate": round(100 * len(wins) / total, 2),
        "total_return_r": round(total_r, 2),
        "expectancy_r": round(total_r / total, 3),
        "avg_winner_r": round(sum(t["pnl_r"] for t in wins) / len(wins), 3) if wins else 0,
        "max_drawdown_r": round(max_dd, 2),
        "by_nakshatra": _bucket_winrate(trades, "nakshatra"),
        "by_hora": _bucket_winrate(trades, "hora"),
        "by_session": _bucket_winrate(trades, "session"),
        "equity_curve_r": curve,
        "trades": trades,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def _print_report(stats: dict) -> None:
    print("\n" + "=" * 64)
    print(f" BHRAMHA BACKTEST - {stats['symbol']} {stats['interval']} "
          f"({stats['days']}d)")
    print("=" * 64)
    if stats["total_trades"] == 0:
        print(" ", stats.get("note"))
        return
    print(f"  Trades        : {stats['total_trades']}  "
          f"(W {stats['wins']} / L {stats['losses']})")
    print(f"  Win rate      : {stats['win_rate']}%")
    print(f"  Total return  : {stats['total_return_r']}R")
    print(f"  Expectancy    : {stats['expectancy_r']}R per trade")
    print(f"  Max drawdown  : {stats['max_drawdown_r']}R")
    print(f"\n  Win rate by Nakshatra (the edge):")
    for nak, d in stats["by_nakshatra"].items():
        print(f"    {nak:<16} {d['win_rate']:>5}%  ({d['trades']} trades)")
    print(f"\n  Win rate by Session:")
    for s, d in stats["by_session"].items():
        print(f"    {s:<16} {d['win_rate']:>5}%  ({d['trades']} trades)")
    print("=" * 64 + "\n")


def main():
    ap = argparse.ArgumentParser(description="BHRAMHA walk-forward backtester")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--min-score", type=float, default=75.0)
    ap.add_argument("--min-rr", type=float, default=1.8)
    ap.add_argument("--max-hold", type=int, default=96, help="max bars to hold a trade")
    ap.add_argument("--no-lunar-block", action="store_true",
                    help="disable the high-lunar-volatility hard block")
    ap.add_argument("--json", metavar="PATH", help="write full stats JSON to PATH")
    args = ap.parse_args()

    cfg = StrategyConfig(min_score=args.min_score, min_rr=args.min_rr,
                         block_high_lunar=not args.no_lunar_block)
    print(f"Fetching {args.days}d of {args.symbol} {args.interval} from Binance...")
    stats = run_backtest(args.symbol, args.interval, args.days,
                         config=cfg, max_hold=args.max_hold)
    _print_report(stats)

    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(stats, fh, indent=2, default=str)
        print(f"Full stats written to {args.json}")


if __name__ == "__main__":
    main()
