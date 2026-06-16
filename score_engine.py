"""
BHRAMHA - Score Engine (FIXED v2)
==================================
Changes from v1:
1. apply_short_overextension_boost: removed the ADX > 35 guard that was
   silently cancelling the boost.  ADX > 35 means a STRONG trend — if price
   is overextended in that trend, shorting the exhaustion is exactly right.
   Guard is replaced with a smarter check: only skip boost when ADX > 35
   AND the trend is clearly bearish (which would mean price already fell hard,
   not that it's overextended to the upside).
2. score_candle: counter_ema200 penalty (-20) now only fires when CLEARLY
   counter-trend — added a 0.5% buffer so entries right at EMA200 don't get
   wrongly punished.
3. Absorption scoring: fixed logic so that it correctly rewards SHORT at
   resistance and LONG at support, and penalises the opposite.
"""

import math
import pandas as pd
import numpy as np

from overextension_engine import detect_overextension


# ─── Normalisation ────────────────────────────────────────────────────────────

def normalize_score(score: float) -> float:
    """Logistic normalisation to 0-100 range."""
    normalized = 100 / (1 + math.exp(-score / 50))
    return round(normalized, 2)


# ─── Short overextension boost ────────────────────────────────────────────────

def apply_short_overextension_boost(data, short_score, adx=None):
    """
    Boost short-side score in exhaustion conditions.

    FIX v2: The original guard `if ADX > 35: skip boost` was wrong.
    High ADX means a strong trend — if price is ALSO overextended, shorting
    the exhaustion top is one of the highest-probability setups.
    We now only skip the boost when ADX > 35 AND the market is in a clear
    DOWNTREND already (meaning the exhaustion engine is firing incorrectly
    because price fell, not because it rose unsustainably).
    """
    overext = detect_overextension(data)

    if not overext.get("overextended"):
        return float(short_score), overext, 0

    # Only skip boost if we are deeply in a downtrend (not an overextended top)
    if adx is not None and float(adx) > 35:
        # Check if this looks like a downtrend exhaustion (wrong signal)
        try:
            close = pd.to_numeric(data["close"], errors="coerce")
            ema50 = close.ewm(span=50, adjust=False).mean()
            last_close = float(close.iloc[-1])
            last_ema50 = float(ema50.iloc[-1])
            already_in_downtrend = last_close < last_ema50 * 0.985  # price 1.5%+ below EMA50
            if already_in_downtrend:
                # Don't boost short — price is already sold off
                return float(short_score), overext, 0
        except Exception:
            pass  # if check fails, allow boost

    boost = int(overext.get("score_boost", 0))
    boosted_short_score = max(0.0, min(100.0, float(short_score) + boost))
    return boosted_short_score, overext, boost


# ─── Volume profile / LVN ─────────────────────────────────────────────────────

LVN_SCORING_ENABLED = True
LVN_LOOKBACK = 100
LVN_BUCKETS = 50
LVN_VOLUME_THRESHOLD = 0.20
LVN_NEAR_PCT = 0.003

ABSORPTION_ENABLED = True
ABSORPTION_LOOKBACK = 20
ABSORPTION_TRIGGER_MULT = 2.5
ABSORPTION_BODY_EPSILON = 0.00001
ABSORPTION_ZONE_NEAR_PCT = 0.003

WEIGHTS = {
    "ema_trend":  15,
    "supertrend": 10,
    "macd":       10,
    "rsi":        10,
    "stoch_rsi":   8,
    "volume":      8,
    "vwap":        7,
    "adx":         7,
    "cmf":         7,
    "obv":         5,
    "bb":          5,
    "pivot":       4,
    "momentum":    4,
}


def build_volume_profile(df, lookback=LVN_LOOKBACK, buckets=LVN_BUCKETS):
    window = df.tail(lookback).copy()
    if len(window) < 10:
        return []

    lows    = pd.to_numeric(window["low"],    errors="coerce")
    highs   = pd.to_numeric(window["high"],   errors="coerce")
    closes  = pd.to_numeric(window["close"],  errors="coerce")
    volumes = pd.to_numeric(window["volume"], errors="coerce").fillna(0.0)

    price_min = float(lows.min())
    price_max = float(highs.max())
    if not np.isfinite(price_min) or not np.isfinite(price_max) or price_max <= price_min:
        return []

    edges         = np.linspace(price_min, price_max, int(buckets) + 1)
    bucket_volume = np.zeros(int(buckets), dtype=float)

    for close_price, candle_volume in zip(closes, volumes):
        if not np.isfinite(close_price):
            continue
        idx = np.searchsorted(edges, float(close_price), side="right") - 1
        idx = max(0, min(int(buckets) - 1, idx))
        bucket_volume[idx] += float(candle_volume)

    profile = []
    for idx in range(int(buckets)):
        low_edge  = float(edges[idx])
        high_edge = float(edges[idx + 1])
        midpoint  = (low_edge + high_edge) / 2.0
        profile.append({
            "low":    low_edge,
            "high":   high_edge,
            "mid":    midpoint,
            "volume": float(bucket_volume[idx]),
        })
    return profile


