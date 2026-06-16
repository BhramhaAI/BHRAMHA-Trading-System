from __future__ import annotations

import os
import pandas as pd


def _win_rate_by_group(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns or df.empty:
        return pd.Series(dtype=float)
    grouped = df.groupby(col)["result"].apply(lambda s: (s.eq("WIN").sum() / len(s)) * 100)
    return grouped.sort_values(ascending=False)


def analyze_signal_quality(csv_path: str = "signals_log.csv"):
    if not os.path.isfile(csv_path):
        return {
            "best_coin": "N/A",
            "best_timeframe": "N/A",
            "best_regime": "N/A",
            "best_nakshatra": "N/A",
            "overall_win_rate": 0.0,
        }

    try:
        df = pd.read_csv(csv_path, on_bad_lines="skip")
    except Exception:
        return {
            "best_coin": "N/A",
            "best_timeframe": "N/A",
            "best_regime": "N/A",
            "best_nakshatra": "N/A",
            "overall_win_rate": 0.0,
        }

    df.columns = [str(c).lower().strip() for c in df.columns]

    if "result" not in df.columns:
        return {
            "best_coin": "N/A",
            "best_timeframe": "N/A",
            "best_regime": "N/A",
            "best_nakshatra": "N/A",
            "overall_win_rate": 0.0,
        }

    eval_df = df[df["result"].isin(["WIN", "LOSS"])].copy()
    if eval_df.empty:
        return {
            "best_coin": "N/A",
            "best_timeframe": "N/A",
            "best_regime": "N/A",
            "best_nakshatra": "N/A",
            "overall_win_rate": 0.0,
        }

    # Backward compatibility with legacy/new column names.
    if "emotion" not in eval_df.columns and "crowd_emotion" in eval_df.columns:
        eval_df["emotion"] = eval_df["crowd_emotion"]
    if "nakshatra" not in eval_df.columns and "nakshatra_bias" in eval_df.columns:
        eval_df["nakshatra"] = eval_df["nakshatra_bias"]

    coin_rates = _win_rate_by_group(eval_df, "coin")
    tf_rates = _win_rate_by_group(eval_df, "timeframe")
    regime_rates = _win_rate_by_group(eval_df, "regime")
    nakshatra_rates = _win_rate_by_group(eval_df, "nakshatra")
    _emotion_rates = _win_rate_by_group(eval_df, "emotion")

    total = len(eval_df)
    wins = eval_df["result"].eq("WIN").sum()
    overall_win_rate = round((wins / total) * 100, 2) if total > 0 else 0.0

    return {
        "best_coin": coin_rates.index[0] if not coin_rates.empty else "N/A",
        "best_timeframe": tf_rates.index[0] if not tf_rates.empty else "N/A",
        "best_regime": regime_rates.index[0] if not regime_rates.empty else "N/A",
        "best_nakshatra": nakshatra_rates.index[0] if not nakshatra_rates.empty else "N/A",
        "overall_win_rate": overall_win_rate,
    }
