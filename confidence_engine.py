def calculate_confidence(score):

    # normalize score
    confidence = min(max(score, 0), 100)

    if confidence >= 95:
        probability = "VERY HIGH"
        risk = "LOW"
        strength = "EXTREME"

    elif confidence >= 90:
        probability = "VERY HIGH"
        risk = "LOW"
        strength = "STRONG"

    elif confidence >= 85:
        probability = "HIGH"
        risk = "MEDIUM"
        strength = "GOOD"

    else:
        probability = "LOW"
        risk = "HIGH"
        strength = "WEAK"

    return {
        "confidence": round(confidence, 2),
        "probability": probability,
        "risk": risk,
        "strength": strength
    }
