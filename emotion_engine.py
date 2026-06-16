def get_market_emotion(df):

    rsi = df["rsi"].iloc[-1]
    volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].mean()

    atr = df["atr"].iloc[-1]

    emotion = "Neutral"
    modifier = 1

    # Panic selling
    if rsi < 25 and volume > avg_volume:
        emotion = "Panic"
        modifier = 1.3

    # Fear
    elif rsi < 35:
        emotion = "Fear"
        modifier = 1.15

    # Greed
    elif rsi > 65:
        emotion = "Greed"
        modifier = 1.15

    # FOMO
    if rsi > 75 and volume > avg_volume:
        emotion = "FOMO"
        modifier = 1.25

    return {
        "emotion": emotion,
        "modifier": modifier
    }