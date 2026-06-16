import pandas as pd

def analyze_signals():

    try:
        df = pd.read_csv("signals_log.csv")
    except:
        return None

    stats = {}

    stats["total_signals"] = len(df)

    if "result" in df.columns:

        wins = df[df["result"] == "WIN"]

        stats["win_rate"] = round(len(wins) / len(df) * 100, 2)

    return stats