from __future__ import annotations


def detect_volatility(data):
    df = data.copy()
    if len(df) < 25:
        return {
            "state": "normal",
            "volatility_expansion": False,
            "bb_width_now": 0.0,
            "bb_width_prev": 0.0,
            "atr_expansion": False,
            "volume_spike": False,
        }

    close = df["close"]
    bb_upper = df.get("bb_upper")
    bb_lower = df.get("bb_lower")

    if bb_upper is None or bb_lower is None:
        ma = close.rolling(20, min_periods=20).mean()
        std = close.rolling(20, min_periods=20).std(ddof=0)
        bb_upper = ma + (2 * std)
        bb_lower = ma - (2 * std)

    bb_width = (bb_upper - bb_lower) / close.replace(0, 1e-9)
    width_now = float(bb_width.iloc[-1])
    width_prev = float(bb_width.iloc[-2])
    width_avg_10 = float(bb_width.tail(10).mean())
    width_avg_20 = float(bb_width.tail(20).mean())
    atr_series = df["atr"] if "atr" in df.columns else (df["high"] - df["low"]).rolling(14).mean()
    atr_now = float(atr_series.iloc[-1])
    atr_prev = float(atr_series.iloc[-2])
    atr_expansion = atr_now > (atr_prev * 1.05)

    vol_now = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0.0
    vol_avg = float(df["volume"].tail(20).mean()) if "volume" in df.columns else 0.0
    volume_spike = vol_now > (vol_avg * 1.2) if vol_avg > 0 else False

    state = "normal"
    if width_now < width_avg_10:
        state = "compressed"
    elif width_now > (width_avg_20 * 1.12):
        state = "expanding"

    volatility_expansion = bool(width_now > width_prev and atr_expansion and volume_spike)

    return {
        "state": state,
        "volatility_expansion": volatility_expansion,
        "bb_width_now": round(width_now, 6),
        "bb_width_prev": round(width_prev, 6),
        "atr_expansion": atr_expansion,
        "volume_spike": volume_spike,
    }
