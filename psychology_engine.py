def psychology_score(df):

    rsi = df["rsi"].iloc[-1]
    volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].mean()

    score = 50

    # Fear / Greed model
    if rsi > 70:
        score += 20

    if rsi < 30:
        score -= 20

    # Crowd pressure
    if volume > avg_volume:
        score += 10
    else:
        score -= 5

    return score