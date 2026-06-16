from __future__ import annotations


def _swing_levels(series, lookback=3):
    levels = []
    if len(series) < (lookback * 2 + 1):
        return levels
    values = series.tolist()
    for i in range(lookback, len(values) - lookback):
        center = values[i]
        left = values[i - lookback:i]
        right = values[i + 1:i + 1 + lookback]
        if all(center >= x for x in left) and all(center >= x for x in right):
            levels.append(float(center))
    return levels


def detect_liquidity_zones(data):
    import pandas as pd

    df = data.copy()
    if len(df) < 30:
        last_close = float(df["close"].iloc[-1]) if len(df) else 0.0
        return {
            "liquidity_above": last_close,
            "liquidity_below": last_close,
            "distance_to_liquidity": 0.0,
            "nearest_pool_distance": 0.0,
            "equal_high": None,
            "equal_low": None,
            "recent_swing_high": None,
            "recent_swing_low": None,
            "range_high": last_close,
            "range_low": last_close,
            "prev_day_high": None,
            "prev_day_low": None,
        }

    highs = pd.to_numeric(df["high"], errors="coerce")
    lows = pd.to_numeric(df["low"], errors="coerce")
    closes = pd.to_numeric(df["close"], errors="coerce")
    last_close = float(closes.iloc[-1])
    eq_tolerance_pct = 0.002  # 0.2%

    # Equal highs / lows from recent candles.
    recent_highs = highs.tail(60).tolist()
    recent_lows = lows.tail(60).tolist()
    equal_high = None
    equal_low = None

    for i in range(len(recent_highs) - 1):
        for j in range(i + 1, len(recent_highs)):
            base = max(abs(recent_highs[i]), abs(recent_highs[j]), 1e-9)
            if abs(recent_highs[i] - recent_highs[j]) / base <= eq_tolerance_pct:
                equal_high = (recent_highs[i] + recent_highs[j]) / 2.0
    for i in range(len(recent_lows) - 1):
        for j in range(i + 1, len(recent_lows)):
            base = max(abs(recent_lows[i]), abs(recent_lows[j]), 1e-9)
            if abs(recent_lows[i] - recent_lows[j]) / base <= eq_tolerance_pct:
                equal_low = (recent_lows[i] + recent_lows[j]) / 2.0

    # Previous day levels from last 24 bars approximation on intraday data.
    day_window = df.tail(96) if len(df) >= 96 else df
    prev_day_high = float(day_window["high"].max()) if len(day_window) else None
    prev_day_low = float(day_window["low"].min()) if len(day_window) else None

    swing_highs = _swing_levels(highs.tail(120), lookback=3)
    swing_lows = _swing_levels(lows.tail(120).mul(-1), lookback=3)
    swing_lows = [-x for x in swing_lows]
    recent_swing_high = swing_highs[-1] if swing_highs else None
    recent_swing_low = swing_lows[-1] if swing_lows else None
    range_high = float(highs.tail(50).max())
    range_low = float(lows.tail(50).min())

    candidates_above = [x for x in [equal_high, prev_day_high, recent_swing_high, range_high] if x is not None and x > last_close]
    candidates_below = [x for x in [equal_low, prev_day_low, recent_swing_low, range_low] if x is not None and x < last_close]
    candidates_above += [x for x in swing_highs if x > last_close]
    candidates_below += [x for x in swing_lows if x < last_close]

    liquidity_above = min(candidates_above) if candidates_above else range_high
    liquidity_below = max(candidates_below) if candidates_below else range_low

    nearest_pool_distance = min(abs(liquidity_above - last_close), abs(last_close - liquidity_below))
    distance_to_liquidity = (nearest_pool_distance / max(last_close, 1e-9)) * 100.0

    return {
        "liquidity_above": float(liquidity_above),
        "liquidity_below": float(liquidity_below),
        "distance_to_liquidity": float(distance_to_liquidity),
        "nearest_pool_distance": float(nearest_pool_distance),
        "equal_high": float(equal_high) if equal_high is not None else None,
        "equal_low": float(equal_low) if equal_low is not None else None,
        "recent_swing_high": float(recent_swing_high) if recent_swing_high is not None else None,
        "recent_swing_low": float(recent_swing_low) if recent_swing_low is not None else None,
        "range_high": float(range_high),
        "range_low": float(range_low),
        "prev_day_high": prev_day_high,
        "prev_day_low": prev_day_low,
    }


