# BHRAMHA — Vedic Intelligence Strategy Skill

> **BNB Hack: AI Trading Agent Edition — Track 2 (Strategy Skills)**
> Powered by **CoinMarketCap** · LLM narration via **Groq**

**BHRAMHA** (Binance Harmonic Regime Adaptive Market-timing Hybrid Algorithm) is a
crypto **Strategy Skill**: it turns CoinMarketCap market data into a backtestable
trading strategy by scoring seven layers of confluence. Its differentiator is a
layer no other crypto strategy uses — an **astronomically-correct Vedic timing
engine** (nakshatra, hora, tithi) computed from JPL ephemeris data.

This repository is a **strategy spec + backtester**, exactly as Track 2 asks —
not a live execution agent. A separate (optional) execution layer exists for
Track 1 but is not required to run, evaluate, or backtest the strategy.

---

## TL;DR — run it

```bash
cp .env.example .env          # add CMC_API_KEY and GROQ_API_KEY
pip install -r requirements.txt

# A reproducible backtest (the core Track 2 deliverable):
python skill_runner.py backtest --symbol BTCUSDT --interval 4h --days 365

# A live strategy signal with a plain-English rationale:
python skill_runner.py signal   --symbol BTCUSDT --interval 15m

# A visual dashboard — live Vedic Panchang, CMC context, and the backtest proof:
python dashboard.py             # http://127.0.0.1:5000
```

The dashboard renders the current nakshatra/hora/tithi, the live CoinMarketCap
Fear & Greed and regime context, and the validated backtest + Vedic ablation —
a one-glance demo of everything below.

---

## Why this is a "CMC Skill"

CoinMarketCap is the strategy's market-context backbone (`cmc_data.py`):

| CMC endpoint | Used for |
|--------------|----------|
| `cryptocurrency/quotes/latest` | live price, 24h volume, momentum (1h/24h/7d) |
| `v3/fear-and-greed/latest` | **native CMC sentiment** (contrarian layer) |
| `global-metrics/quotes/latest` | BTC dominance + total market cap (regime) |

> CMC's free plan does not expose historical OHLCV (verified: HTTP 403, error
> 1006), so **candles for backtesting come from Binance** while **CMC supplies the
> sentiment + regime overlay**. This split is intentional and documented, not a
> workaround hidden from the judge.

---

## The strategy — seven layers of confluence

A trade only fires when independent layers agree. The score is a deterministic
function of the inputs (see `strategy_core.evaluate`).

| Layer | Module | Contribution |
|-------|--------|--------------|
| 1 · Technical base | `score_engine` | RSI, MACD, EMA stack, Supertrend, volume |
| 2 · Market structure / regime | `market_regime_engine` | trend / range / volatile |
| 3 · **Vedic timing (the edge)** | `vedic_core` | nakshatra, hora, tithi, lunar volatility |
| 4 · Macro / regime | `cmc_data` | BTC dominance, total market cap |
| 5 · Sentiment | `cmc_data` | CMC Fear & Greed (contrarian) |
| 6 · Whale flow *(live only)* | `whale_engine` | large executed trades (optional) |
| 7 · Adaptive thresholds | `StrategyConfig` | tunable min-score / min-R:R |

**Hard blocks** (checked before scoring): blocked nakshatras (Ashwini,
Dhanishta, Bharani, Mrigashira, Punarvasu), Saturn hora, and high lunar
volatility (within ~2 days of a new/full moon).

### The Vedic engine — and how it was fixed

The Vedic layer is the project's original contribution. During this build the
underlying astronomy was corrected and made reproducible:

| Quantity | Before | Now (`vedic_core.py`) |
|----------|--------|------------------------|
| Nakshatra | Moon's *equatorial right ascension*, no ayanamsa | Moon's **sidereal ecliptic longitude** with time-varying **Lahiri ayanamsa** |
| Tithi / moon phase | `day_of_year % 29.53` (calendar fiction) | true **Sun–Moon elongation** |
| Time basis | wall clock only (`now()`) | **any timestamp** → fully backtestable |

Because every Vedic value is now a pure function of time, the *exact* timing
logic the strategy uses can be replayed for any historical candle.

---

## Reproducible backtesting

`backtester.py` is a real walk-forward backtester:

