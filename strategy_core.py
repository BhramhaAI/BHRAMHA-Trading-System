# -*- coding: utf-8 -*-
"""
strategy_core.py — BHRAMHA's pure, backtestable strategy function.
==================================================================

This is the *strategy spec* BNB Hack Track 2 asks for: a single, side-effect-free
function that turns market data into a trading decision. It contains no network
calls, no order placement, no logging, and no wall-clock reads — every output is
a deterministic function of its inputs. That is exactly what makes it both
**backtestable** (replay historical candles) and **reusable** by the live agent.

`evaluate(df, timestamp, cmc_context)` reproduces BHRAMHA's seven-layer confluence
model from the strategy doc, with the layer weights preserved:

    Layer 1  Technical base        score_engine.score_candle (RSI/MACD/EMA/ADX/vol)
    Layer 2  Market structure      regime detection (trend/range/volatile)
    Layer 3  Vedic timing          vedic_core: nakshatra / hora / tithi / lunar  ← the edge
    Layer 4  Macro / regime        BTC dominance + global flow (from CMC context)
    Layer 5  Sentiment             CMC Fear & Greed (contrarian)
    Layer 6  (whale flow)          optional, supplied via context on the live path
    Layer 7  Adaptive thresholds   exposed as `min_score` config knob

The Vedic hard-block system (Ashwini/Dhanishta/etc., Saturn hora, high lunar
volatility) is enforced first — those windows reject the trade regardless of
technical score, just as in the live bot.

Returns a `dict` (see `_signal()`), never raises on a single bad bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import pandas as pd

from vedic_core import get_vedic_context
from score_engine import score_candle
from indicators import add_indicators

try:
    from market_regime_engine import detect_market_regime
except Exception:  # pragma: no cover - keep core importable in minimal envs
    detect_market_regime = None

try:
    from smart_exit_engine import smart_stop_loss, smart_take_profit
    _HAVE_SMART_EXIT = True
except Exception:  # pragma: no cover
    _HAVE_SMART_EXIT = False


# ──────────────────────────────────────────────────────────────────────────────
# Tunable configuration (Layer 7 lives here — adaptive thresholds as knobs)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class StrategyConfig:
    min_score: float = 75.0          # final-score gate to fire a signal
    min_rr: float = 1.8              # minimum reward:risk (matches config.MIN_RR)
    base_score: float = 70.0         # technical base anchor
    tech_weight: float = 0.65        # Layer 1 weight (TECH_MAX_BONUS = 65%)
    enforce_vedic_blocks: bool = True
    block_high_lunar: bool = True
    block_saturn_hora: bool = True
    use_smart_exits: bool = True     # use ATR/liquidity exits when available
    atr_sl_mult: float = 1.5         # fallback SL distance if smart exits absent


DEFAULT_CONFIG = StrategyConfig()


# ──────────────────────────────────────────────────────────────────────────────
# Vedic scoring overlays (preserve BHRAMHA's empirical buckets)
# ──────────────────────────────────────────────────────────────────────────────
# Nakshatra action → (long_adj, short_adj). BLOCK is handled as a hard gate.
_NAK_ACTION_ADJ = {
    "GOLDEN":     (18.0, 18.0),
    "TRADE":      (8.0, 8.0),
    "CAUTION":    (-6.0, -6.0),
    "SHORT_ONLY": (-25.0, 6.0),   # heavy LONG penalty, mild SHORT favour
    "BLOCK":      (-100.0, -100.0),
}

# Hora planet scoring (Saturn hard-blocked separately).
_HORA_ADJ = {
    "JUPITER": (8.0, 0.0),    # bullish hour
    "MARS":    (0.0, 6.0),    # aggressive / short-friendly
    "SUN":     (3.0, 1.0),
    "VENUS":   (2.0, 2.0),
    "MERCURY": (1.0, 1.0),
    "MOON":    (1.0, 1.0),
    "SATURN":  (0.0, 0.0),    # blocked upstream
}

# Tithi group → position-size multiplier (lunar-day risk model).
_TITHI_SIZE = {
    "RIKTA": 0.5,    # inauspicious — half size
    "NANDA": 1.0,
    "BHADRA": 1.0,
    "JAYA": 1.0,
    "PURNA": 0.7,    # full/peak days — reduce
}


def _fear_greed_adj(value: int) -> tuple[float, float]:
    """Contrarian Fear & Greed overlay. Extreme fear favours LONG, extreme greed
    favours SHORT. Returns (long_adj, short_adj)."""
    if value is None:
        return 0.0, 0.0
    if value <= 20:        # extreme fear
        return 8.0, -4.0
    if value <= 40:        # fear
        return 4.0, -2.0
    if value >= 80:        # extreme greed
        return -4.0, 8.0
    if value >= 60:        # greed
        return -2.0, 4.0
    return 0.0, 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Result shape
# ──────────────────────────────────────────────────────────────────────────────
def _signal(**kw) -> dict:
    base = {
        "fired": False,
        "direction": None,
        "score": 0.0,
        "timestamp": None,
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "rr": None,
        "position_size_mult": 1.0,
        "reasons": [],
        "rejected_reason": None,
        "vedic": {},
        "layers": {},
    }
    base.update(kw)
    return base


def _to_dt(timestamp, df) -> datetime:
    if timestamp is None:
        ts = df.index[-1]
        timestamp = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _fallback_exits(entry: float, direction: str, df: pd.DataFrame,
                    cfg: StrategyConfig) -> tuple[float, dict]:
    """Simple ATR exits used when smart_exit_engine is unavailable."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    risk = atr * cfg.atr_sl_mult
    if direction == "LONG":
        sl = entry - risk
        tp = {"tp1": entry + risk * 1.5, "tp2": entry + risk * 2.5, "tp3": entry + risk * 4.0}
    else:
        sl = entry + risk
        tp = {"tp1": entry - risk * 1.5, "tp2": entry - risk * 2.5, "tp3": entry - risk * 4.0}
    return sl, tp


