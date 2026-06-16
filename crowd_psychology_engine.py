from __future__ import annotations


def analyze_crowd_psychology(df):
    """
    Estimate collective trader emotion and crowd phase
    from RSI, volume behavior, ATR trend, and trend strength.
    """
    import pandas as pd

    data = df.copy()

    rsi = float(pd.to_numeric(data["rsi"], errors="coerce").iloc[-1])
    volume = float(pd.to_numeric(data["volume"], errors="coerce").iloc[-1])
    avg_volume = float(pd.to_numeric(data["volume"], errors="coerce").tail(20).mean())

    atr_series = pd.to_numeric(data["atr"], errors="coerce")
    atr_now = float(atr_series.iloc[-1])
    atr_prev = float(atr_series.iloc[-2]) if len(atr_series) > 1 else atr_now
    atr_rising = atr_now > atr_prev

    close = pd.to_numeric(data["close"], errors="coerce")
    ema50 = pd.to_numeric(data["ema50"], errors="coerce")
    trend_strength = abs(float(close.iloc[-1] - ema50.iloc[-1])) / max(atr_now, 1e-9)

    volume_spike = volume > (avg_volume * 1.2)

    emotion = "NEUTRAL"
    crowd_phase = "BALANCED"
    modifier = 1.0

    if rsi < 25 and volume_spike:
        emotion = "PANIC"
        crowd_phase = "CAPITULATION"
        modifier = 1.25
    elif rsi < 35:
        emotion = "FEAR"
        crowd_phase = "RISK_OFF"
        modifier = 1.12
    elif 35 <= rsi <= 60:
        emotion = "NEUTRAL"
        crowd_phase = "CONSOLIDATION"
        modifier = 1.0
    elif rsi > 80 and atr_rising:
        emotion = "EUPHORIA"
        crowd_phase = "BLOW_OFF"
        modifier = 1.3
    elif rsi > 65:
        emotion = "GREED"
        crowd_phase = "RISK_ON"
        modifier = 1.15

    if trend_strength > 2 and emotion in {"GREED", "EUPHORIA"}:
        crowd_phase = "MOMENTUM_CHASE"
    elif trend_strength < 0.6 and emotion in {"FEAR", "NEUTRAL"}:
        crowd_phase = "HESITATION"

    return {
        "emotion": emotion,
        "crowd_phase": crowd_phase,
        "modifier": modifier,
    }
