def get_trend(df):

    ema50 = df["ema50"].iloc[-1]
    price = df["close"].iloc[-1]

    if price > ema50:
        return "UP"

    if price < ema50:
        return "DOWN"

    return "SIDEWAYS"