from __future__ import annotations


def detect_overextension(data):
    df = data.copy()
    if len(df) < 25:
        return {
            "overextended": False,
            "score_boost": 0,
            "conditions_met": 0,
            "strong_reversal": False,
        }

    last = df.iloc[-1]
    rsi = float(last.get("rsi", 50))
    close = float(last["close"])
    atr = float(last.get("atr", 0))
    vwap = float(last.get("vwap", close))
    bb_upper = float(last.get("bb_upper", close))
    candle_size = float(last["high"] - last["low"])

    outside_upper_bb = close >= bb_upper
    vwap_stretch = close > (vwap * 1.02)
    large_candle = candle_size > (atr * 1.5) if atr > 0 else False
    high_rsi = rsi > 75

    conditions = [high_rsi, outside_upper_bb, vwap_stretch, large_candle]
    conditions_met = sum(1 for x in conditions if x)
    overextended = conditions_met >= 2
    strong_reversal = high_rsi and outside_upper_bb

    score_boost = 0
    if overextended:
        score_boost = 6 if strong_reversal else 4

    return {
        "overextended": overextended,
        "score_boost": score_boost,
        "conditions_met": conditions_met,
        "strong_reversal": strong_reversal,
        "high_rsi": high_rsi,
        "outside_upper_bb": outside_upper_bb,
        "vwap_stretch": vwap_stretch,
        "large_candle": large_candle,
    }