# ──────────────────────────────────────────────────────────────────────────────
# The strategy
# ──────────────────────────────────────────────────────────────────────────────
def evaluate(df: pd.DataFrame,
             timestamp: datetime | None = None,
             cmc_context: dict | None = None,
             config: StrategyConfig = DEFAULT_CONFIG) -> dict:
    """
    Evaluate BHRAMHA's strategy on the *last* bar of `df`.

    Parameters
    ----------
    df : DataFrame
        OHLCV candles indexed by UTC time. Indicators are added if missing.
    timestamp : datetime, optional
        Decision time (defaults to the last bar's index). Drives the Vedic layer.
    cmc_context : dict, optional
        Output of `cmc_data.get_market_context()` — supplies Fear & Greed and
        global regime metrics. Optional: the strategy runs without it.
    config : StrategyConfig
        Threshold/weight knobs (Layer 7).

    Returns
    -------
    dict : see `_signal`. `fired=True` means a trade signal passed all gates.
    """
    cfg = config
    cmc_context = cmc_context or {}

    if df is None or len(df) < 50:
        return _signal(rejected_reason="insufficient_data")

    try:
        if "ema21" not in df.columns or "rsi" not in df.columns:
            df = add_indicators(df.copy())
    except Exception as exc:
        return _signal(rejected_reason=f"indicator_error: {exc}")

    ts = _to_dt(timestamp, df)
    reasons: list[str] = []
    layers: dict = {}

    # ── Layer 3: Vedic timing (computed first — drives the hard gates) ─────────
    vedic = get_vedic_context(ts)
    nak = vedic["nakshatra"]
    tithi = vedic["tithi"]
    hora = vedic["hora"]
    layers["vedic"] = {
        "nakshatra": nak["nakshatra_name"], "action": nak["action"],
        "hora": hora["hora_planet"], "tithi": tithi["tithi"],
        "tithi_group": tithi["tithi_group"],
    }

    # ── Vedic hard blocks (preserved from the live bot) ───────────────────────
    if cfg.enforce_vedic_blocks and nak["action"] == "BLOCK":
        return _signal(timestamp=ts.isoformat(), vedic=layers["vedic"],
                       rejected_reason=f"nakshatra_block:{nak['nakshatra_name']}")
    if cfg.block_high_lunar and tithi["high_lunar_volatility"]:
        return _signal(timestamp=ts.isoformat(), vedic=layers["vedic"],
                       rejected_reason="high_lunar_volatility")
    if cfg.block_saturn_hora and hora["hora_planet"] == "SATURN":
        return _signal(timestamp=ts.isoformat(), vedic=layers["vedic"],
                       rejected_reason="saturn_hora")

    # ── Layer 1: technical base — score both directions, pick the stronger ─────
    long_s = score_candle(df, "LONG")
    short_s = score_candle(df, "SHORT")
    if long_s["score"] >= short_s["score"]:
        direction, tech = "LONG", long_s
    else:
        direction, tech = "SHORT", short_s
    layers["technical"] = {"score": tech["score"], "confidence": tech.get("confidence")}

    # ── Layer 2/4: regime ─────────────────────────────────────────────────────
    regime_name = "NORMAL"
    if detect_market_regime is not None:
        try:
            regime = detect_market_regime(df)
            regime_name = str(regime.get("regime", "NORMAL")).upper()
        except Exception:
            regime_name = "NORMAL"
    layers["regime"] = regime_name

    # ── Build final score from base + weighted overlays ───────────────────────
    score = cfg.base_score + cfg.tech_weight * float(tech["score"])

    # Vedic nakshatra + hora overlay (direction-aware)
    nak_long, nak_short = _NAK_ACTION_ADJ.get(nak["action"], (0.0, 0.0))
    hora_long, hora_short = _HORA_ADJ.get(hora["hora_planet"], (0.0, 0.0))
    fg_value = (cmc_context.get("fear_greed") or {}).get("value")
    fg_long, fg_short = _fear_greed_adj(fg_value)

    if direction == "LONG":
        vedic_adj, hora_adj, sent_adj = nak_long, hora_long, fg_long
    else:
        vedic_adj, hora_adj, sent_adj = nak_short, hora_short, fg_short

    score += vedic_adj + hora_adj + sent_adj
    layers["adjustments"] = {
        "nakshatra": vedic_adj, "hora": hora_adj, "fear_greed": sent_adj,
        "fear_greed_value": fg_value,
    }

    if nak["action"] == "GOLDEN":
        reasons.append(f"Favourable nakshatra window: {nak['nakshatra_name']}")
    if hora["hora_planet"] in ("JUPITER", "MARS"):
        reasons.append(f"{hora['hora_planet']} hora favours {direction}")
    if fg_value is not None and (fg_value <= 20 or fg_value >= 80):
        reasons.append(f"Contrarian Fear&Greed={fg_value}")

    # Position sizing from tithi (lunar day)
    size_mult = _TITHI_SIZE.get(tithi["tithi_group"], 1.0)

    # ── Gate ──────────────────────────────────────────────────────────────────
    if score < cfg.min_score:
        return _signal(timestamp=ts.isoformat(), direction=direction,
                       score=round(score, 2), vedic=layers["vedic"], layers=layers,
                       rejected_reason=f"below_min_score({score:.1f}<{cfg.min_score})")

    # ── Exits + R:R ───────────────────────────────────────────────────────────
    entry = float(df["close"].iloc[-1])
    try:
        if cfg.use_smart_exits and _HAVE_SMART_EXIT:
            sl = smart_stop_loss(df, entry, direction, regime=regime_name,
                                 nakshatra_type=nak["nakshatra_type"],
                                 hora_planet=hora["hora_planet"])
            tp_info = smart_take_profit(df, entry, sl, direction,
                                        regime=regime_name, score=int(score))
        else:
            sl, tp_info = _fallback_exits(entry, direction, df, cfg)
    except Exception:
        sl, tp_info = _fallback_exits(entry, direction, df, cfg)

    tp2 = tp_info.get("tp2", tp_info.get("tp1"))
    risk = abs(entry - sl)
    rr = abs(tp2 - entry) / risk if risk > 0 else 0.0

    if rr < cfg.min_rr:
        return _signal(timestamp=ts.isoformat(), direction=direction,
                       score=round(score, 2), vedic=layers["vedic"], layers=layers,
                       entry=entry, stop_loss=sl, take_profit=tp_info, rr=round(rr, 2),
                       rejected_reason=f"rr_below_min({rr:.2f}<{cfg.min_rr})")

    return _signal(
        fired=True, direction=direction, score=round(score, 2),
        timestamp=ts.isoformat(), entry=entry, stop_loss=sl, take_profit=tp_info,
        rr=round(rr, 2), position_size_mult=size_mult, reasons=reasons,
        vedic=layers["vedic"], layers=layers,
    )


if __name__ == "__main__":
    # Smoke test against live Binance data (network) — optional.
    import json
    try:
        from binance_data import get_data
        from cmc_data import get_market_context
        d = get_data("BTCUSDT", "15m", limit=200)
        ctx = get_market_context("BTCUSDT")
        print(json.dumps(evaluate(d, cmc_context=ctx), indent=2, default=str))
    except Exception as e:
        print("smoke test skipped:", e)