1. Pulls deep historical OHLCV from Binance (paginated).
2. Walks candles forward one bar at a time, calling `strategy_core.evaluate()`
   on data available **up to that bar only** (no lookahead), with the bar's real
   timestamp driving the Vedic layer.
3. Simulates each fired trade against later candles (SL vs. TP2, conservative on
   ambiguous bars).
4. Reports win rate, expectancy (in R), max drawdown, equity curve, and
   **win rate bucketed by nakshatra / hora / session**.

```bash
python backtester.py --symbol ETHUSDT --interval 1h --days 180 --json out.json
```

### Honesty about performance — and what the Vedic edge actually is

Earlier versions quoted dramatic per-nakshatra win rates (e.g. "Revati + London
92.6%"). Those came from a **small live-forward sample and do not reproduce.** We
built proper tooling to test our own hypothesis and report what the data says:

**1. Per-nakshatra prediction is noise.** A 548-trade walk-forward across 6
symbols / 365 days, split train/test, shows a train-derived "avoid these
nakshatras" list *fails* out-of-sample ([`validate_nakshatra.py`](validate_nakshatra.py)).
We do not present the individual bucket win rates as fact.

**2. The Vedic layer IS a measurable selectivity filter.** Ablation
([`ablation_vedic.py`](ablation_vedic.py) — 4h, 365d, min_score 92, pooled across
6 symbols):

| Vedic layer | Trades | Win rate | Total return | Expectancy |
|-------------|--------|----------|--------------|------------|
| **ON**      | 538    | 30.3%    | **+18.2R**   | **+0.034R/trade** |
| OFF (technical only) | 763 | 29.9% | +11.1R     | +0.015R/trade |

Turning the Vedic blocks/overlays on trims ~30% of weaker setups and **roughly
doubles per-trade expectancy.** The value is the *aggregate filtering*, not the
individual buckets — a more defensible and more interesting claim than the
original numbers.

**3. Reference single-symbol backtest** (BNBUSDT 4h, 365d, min_score 92 —
[`backtest_BNBUSDT_4h.json`](backtest_BNBUSDT_4h.json)):
**88 trades · +9.28R · +0.106R/trade · max drawdown −9.0R.**

Caveat: this is one ~1-year window; the effect is modest and may be
regime-dependent. Every number here is reproducible with the scripts in this
repo — verify, don't trust.

### Reproduce / interrogate every claim

| Script | What it proves |
|--------|----------------|
| [`backtester.py`](backtester.py) | Walk-forward backtest + per-nakshatra / session breakdowns |
| [`calibrate.py`](calibrate.py) | Threshold sweep + pooled, Wilson-bounded nakshatra stats |
| [`validate_nakshatra.py`](validate_nakshatra.py) | Out-of-sample (train/test) honesty check |
| [`ablation_vedic.py`](ablation_vedic.py) | Isolates the Vedic layer's contribution (on vs off) |

---

## Architecture

```
CoinMarketCap context  ─┐
Binance OHLCV candles  ─┼─►  strategy_core.evaluate()  ─►  Groq LLM narration
vedic_core timing      ─┘         (deterministic)            (explanation only)
```

| File | Role |
|------|------|
| `vedic_core.py` | Correct, time-parameterized Vedic engine (single source of truth) |
| `cmc_data.py` | CoinMarketCap data layer |
| `strategy_core.py` | Pure `evaluate()` — no I/O, no side effects, backtestable |
| `backtester.py` | Walk-forward backtester + stats |
| `groq_client.py`, `skill_runner.py` | LLM narration + skill CLI |
| `dashboard.py` | Flask visual dashboard — live Panchang, CMC context, backtest proof |
| `SKILL.md` | LLM Skill manifest |
| The original `*_engine.py` modules | the live execution path (Track 1, optional) |

---

## Security

- All secrets load from a **gitignored `.env`** via `config.py` — nothing is
  hardcoded. Copy `.env.example` and add your own keys.
- If you forked from an earlier commit, **rotate any keys** that were ever
  committed (Binance, Telegram) before publishing.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: CMC_API_KEY, GROQ_API_KEY (Binance/Telegram only for live trading)
```

Requires Python 3.10+. First run downloads the `de421.bsp` JPL ephemeris (~17 MB)
used by `skyfield` for the Vedic computations.
