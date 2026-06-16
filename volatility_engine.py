def get_volatility_state(df, nakshatra_vol):

    atr = df["atr"].iloc[-1]
    atr_mean = df["atr"].mean()

    volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].mean()

    range_now = df["high"].iloc[-1] - df["low"].iloc[-1]

    state = "NORMAL"
    modifier = 1

    if atr < atr_mean * 0.8:
        state = "LOW VOLATILITY"
        modifier = 0.85

    elif atr > atr_mean * 1.2:
        state = "HIGH VOLATILITY"
        modifier = 1.15

    if volume > avg_volume * 1.5:
        state = "EXPLOSIVE"
        modifier = 1.25

    modifier = modifier * nakshatra_vol

    return {
        "state": state,
        "modifier": modifier
    }


def volatility_expansion(data):
    df = data.copy()
    if len(df) < 30:
        return {"volatility": "normal", "atr_trend": 0.0, "bb_width": 0.0}

    close = df["close"]
    atr = df["atr"]
    bb_upper = df.get("bb_upper")
    bb_lower = df.get("bb_lower")

    if bb_upper is None or bb_lower is None:
        ma = close.rolling(20, min_periods=20).mean()
        std = close.rolling(20, min_periods=20).std(ddof=0)
        bb_upper = ma + 2 * std
        bb_lower = ma - 2 * std

    atr_fast = float(atr.tail(5).mean())
    atr_slow = float(atr.tail(20).mean())
    atr_trend = (atr_fast - atr_slow) / max(atr_slow, 1e-9)

    bb_width = (bb_upper - bb_lower) / close.replace(0, 1e-9)
    width_fast = float(bb_width.tail(5).mean())
    width_slow = float(bb_width.tail(20).mean())
    width_ratio = width_fast / max(width_slow, 1e-9)

    label = "normal"
    if atr_trend > 0.08 and width_ratio > 1.05:
        label = "expanding"
    elif atr_trend < -0.08 and width_ratio < 0.95:
        label = "compressed"

    return {
        "volatility": label,
        "atr_trend": round(atr_trend, 4),
        "bb_width": round(width_fast, 4),
    }