def detect_low_volume_nodes(df, lookback=LVN_LOOKBACK, buckets=LVN_BUCKETS,
                            threshold=LVN_VOLUME_THRESHOLD):
    if not LVN_SCORING_ENABLED:
        return []

    profile = build_volume_profile(df, lookback=lookback, buckets=buckets)
    if not profile:
        return []

    avg_bucket_volume = float(np.mean([b["volume"] for b in profile]))
    if avg_bucket_volume <= 0:
        return []

    max_volume = avg_bucket_volume * float(threshold)
    return [
        {**bucket, "avg_volume": avg_bucket_volume}
        for bucket in profile
        if float(bucket["volume"]) < max_volume
    ]


def find_nearest_lvn(entry, lvns, near_pct=LVN_NEAR_PCT):
    entry = float(entry)
    max_distance = abs(entry) * float(near_pct)
    matches = [lvn for lvn in lvns if abs(float(lvn["mid"]) - entry) <= max_distance]
    if not matches:
        return None
    return min(matches, key=lambda lvn: abs(float(lvn["mid"]) - entry))


def find_lvn_between(entry, target, direction, lvns):
    entry     = float(entry)
    target    = float(target)
    direction = str(direction).upper()

    if direction == "LONG":
        candidates = [lvn for lvn in lvns if entry < float(lvn["mid"]) <= target]
        if not candidates:
            return None
        return min(candidates, key=lambda lvn: float(lvn["mid"]))

    candidates = [lvn for lvn in lvns if target <= float(lvn["mid"]) < entry]
    if not candidates:
        return None
    return max(candidates, key=lambda lvn: float(lvn["mid"]))


# ─── Absorption detection ─────────────────────────────────────────────────────

def _safe_absorption_ratio(volume, open_price, close_price):
    body_size = abs(float(close_price) - float(open_price))
    body_size = max(body_size, ABSORPTION_BODY_EPSILON)
    return float(volume) / body_size


def _extract_zone_value(row, keys):
    for key in keys:
        value = row.get(key)
        if value is None or pd.isna(value):
            continue
        return float(value)
    return None


def _recent_structure_levels(df, lookback=ABSORPTION_LOOKBACK):
    window = df.tail(lookback)
    if len(window) < 3:
        return None, None

    resistance = _extract_zone_value(window.iloc[-1], [
        "nearest_resistance", "resistance_zone", "supply_zone",
        "supply_zone_high", "orderblock_bearish", "bearish_ob_top",
    ])
    support = _extract_zone_value(window.iloc[-1], [
        "nearest_support", "support_zone", "demand_zone",
        "demand_zone_low", "orderblock_bullish", "bullish_ob_bottom",
    ])

    if resistance is None:
        resistance = float(pd.to_numeric(window["high"], errors="coerce").max())
    if support is None:
        support = float(pd.to_numeric(window["low"], errors="coerce").min())

    return resistance, support


def detect_absorption(df, lookback=ABSORPTION_LOOKBACK):
    if not ABSORPTION_ENABLED or len(df) < max(lookback + 1, 5):
        return {"detected": False}

    window  = df.tail(lookback + 1).copy()
    current = window.iloc[-1]
    previous = window.iloc[:-1].copy()
    if previous.empty:
        return {"detected": False}

    current_ratio = _safe_absorption_ratio(
        current.get("volume", 0.0),
        current.get("open",   0.0),
        current.get("close",  0.0),
    )
    previous_ratios = [
        _safe_absorption_ratio(
            row.get("volume", 0.0),
            row.get("open",   0.0),
            row.get("close",  0.0),
        )
        for _, row in previous.iterrows()
    ]
    average_ratio = float(np.mean(previous_ratios)) if previous_ratios else 0.0
    detected = average_ratio > 0 and current_ratio > (average_ratio * ABSORPTION_TRIGGER_MULT)

    resistance, support = _recent_structure_levels(df, lookback=lookback)
    close_price   = float(current.get("close", 0.0))
    zone_tolerance = max(abs(close_price) * ABSORPTION_ZONE_NEAR_PCT, ABSORPTION_BODY_EPSILON)
    at_resistance = resistance is not None and abs(close_price - float(resistance)) <= zone_tolerance
    at_support    = support    is not None and abs(close_price - float(support))    <= zone_tolerance

    return {
        "detected":      detected,
        "current_ratio": current_ratio,
        "average_ratio": average_ratio,
        "trigger_ratio": average_ratio * ABSORPTION_TRIGGER_MULT,
        "resistance":    resistance,
        "support":       support,
        "at_resistance": at_resistance,
        "at_support":    at_support,
    }


