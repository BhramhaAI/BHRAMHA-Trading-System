# -*- coding: utf-8 -*-
"""
skill_runner.py — BHRAMHA CMC Skill entry point (BNB Hack Track 2).
===================================================================

This is the invokable surface of the BHRAMHA "Strategy Skill". It composes the
pieces built for Track 2 into the one thing the track asks for: a CMC-powered,
backtestable strategy that emits a natural-language strategy spec.

Pipeline:
    CoinMarketCap context  ─┐
    Binance OHLCV candles  ─┼─►  strategy_core.evaluate()  ─►  Groq LLM narration
    vedic_core timing      ─┘         (deterministic)            (explanation)

Commands:
    python skill_runner.py signal   --symbol BTCUSDT --interval 15m
    python skill_runner.py backtest --symbol BTCUSDT --interval 4h --days 365
    python skill_runner.py context  --symbol BTCUSDT      # raw CMC + Vedic snapshot

The `signal` command produces the strategy spec (structured signal + LLM
rationale). The `backtest` command produces the reproducible performance proof.
"""

from __future__ import annotations

import argparse
import json

from strategy_core import evaluate, StrategyConfig
from vedic_core import get_vedic_context
from groq_client import chat

_SYSTEM = (
    "You are BHRAMHA, a crypto strategy author. You translate a deterministic "
    "trading signal — built from technical indicators, Vedic astronomical timing "
    "(nakshatra, hora, tithi), and CoinMarketCap sentiment — into a concise, "
    "honest strategy rationale. Never invent numbers; only explain the data given. "
    "Be specific about WHY the confluence does or does not justify a trade."
)


def _narrate(signal: dict, cmc_ctx: dict) -> str:
    prompt = (
        "Here is the deterministic signal output (JSON):\n"
        f"{json.dumps(signal, indent=2, default=str)}\n\n"
        "Here is the CoinMarketCap market context (JSON):\n"
        f"{json.dumps(cmc_ctx, indent=2, default=str)}\n\n"
        "Write a 4-6 sentence strategy rationale: the direction (or why no trade), "
        "the key technical + Vedic + sentiment factors that drove the score, the "
        "entry/stop/target plan if fired, and the main risk. Plain English."
    )
    return chat(prompt, system=_SYSTEM)


def cmd_signal(args):
    from binance_data import get_data
    from cmc_data import get_market_context

    df = get_data(args.symbol, args.interval, limit=250)
    if df is None:
        print("Could not fetch candles."); return
    cmc_ctx = get_market_context(args.symbol)
    cfg = StrategyConfig(min_score=args.min_score, min_rr=args.min_rr,
                         block_high_lunar=not args.no_lunar_block)
    sig = evaluate(df, cmc_context=cmc_ctx, config=cfg)

    print(json.dumps(sig, indent=2, default=str))
    print("\n-- STRATEGY RATIONALE (LLM) " + "-" * 30)
    print(_narrate(sig, cmc_ctx))


def cmd_backtest(args):
    from backtester import run_backtest, _print_report

    cfg = StrategyConfig(min_score=args.min_score, min_rr=args.min_rr,
                         block_high_lunar=not args.no_lunar_block)
    stats = run_backtest(args.symbol, args.interval, args.days, config=cfg)
    _print_report(stats)

    if not args.no_llm and stats.get("total_trades"):
        summary = {k: stats[k] for k in (
            "symbol", "interval", "days", "total_trades", "win_rate",
            "total_return_r", "expectancy_r", "max_drawdown_r",
            "by_nakshatra", "by_session")}
        print("-- BACKTEST INTERPRETATION (LLM) " + "-" * 25)
        print(chat(
            "Interpret this BHRAMHA backtest honestly — strengths, weaknesses, "
            "and whether the Vedic nakshatra buckets show real edge:\n"
            f"{json.dumps(summary, indent=2, default=str)}",
            system=_SYSTEM, max_tokens=500))


def cmd_context(args):
    from cmc_data import get_market_context
    out = {
        "vedic": get_vedic_context(),
        "cmc": get_market_context(args.symbol),
    }
    print(json.dumps(out, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser(description="BHRAMHA CMC Strategy Skill")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--symbol", default="BTCUSDT")
    common.add_argument("--min-score", type=float, default=78.0)
    common.add_argument("--min-rr", type=float, default=1.8)
    common.add_argument("--no-lunar-block", action="store_true")

    s = sub.add_parser("signal", parents=[common], help="emit a strategy signal + rationale")
    s.add_argument("--interval", default="15m")
    s.set_defaults(func=cmd_signal)

    b = sub.add_parser("backtest", parents=[common], help="reproducible backtest")
    b.add_argument("--interval", default="4h")
    b.add_argument("--days", type=int, default=365)
    b.add_argument("--no-llm", action="store_true")
    b.set_defaults(func=cmd_backtest)

    c = sub.add_parser("context", parents=[common], help="raw CMC + Vedic snapshot")
    c.set_defaults(func=cmd_context)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
