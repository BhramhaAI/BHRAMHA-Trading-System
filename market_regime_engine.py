"""
BHRAMHA - Enhanced Market Regime Engine (FIXED v2)
===================================================
Changes from v1:
1. Added SIDEWAYS regime (alias for RANGING with tighter detection).
2. Strengthened TRENDING_BULL / TRENDING_BEAR thresholds — ADX must be >= 28
   AND price must be above/below BOTH EMA50 AND EMA200 for a full bull regime.
3. Added `trend_direction` output field: "UP" | "DOWN" | "SIDEWAYS"
   so signal_engine.py can use it directly as a LONG/SHORT gate.
4. Added `long_allowed` and `short_allowed` boolean flags that signal_engine
   can check before generating directional signals.
5. Reduced VOLATILE false-positives: requires ADX < 20 (not < 25).
"""

import pandas as pd
import numpy as np


def detect_market_regime(df: pd.DataFrame) -> dict:
    """
    Classify current market into one of:
        TRENDING_BULL  – strong uptrend  (ADX>=28, price > EMA50 & EMA200, +DI > -DI)
        TRENDING_BEAR  – strong downtrend (ADX>=28, price < EMA50 & EMA200, -DI > +DI)
        RANGING        – low-volatility sideways / no trend
        SIDEWAYS       – same as RANGING, used when ADX is very low
        VOLATILE       – high ATR but no directional conviction
        BREAKOUT       – BB squeeze + volume surge
        NORMAL         – default, moderate conditions

    Returns dict with:
        regime, regime_score, note, adx, atr_pct, bb_width, vol_ratio,
        trend_direction ("UP"|"DOWN"|"SIDEWAYS"),
        long_allowed (bool), short_allowed (bool)
    """
    if len(df) < 50:
        return {
            "regime": "NORMAL",
            "regime_score": 50,
            "note": "Insufficient data",
            "trend_direction": "SIDEWAYS",
            "long_allowed": False,
            "short_allowed": False,
        }

    df = df.copy()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # ── ATR ───────────────────────────────────────────────────────
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr      = tr.ewm(com=13, adjust=False).mean()
    atr_now  = float(atr.iloc[-1])
    atr_avg  = float(atr.rolling(20).mean().iloc[-1])
    atr_pct  = atr_now / max(float(close.iloc[-1]), 1e-9) * 100

    # ── ADX ───────────────────────────────────────────────────────
    up_move  = high.diff()
    dn_move  = -low.diff()
    plus_dm  = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    tr_s     = pd.Series(tr).ewm(com=13, adjust=False).mean()
    plus_di  = 100 * pd.Series(plus_dm, index=df.index).ewm(com=13, adjust=False).mean() / tr_s
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(com=13, adjust=False).mean() / tr_s
    dx       = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx      = float(dx.ewm(com=13, adjust=False).mean().iloc[-1])
    plus_di_val  = float(plus_di.iloc[-1])
    minus_di_val = float(minus_di.iloc[-1])

    # ── Bollinger Width ───────────────────────────────────────────
    bb_mid       = close.rolling(20).mean()
    bb_std       = close.rolling(20).std()
    bb_upper     = bb_mid + 2 * bb_std
    bb_lower     = bb_mid - 2 * bb_std
    bb_width     = float(((bb_upper - bb_lower) / bb_mid).iloc[-1])
    bb_width_avg = float(((bb_upper - bb_lower) / bb_mid).rolling(20).mean().iloc[-1])

    # ── EMA trend ─────────────────────────────────────────────────
    ema50    = close.ewm(span=50,  adjust=False).mean()
    ema200   = close.ewm(span=200, adjust=False).mean()
    latest   = float(close.iloc[-1])
    ema50_v  = float(ema50.iloc[-1])
    ema200_v = float(ema200.iloc[-1])

    # ── Volume surge ──────────────────────────────────────────────
    vol_avg   = float(volume.rolling(20).mean().iloc[-1])
    vol_now   = float(volume.iloc[-1])
    vol_ratio = vol_now / max(vol_avg, 1e-9)

    # ── Slope of EMA50 (direction confirmation) ───────────────────
    ema50_5bars_ago = float(ema50.iloc[-6]) if len(ema50) > 6 else ema50_v
    ema50_slope_pct = (ema50_v - ema50_5bars_ago) / max(abs(ema50_5bars_ago), 1e-9) * 100

    # ── Regime classification ──────────────────────────────────────
    regime       = "NORMAL"
    regime_score = 50
    note         = ""

    # BREAKOUT: BB squeeze (width < 80 % of avg) + volume surge
    if bb_width < bb_width_avg * 0.8 and vol_ratio > 1.5:
        regime       = "BREAKOUT"
        regime_score = 82
        note         = "Bollinger squeeze with volume surge – breakout likely imminent."

    # VOLATILE: High ATR relative to average, AND low ADX (choppy)
    elif atr_now > atr_avg * 1.5 and adx < 20:
        regime       = "VOLATILE"
        regime_score = 38
        note         = f"ATR spike ({atr_pct:.1f}%) without directional trend – choppy."

    # TRENDING_BULL: Strong ADX + price above BOTH EMAs + +DI dominant + slope up
    elif (
        adx >= 28
        and latest > ema50_v
        and latest > ema200_v
        and plus_di_val > minus_di_val
        and ema50_slope_pct > 0
    ):
        regime       = "TRENDING_BULL"
        regime_score = 90
        note         = f"Strong bull trend (ADX={adx:.0f}, price above EMA50 & EMA200)."

    # TRENDING_BEAR: Strong ADX + price below BOTH EMAs + -DI dominant + slope down
    elif (
        adx >= 28
        and latest < ema50_v
        and latest < ema200_v
        and minus_di_val > plus_di_val
        and ema50_slope_pct < 0
    ):
        regime       = "TRENDING_BEAR"
        regime_score = 90
        note         = f"Strong bear trend (ADX={adx:.0f}, price below EMA50 & EMA200)."

    # RANGING / SIDEWAYS: Low ADX and narrow Bollinger
    elif adx < 20 and bb_width < bb_width_avg * 0.95:
        regime       = "RANGING"
        regime_score = 52
        note         = f"Low ADX ({adx:.0f}) and narrow Bollinger – range-bound market."

    # SIDEWAYS: ADX slightly higher but price stuck between EMAs
    elif adx < 25 and ema200_v * 0.99 <= latest <= ema200_v * 1.01:
        regime       = "SIDEWAYS"
        regime_score = 50
        note         = f"Price hugging EMA200 (ADX={adx:.0f}) – sideways chop."

    else:
        regime       = "NORMAL"
        regime_score = 58
        note         = f"Moderate conditions (ADX={adx:.0f}, ATR%={atr_pct:.1f}%)."

    # ── Directional summary ───────────────────────────────────────
    if regime == "TRENDING_BULL":
        trend_direction = "UP"
    elif regime == "TRENDING_BEAR":
        trend_direction = "DOWN"
    else:
        trend_direction = "SIDEWAYS"

    # ── Gate flags for signal_engine ─────────────────────────────
    # LONG only allowed in bull trends or neutral/breakout with upward bias
    long_allowed = regime in {
        "TRENDING_BULL", "BREAKOUT", "NORMAL"
    } and trend_direction in {"UP", "SIDEWAYS"}

    # SHORT allowed in bear trends, ranging (mean revert), volatile (counter), and normal
    short_allowed = regime in {
        "TRENDING_BEAR", "RANGING", "SIDEWAYS", "VOLATILE", "NORMAL", "BREAKOUT"
    }

    # In RANGING / SIDEWAYS, longs are risky — require higher conviction
    long_allowed_strict = regime in {"TRENDING_BULL"}
    short_allowed_strict = regime in {"TRENDING_BEAR", "RANGING", "SIDEWAYS", "VOLATILE"}

    return {
        "regime":              regime,
        "regime_score":        regime_score,
        "note":                note,
        "adx":                 round(adx, 1),
        "atr_pct":             round(atr_pct, 2),
        "bb_width":            round(bb_width, 4),
        "vol_ratio":           round(vol_ratio, 2),
        "trend_direction":     trend_direction,
        "long_allowed":        long_allowed,
        "short_allowed":       short_allowed,
        "long_allowed_strict": long_allowed_strict,
        "short_allowed_strict": short_allowed_strict,
        "ema50_slope_pct":     round(ema50_slope_pct, 3),
        "plus_di":             round(plus_di_val, 1),
        "minus_di":            round(minus_di_val, 1),
    }