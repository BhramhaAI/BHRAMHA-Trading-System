---
name: bhramha-vedic-strategy
description: >
  A CoinMarketCap-powered crypto trading Strategy Skill that fuses classical
  technical analysis with Vedic astronomical timing (nakshatra, hora, tithi) and
  CMC Fear & Greed sentiment into a single, backtestable confluence score. Use
  this skill to (1) generate a directional strategy signal for a crypto symbol
  with a plain-English rationale, or (2) run a reproducible historical backtest
  that reports win rate, expectancy, and win-rate-by-nakshatra.
metadata:
  track: "BNB Hack Track 2 — Strategy Skills"
  data_source: "CoinMarketCap (quotes, Fear & Greed, global metrics) + Binance OHLCV"
  llm: "Groq (llama-3.3-70b-versatile)"
---

# BHRAMHA — Vedic Intelligence Strategy Skill

BHRAMHA turns market data into a trading strategy by scoring **seven layers of
confluence** and only acting when they align. Its differentiator is **Layer 3**:
an astronomically-correct Vedic timing engine that no other crypto strategy uses
— which a year-long, 6-symbol backtest shows works as a *selectivity filter* that
roughly doubles per-trade expectancy (see Honesty note below for the exact
numbers, and the caveat about what the Vedic layer does and does not do).

## What this skill does

Given a crypto symbol, the skill computes a deterministic confluence score and
emits a strategy spec: direction, entry, stop-loss, tiered take-profits, R:R,
position-size multiplier, and the reasons. An LLM then narrates the rationale in
plain English. The same deterministic core can be replayed over historical
candles to produce an honest, reproducible backtest.

## When to use it

- "What's the BHRAMHA signal for BTC right now?" → `signal`
- "Backtest this strategy on ETH over the last year." → `backtest`
- "Show me the current Vedic + CMC market context." → `context`

## How to invoke

```bash
# 1. One-time setup
cp .env.example .env          # then fill in CMC_API_KEY and GROQ_API_KEY
pip install -r requirements.txt

# 2. Generate a strategy signal + LLM rationale
python skill_runner.py signal   --symbol BTCUSDT --interval 15m

# 3. Run a reproducible backtest (the Track 2 deliverable)
python skill_runner.py backtest --symbol BTCUSDT --interval 4h --days 365

# 4. Inspect the raw CMC + Vedic context
python skill_runner.py context  --symbol ETHUSDT
```

## The seven layers (scoring model)

| Layer | Source module | What it contributes |
|-------|---------------|---------------------|
| 1 Technical base | `score_engine.score_candle` | RSI, MACD, EMA stack, Supertrend, volume |
| 2 Market structure / regime | `market_regime_engine` | trend / range / volatile classification |
| 3 **Vedic timing (the edge)** | `vedic_core` | nakshatra, hora, tithi, lunar volatility |
| 4 Macro / regime | `cmc_data.get_global_metrics` | BTC dominance, total market cap |
| 5 Sentiment | `cmc_data.get_fear_greed` | CMC Fear & Greed (contrarian) |
| 6 Whale flow (live only) | `whale_engine` | large executed trades (optional overlay) |
| 7 Adaptive thresholds | `StrategyConfig` | tunable min-score / min-R:R gates |

Hard blocks (enforced before scoring): blocked nakshatras (Ashwini, Dhanishta,
Bharani, Mrigashira, Punarvasu), Saturn hora, and high lunar volatility
(within ~2 days of a new/full moon).

## Files

- `vedic_core.py` — astronomically-correct, time-parameterized Vedic engine.
- `cmc_data.py` — CoinMarketCap data layer (quotes, Fear & Greed, global metrics).
- `strategy_core.py` — the pure, side-effect-free `evaluate()` strategy function.
- `backtester.py` — walk-forward historical backtester with nakshatra buckets.
- `groq_client.py` / `skill_runner.py` — LLM narration and the skill CLI.

## Honesty note (what the edge actually is)

The Vedic layer is fully reproducible historically (the astronomy is a pure
function of time). But we are deliberately precise about what it buys you:

- **The per-nakshatra win rates from early live logs do NOT reproduce.** A
  548-trade walk-forward across 6 symbols / 365 days shows the individual
  nakshatra win rates are statistical noise (`validate_nakshatra.py`: a
  train-derived avoid-list fails out-of-sample). We do not present them as fact.
- **What IS data-supported** is the Vedic block/overlay set acting as a
  *confluence selectivity filter*. Ablation (`ablation_vedic.py`, 4h, 365d,
  min_score 92, pooled): turning the Vedic layer **on** vs **off** improved
  per-trade expectancy from **+0.015R to +0.034R** while trimming ~30% of
  trades (763 → 538). The value is the aggregate filtering, not the buckets.
- Reference single-symbol backtest (BNBUSDT 4h, 365d, min_score 92):
  **88 trades, +9.28R, +0.106R/trade, max drawdown −9.0R.**

The backtester, calibrator, validator and ablation scripts are all included so a
judge can reproduce every number rather than trust it.
