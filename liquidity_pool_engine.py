from __future__ import annotations


def _equal_levels(values, tolerance_pct=0.001):
    if len(values) < 2:
        return False, None
    level = None
    found = False
    vals = [float(v) for v in values]
    for i in range(len(vals) - 1):
        for j in range(i + 1, len(vals)):
            a = vals[i]
            b = vals[j]
            base = max(abs(a), abs(b), 1e-9)
            if abs(a - b) / base <= tolerance_pct:
                found = True
                level = (a + b) / 2.0
    return found, level


def _swing_highs(highs, lookback=3):
    out = []
    data = [float(x) for x in highs]
    if len(data) < (lookback * 2 + 1):
        return out
    for i in range(lookback, len(data) - lookback):
        center = data[i]
        left = data[i - lookback:i]
        right = data[i + 1:i + 1 + lookback]
        if all(center >= x for x in left) and all(center >= x for x in right):
            out.append(center)
    return out


def _swing_lows(lows, lookback=3):
    out = []
    data = [float(x) for x in lows]
    if len(data) < (lookback * 2 + 1):
        return out
    for i in range(lookback, len(data) - lookback):
        center = data[i]
        left = data[i - lookback:i]
        right = data[i + 1:i + 1 + lookback]
        if all(center <= x for x in left) and all(center <= x for x in right):
            out.append(center)
    return out


def detect_liquidity_pools(data):
    import pandas as pd

    df = data.copy()
    if len(df) < 10:
        last_price = float(df["close"].iloc[-1]) if len(df) else 0.0
        return {
            "liquidity_above": last_price,
            "liquidity_below": last_price,
            "equal_highs": False,
            "equal_lows": False,
            "distance_to_liquidity": 0.0,
            "sweep_highs": False,
            "sweep_lows": False,
        }

    highs = pd.to_numeric(df["high"], errors="coerce")
    lows = pd.to_numeric(df["low"], errors="coerce")
    closes = pd.to_numeric(df["close"], errors="coerce")
    last_close = float(closes.iloc[-1])
    last_high = float(highs.iloc[-1])
    last_low = float(lows.iloc[-1])

    recent_highs = highs.tail(60).tolist()
    recent_lows = lows.tail(60).tolist()
    equal_highs, eq_high_level = _equal_levels(recent_highs, tolerance_pct=0.001)
    equal_lows, eq_low_level = _equal_levels(recent_lows, tolerance_pct=0.001)

    swing_high_levels = _swing_highs(highs.tail(120), lookback=3)
    swing_low_levels = _swing_lows(lows.tail(120), lookback=3)

    if isinstance(df.index, pd.DatetimeIndex):
        daily = df.resample("1D").agg({"high": "max", "low": "min"}).dropna()
        if len(daily) >= 2:
            prev_day = daily.iloc[-2]
            prev_day_high = float(prev_day["high"])
            prev_day_low = float(prev_day["low"])
        else:
            prev_day_high = float(highs.tail(96).max())
            prev_day_low = float(lows.tail(96).min())
    else:
        prev_day_high = float(highs.tail(96).max())
        prev_day_low = float(lows.tail(96).min())

    above_candidates = [x for x in swing_high_levels if x > last_close]
    below_candidates = [x for x in swing_low_levels if x < last_close]
    if eq_high_level is not None and eq_high_level > last_close:
        above_candidates.append(float(eq_high_level))
    if eq_low_level is not None and eq_low_level < last_close:
        below_candidates.append(float(eq_low_level))
    if prev_day_high > last_close:
        above_candidates.append(prev_day_high)
    if prev_day_low < last_close:
        below_candidates.append(prev_day_low)

    liquidity_above = min(above_candidates) if above_candidates else float(highs.tail(60).max())
    liquidity_below = max(below_candidates) if below_candidates else float(lows.tail(60).min())
    distance_to_liquidity = min(abs(liquidity_above - last_close), abs(last_close - liquidity_below))

    sweep_highs = bool(
        equal_highs
        and eq_high_level is not None
        and last_high > eq_high_level
        and last_close < eq_high_level
    )
    sweep_lows = bool(
        equal_lows
        and eq_low_level is not None
        and last_low < eq_low_level
        and last_close > eq_low_level
    )

    return {
        "liquidity_above": float(liquidity_above),
        "liquidity_below": float(liquidity_below),
        "equal_highs": bool(equal_highs),
        "equal_lows": bool(equal_lows),
        "distance_to_liquidity": float(distance_to_liquidity),
        "sweep_highs": sweep_highs,
        "sweep_lows": sweep_lows,
    }
