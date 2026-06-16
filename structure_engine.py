from __future__ import annotations


def _last_swings(df, lookback=3):
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    swing_highs = []
    swing_lows = []

    for i in range(lookback, len(df) - lookback):
        h = highs[i]
        l = lows[i]
        if all(h >= x for x in highs[i - lookback:i]) and all(h >= x for x in highs[i + 1:i + 1 + lookback]):
            swing_highs.append((i, float(h)))
        if all(l <= x for x in lows[i - lookback:i]) and all(l <= x for x in lows[i + 1:i + 1 + lookback]):
            swing_lows.append((i, float(l)))
    return swing_highs, swing_lows


def detect_structure(data):
    df = data.tail(160).copy()
    if len(df) < 20:
        return {
            "trend": "neutral",
            "bos_up": False,
            "bos_down": False,
            "choch_up": False,
            "choch_down": False,
            "market_structure": "RANGE",
            "hh": False,
            "hl": False,
            "lh": False,
            "ll": False,
        }

    swing_highs, swing_lows = _last_swings(df, lookback=3)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {
            "trend": "neutral",
            "bos_up": False,
            "bos_down": False,
            "choch_up": False,
            "choch_down": False,
            "market_structure": "RANGE",
            "hh": False,
            "hl": False,
            "lh": False,
            "ll": False,
        }

    prev_high = swing_highs[-2][1]
    last_high = swing_highs[-1][1]
    prev_low = swing_lows[-2][1]
    last_low = swing_lows[-1][1]
    close = float(df["close"].iloc[-1])

    hh = last_high > prev_high
    hl = last_low > prev_low
    lh = last_high < prev_high
    ll = last_low < prev_low

    trend = "neutral"
    if hh and hl:
        trend = "bullish"
    elif lh and ll:
        trend = "bearish"

    bos_up = close > prev_high
    bos_down = close < prev_low
    choch_up = bool(lh and ll and bos_up)
    choch_down = bool(hh and hl and bos_down)

    market_structure = "RANGE"
    if bos_up:
        market_structure = "BOS_UP"
    elif bos_down:
        market_structure = "BOS_DOWN"
    elif choch_up:
        market_structure = "CHOCH_UP"
    elif choch_down:
        market_structure = "CHOCH_DOWN"

    return {
        "trend": trend,
        "bos_up": bos_up,
        "bos_down": bos_down,
        "choch_up": choch_up,
        "choch_down": choch_down,
        "market_structure": market_structure,
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "last_swing_high": last_high,
        "last_swing_low": last_low,
    }