def detect_liquidity_sweep_context(data):
    zones = detect_liquidity_zones(data)
    last = data.iloc[-1]
    last_high = float(last["high"])
    last_low = float(last["low"])
    last_close = float(last["close"])
    last_open = float(last["open"])
    prev_close = float(data["close"].iloc[-2]) if len(data) > 1 else last_close

    upward_shift = last_close > max(last_open, prev_close)
    downward_shift = last_close < min(last_open, prev_close)

    equal_low = zones.get("equal_low")
    equal_high = zones.get("equal_high")

    swept_below = bool(equal_low is not None and last_low < equal_low and upward_shift)
    swept_above = bool(equal_high is not None and last_high > equal_high and downward_shift)

    return {
        "swept_below": swept_below,
        "swept_above": swept_above,
        "sweep_direction": "DOWN" if swept_below else ("UP" if swept_above else "NONE"),
    }


def detect_liquidity_sweep(df):

    last = df.iloc[-1]

    high = last["high"]
    low = last["low"]
    open_price = last["open"]
    close = last["close"]

    body = abs(close - open_price)
    candle_range = high - low

    upper_wick = high - max(close, open_price)
    lower_wick = min(close, open_price) - low

    sweep = "None"
    modifier = 1

    # stop hunt below
    if lower_wick > body * 2:
        sweep = "Liquidity Grab Down"
        modifier = 1.2

    # stop hunt above
    if upper_wick > body * 2:
        sweep = "Liquidity Grab Up"
        modifier = 1.2

    return {
        "type": sweep,
        "modifier": modifier
    }


def detect_liquidity_event(data):
    """
    Detect smart money liquidity behavior:
    - Equal highs/lows (0.1% tolerance)
    - Liquidity grab up/down
    - Stop hunt by wick/body imbalance
    """
    import pandas as pd

    df = data.copy()
    if len(df) < 3:
        return {"liquidity_event": "None"}

    highs = pd.to_numeric(df["high"], errors="coerce")
    lows = pd.to_numeric(df["low"], errors="coerce")
    opens = pd.to_numeric(df["open"], errors="coerce")
    closes = pd.to_numeric(df["close"], errors="coerce")

    last = df.iloc[-1]
    last_high = float(last["high"])
    last_low = float(last["low"])
    last_open = float(last["open"])
    last_close = float(last["close"])

    prev_high_1 = float(highs.iloc[-2])
    prev_high_2 = float(highs.iloc[-3])
    prev_low_1 = float(lows.iloc[-2])
    prev_low_2 = float(lows.iloc[-3])

    def within_point_one_percent(a, b):
        base = max(abs(a), abs(b), 1e-9)
        return abs(a - b) / base <= 0.001

    # 1) Equal High Detection -> Liquidity Grab Up
    equal_highs = within_point_one_percent(prev_high_1, prev_high_2)
    eq_high_level = (prev_high_1 + prev_high_2) / 2.0
    if equal_highs and last_high > eq_high_level and last_close < eq_high_level:
        return {"liquidity_event": "Liquidity Grab Up"}

    # 2) Equal Low Detection -> Liquidity Grab Down
    equal_lows = within_point_one_percent(prev_low_1, prev_low_2)
    eq_low_level = (prev_low_1 + prev_low_2) / 2.0
    if equal_lows and last_low < eq_low_level and last_close > eq_low_level:
        return {"liquidity_event": "Liquidity Grab Down"}

    # 3) Stop Hunt Detection
    body = abs(last_close - last_open)
    upper_wick = last_high - max(last_close, last_open)
    lower_wick = min(last_close, last_open) - last_low
    wick = max(upper_wick, lower_wick)
    if wick > body * 2:
        return {"liquidity_event": "Stop Hunt"}

    return {"liquidity_event": "None"}