# ─── Main candle scorer ───────────────────────────────────────────────────────

def score_candle(df, direction):
    """
    Score a candle for the given direction (LONG or SHORT).

    FIX v2: counter_ema200 penalty now uses a 0.5% buffer so that price
    just at EMA200 doesn't get wrongly penalised.
    """
    try:
        if len(df) < 30:
            return {"score": 0, "confidence": 0, "breakdown": {}, "flags": []}

        row  = df.iloc[-1]
        prev = df.iloc[-2]
        bull = (direction == "LONG")
        breakdown: dict = {}
        flags: list[str] = []

        close = float(row["close"])

        # ── EMA trend ─────────────────────────────────────────────
        e9   = float(row.get("ema9",   close))
        e21  = float(row.get("ema21",  close))
        e50  = float(row.get("ema50",  close))
        e200 = float(row.get("ema200", close))

        if bull:
            if   e9 > e21 and e21 > e50 and e50 > e200: breakdown["ema_trend"] = 15
            elif e9 > e21 and e21 > e50:                 breakdown["ema_trend"] = 10
            elif close > e50:                             breakdown["ema_trend"] = 6
            else:                                         breakdown["ema_trend"] = 0
        else:
            if   e9 < e21 and e21 < e50 and e50 < e200: breakdown["ema_trend"] = 15
            elif e9 < e21 and e21 < e50:                 breakdown["ema_trend"] = 10
            elif close < e50:                             breakdown["ema_trend"] = 6
            else:                                         breakdown["ema_trend"] = 0

        # ── Supertrend ────────────────────────────────────────────
        st = int(row.get("supertrend_dir", 0))
        breakdown["supertrend"] = 10 if (bull and st == 1) or (not bull and st == -1) else 0

        # ── MACD ──────────────────────────────────────────────────
        hist  = float(row.get("macd_hist",  row.get("macd", 0)))
        phist = float(prev.get("macd_hist", prev.get("macd", 0)))
        crossed  = (bull and hist > 0 and phist <= 0) or (not bull and hist < 0 and phist >= 0)
        trending = (bull and hist > 0) or (not bull and hist < 0)
        breakdown["macd"] = 10 if crossed else (6 if trending else 0)

        # ── RSI ───────────────────────────────────────────────────
        rsi = float(row.get("rsi", 50))
        if bull:
            breakdown["rsi"] = (
                10 if 45 <= rsi <= 65 else
                8  if 35 <= rsi < 45  else
                5  if rsi < 35        else 0
            )
            if rsi > 75:
                flags.append("rsi_overbought")
        else:
            breakdown["rsi"] = (
                10 if 35 <= rsi <= 55 else
                8  if 55 < rsi <= 65  else
                5  if rsi > 65        else 0
            )
            if rsi < 25:
                flags.append("rsi_oversold")

        # ── StochRSI ──────────────────────────────────────────────
        sk  = float(row.get("stoch_rsi_k", 50))
        sd  = float(row.get("stoch_rsi_d", 50))
        psk = float(prev.get("stoch_rsi_k", 50))
        psd = float(prev.get("stoch_rsi_d", 50))
        cross_up   = psk <= psd and sk > sd and sk < 80
        cross_down = psk >= psd and sk < sd and sk > 20
        if   (bull and cross_up)   or (not bull and cross_down):  breakdown["stoch_rsi"] = 8
        elif (bull and sk > sd and sk < 80) or (not bull and sk < sd and sk > 20):
            breakdown["stoch_rsi"] = 4
        else:
            breakdown["stoch_rsi"] = 0

        # ── Volume ────────────────────────────────────────────────
        vr = float(row.get("vol_ratio", row.get("volume", 1)))
        if vr == 0:
            vr = 1
        breakdown["volume"] = (
            8 if vr >= 2.0 else
            5 if vr >= 1.5 else
            3 if vr >= 1.2 else 0
        )

        # ── VWAP ──────────────────────────────────────────────────
        vwap  = float(row.get("vwap", close))
        above = close > vwap
        breakdown["vwap"] = 7 if (bull and above) or (not bull and not above) else 0

        # ── ADX ───────────────────────────────────────────────────
        adx = float(row.get("adx", 20))
        pdi = float(row.get("plus_di",  0))
        mdi = float(row.get("minus_di", 0))
        di_ok = (bull and pdi > mdi) or (not bull and mdi > pdi)
        breakdown["adx"] = 7 if adx >= 30 and di_ok else (3 if adx >= 20 else 0)

        # ── CMF ───────────────────────────────────────────────────
        cmf = float(row.get("cmf", 0))
        breakdown["cmf"] = (
            7 if (bull and cmf > 0.15) or (not bull and cmf < -0.15) else
            3 if (bull and cmf > 0.05) or (not bull and cmf < -0.05) else 0
        )

        # ── OBV ───────────────────────────────────────────────────
        obv_r = int(row.get("obv_rising", 0))
        breakdown["obv"] = 5 if (bull and obv_r == 1) or (not bull and obv_r == 0) else 0

        # ── Bollinger ─────────────────────────────────────────────
        bb_pct = float(row.get("bb_pct", 0.5))
        if bull:   breakdown["bb"] = 5 if 0.4 <= bb_pct <= 0.8 else (2 if bb_pct < 0.2 else 0)
        else:      breakdown["bb"] = 5 if 0.2 <= bb_pct <= 0.6 else (2 if bb_pct > 0.8 else 0)

        # ── Pivot ─────────────────────────────────────────────────
        ns = int(row.get("near_pivot_support",    0))
        nr = int(row.get("near_pivot_resistance", 0))
        breakdown["pivot"] = 4 if (bull and ns == 1) or (not bull and nr == 1) else 0

        # ── Momentum ──────────────────────────────────────────────
        roc = float(row.get("roc_10", row.get("momentum_10", 0)))
        breakdown["momentum"] = (
            4 if (bull and roc > 2)   or (not bull and roc < -2)   else
            2 if (bull and roc > 0.5) or (not bull and roc < -0.5) else 0
        )

        # ── LVN near entry ────────────────────────────────────────
        lvns = detect_low_volume_nodes(df)
        nearest_lvn = find_nearest_lvn(close, lvns)
        breakdown["lvn_entry"] = 6 if nearest_lvn else 0
        if nearest_lvn:
            flags.append("lvn_near_entry")

        # ── Absorption ────────────────────────────────────────────
        absorption = detect_absorption(df)
        absorption_score = 0
        if absorption.get("detected"):
            if (not bull) and absorption.get("at_resistance"):
                # Selling absorption at resistance → strong SHORT signal
                absorption_score = 8
            elif bull and absorption.get("at_support"):
                # Buying absorption at support → strong LONG signal
                absorption_score = 8
            elif bull and absorption.get("at_resistance"):
                # Buying into resistance absorption → bad LONG
                absorption_score = -5
            elif (not bull) and absorption.get("at_support"):
                # Selling into support absorption → bad SHORT
                absorption_score = -5
        breakdown["absorption"] = absorption_score

        # ── Confluence bonus ──────────────────────────────────────
        nonzero = sum(1 for v in breakdown.values() if v > 0)
        bonus   = 10 if nonzero >= 7 else 0
        raw     = sum(breakdown.values()) + bonus

        # ── Counter EMA200 penalty (with 0.5% buffer) ─────────────
        # FIX v2: only penalise when price is CLEARLY on wrong side of EMA200,
        # not just touching it.
        buffer_pct = 0.005  # 0.5% tolerance
        if bull and close < e200 * (1 - buffer_pct):
            raw -= 20
            flags.append("counter_ema200")
        elif not bull and close > e200 * (1 + buffer_pct):
            raw -= 20
            flags.append("counter_ema200")

        score      = max(0, min(100, round(raw)))
        flag_penalty = len([f for f in flags if f != "counter_ema200"]) * 3
        confidence = max(0, min(100, score - flag_penalty))

        return {
            "score":                    score,
            "confidence":               confidence,
            "breakdown":                breakdown,
            "flags":                    flags,
            "lvn_near_entry":           bool(nearest_lvn),
            "lvn_entry_price":          float(nearest_lvn["mid"]) if nearest_lvn else None,
            "absorption_detected":      bool(absorption.get("detected")),
            "absorption_ratio":         float(absorption.get("current_ratio", 0.0)),
            "absorption_average_ratio": float(absorption.get("average_ratio", 0.0)),
            "absorption_at_resistance": bool(absorption.get("at_resistance")),
            "absorption_at_support":    bool(absorption.get("at_support")),
        }

    except Exception as e:
        import logging
        logging.warning(f"score_candle failed: {e}")
        return {"score": 0, "confidence": 0, "breakdown": {}, "flags": []}