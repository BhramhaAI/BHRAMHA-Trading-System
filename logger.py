import datetime
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "signals_log.csv")
REJECTED_CSV_PATH = os.path.join(BASE_DIR, "rejected_log.csv")
COLUMNS = [
    "time",
    "session",
    "signal_type",
    "signal_quality",
    "coin",
    "timeframe",
    "direction",
    "entry",
    "tp",
    "sl",
    "basis_pct",
    "ema_trend",
    "macd",
    "volume",
    "adx",
    "momentum",
    "ob_fvg",
    "crowd_emotion",
    "crowd_phase",
    "regime",
    "liquidity_event",
    "nakshatra_name",
    "nakshatra_type",
    "nakshatra_bias",
    "nakshatra_psychology",
    "stop_hunt_risk",
    "planetary_bias",
    "volatility_bias",
    "sun_sign",
    "sun_sign_bias",
    "sun_transit_effect",
    "hora_planet",
    "tithi_group",
    "abhijit_active",
    "reversal_warning",
    "vedic_time",
    "timing_quality",
    "vedic_block",
    "lunar_volatility",
    "lunar_psychology",
    "pump_probability",
    "orderflow_pressure",
    "volume_accumulation",
    "oi_buildup",
    "result",
    "max_price_reached",
    "min_price_reached",
    "mfe_percent",
    "mae_percent",
    "insight",
]


def _load_log_dataframe(csv_path: str = CSV_PATH) -> pd.DataFrame:
    if os.path.isfile(csv_path):
        try:
            df = pd.read_csv(csv_path, on_bad_lines="skip")
        except Exception:
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    df.columns = [str(c).lower().strip() for c in df.columns]

    if "tf" in df.columns and "timeframe" not in df.columns:
        df["timeframe"] = df["tf"]

    for col in COLUMNS:
        if col not in df.columns:
            if col == "result":
                df[col] = "OPEN"
            elif col == "signal_type":
                df[col] = "TREND"
            elif col == "signal_quality":
                df[col] = "C"
            elif col == "regime":
                df[col] = "NORMAL"
            elif col == "session":
                df[col] = "UNKNOWN"
            elif col == "crowd_emotion":
                df[col] = "NEUTRAL"
            elif col == "crowd_phase":
                df[col] = "BALANCED"
            elif col == "liquidity_event":
                df[col] = "None"
            elif col == "nakshatra_name":
                df[col] = "UNKNOWN"
            elif col == "nakshatra_type":
                df[col] = "OTHER"
            elif col == "nakshatra_bias":
                df[col] = "NEUTRAL"
            elif col == "nakshatra_psychology":
                df[col] = "balanced"
            elif col == "stop_hunt_risk":
                df[col] = "NORMAL"
            elif col == "planetary_bias":
                df[col] = "NEUTRAL"
            elif col == "volatility_bias":
                df[col] = "NORMAL"
            elif col == "sun_sign":
                df[col] = "UNKNOWN"
            elif col == "sun_sign_bias":
                df[col] = "NEUTRAL"
            elif col == "sun_transit_effect":
                df[col] = "neutral"
            elif col == "hora_planet":
                df[col] = "NEUTRAL"
            elif col == "tithi_group":
                df[col] = "UNKNOWN"
            elif col == "abhijit_active":
                df[col] = False
            elif col == "reversal_warning":
                df[col] = False
            elif col == "vedic_time":
                df[col] = "NORMAL"
            elif col == "timing_quality":
                df[col] = "NEUTRAL"
            elif col == "vedic_block":
                df[col] = False
            elif col == "lunar_volatility":
                df[col] = "NORMAL"
            elif col == "lunar_psychology":
                df[col] = "Balanced sentiment with moderate volatility."
            elif col == "pump_probability":
                df[col] = "LOW"
            elif col == "orderflow_pressure":
                df[col] = "NEUTRAL"
            elif col in {"volume_accumulation", "oi_buildup"}:
                df[col] = False
            elif col in {"ema_trend", "macd", "volume", "adx", "momentum", "ob_fvg"}:
                df[col] = 0
            elif col == "basis_pct":
                df[col] = 0.0
            elif col in {"mfe_percent", "mae_percent"}:
                df[col] = ""
            else:
                df[col] = ""

    df["signal_type"] = df["signal_type"].fillna("").astype(str).str.upper()
    df.loc[df["signal_type"].isin(["", "NAN", "NONE"]), "signal_type"] = "TREND"
    df["signal_quality"] = df["signal_quality"].fillna("").astype(str).str.upper()
    df.loc[df["signal_quality"].isin(["", "NAN", "NONE"]), "signal_quality"] = "C"
    df["basis_pct"] = pd.to_numeric(df["basis_pct"], errors="coerce").fillna(0.0)

    df["result"] = df["result"].fillna("").astype(str).str.upper()
    df.loc[df["result"].isin(["", "NAN", "NONE"]), "result"] = "OPEN"

    df["regime"] = df["regime"].fillna("").astype(str).str.upper()
    df.loc[df["regime"].isin(["", "NAN", "NONE"]), "regime"] = "NORMAL"
    df["crowd_emotion"] = df["crowd_emotion"].fillna("").astype(str).str.upper()
    df.loc[df["crowd_emotion"].isin(["", "NAN", "NONE"]), "crowd_emotion"] = "NEUTRAL"
    df["crowd_phase"] = df["crowd_phase"].fillna("").astype(str).str.upper()
    df.loc[df["crowd_phase"].isin(["", "NAN", "NONE"]), "crowd_phase"] = "BALANCED"

    df["liquidity_event"] = df["liquidity_event"].fillna("").astype(str)
    df.loc[df["liquidity_event"].isin(["", "nan", "None", "NAN", "NONE"]), "liquidity_event"] = "None"
    df["nakshatra_name"] = df["nakshatra_name"].fillna("").astype(str)
    df.loc[df["nakshatra_name"].isin(["", "nan", "None", "NAN", "NONE"]), "nakshatra_name"] = "UNKNOWN"
    df["nakshatra_type"] = df["nakshatra_type"].fillna("").astype(str).str.upper()
    df.loc[df["nakshatra_type"].isin(["", "NAN", "NONE"]), "nakshatra_type"] = "OTHER"

    df["nakshatra_bias"] = df["nakshatra_bias"].fillna("").astype(str).str.upper()
    df.loc[df["nakshatra_bias"].isin(["", "NAN", "NONE"]), "nakshatra_bias"] = "NEUTRAL"

    df["nakshatra_psychology"] = df["nakshatra_psychology"].fillna("").astype(str)
    df.loc[df["nakshatra_psychology"].isin(["", "nan", "None", "NAN", "NONE"]), "nakshatra_psychology"] = "balanced"
    df["stop_hunt_risk"] = df["stop_hunt_risk"].fillna("").astype(str).str.upper()
    df.loc[df["stop_hunt_risk"].isin(["", "NAN", "NONE"]), "stop_hunt_risk"] = "NORMAL"

    df["planetary_bias"] = df["planetary_bias"].fillna("").astype(str).str.upper()
    df.loc[df["planetary_bias"].isin(["", "NAN", "NONE"]), "planetary_bias"] = "NEUTRAL"

    df["volatility_bias"] = df["volatility_bias"].fillna("").astype(str).str.upper()
    df.loc[df["volatility_bias"].isin(["", "NAN", "NONE"]), "volatility_bias"] = "NORMAL"
    df["sun_sign"] = df["sun_sign"].fillna("").astype(str).str.upper()
    df.loc[df["sun_sign"].isin(["", "NAN", "NONE"]), "sun_sign"] = "UNKNOWN"
    df["sun_sign_bias"] = df["sun_sign_bias"].fillna("").astype(str).str.upper()
    df.loc[df["sun_sign_bias"].isin(["", "NAN", "NONE"]), "sun_sign_bias"] = "NEUTRAL"
    df["sun_transit_effect"] = df["sun_transit_effect"].fillna("").astype(str)
    df.loc[df["sun_transit_effect"].isin(["", "nan", "None", "NAN", "NONE"]), "sun_transit_effect"] = "neutral"
    df["hora_planet"] = df["hora_planet"].fillna("").astype(str).str.upper()
    df.loc[df["hora_planet"].isin(["", "NAN", "NONE"]), "hora_planet"] = "NEUTRAL"
    df["tithi_group"] = df["tithi_group"].fillna("").astype(str).str.upper()
    df.loc[df["tithi_group"].isin(["", "NAN", "NONE"]), "tithi_group"] = "UNKNOWN"
    df["abhijit_active"] = df["abhijit_active"].fillna(False).astype(bool)
    df["reversal_warning"] = df["reversal_warning"].fillna(False).astype(bool)
    df["vedic_time"] = df["vedic_time"].fillna("").astype(str).str.upper()
    df.loc[df["vedic_time"].isin(["", "NAN", "NONE"]), "vedic_time"] = "NORMAL"
    df["timing_quality"] = df["timing_quality"].fillna("").astype(str).str.upper()
    df.loc[df["timing_quality"].isin(["", "NAN", "NONE"]), "timing_quality"] = "NEUTRAL"
    df["vedic_block"] = df["vedic_block"].fillna(False).astype(bool)
    df["lunar_volatility"] = df["lunar_volatility"].fillna("").astype(str).str.upper()
    df.loc[df["lunar_volatility"].isin(["", "NAN", "NONE"]), "lunar_volatility"] = "NORMAL"
    df["lunar_psychology"] = df["lunar_psychology"].fillna("").astype(str)
    df.loc[df["lunar_psychology"].isin(["", "nan", "None", "NAN", "NONE"]), "lunar_psychology"] = (
        "Balanced sentiment with moderate volatility."
    )
    df["pump_probability"] = df["pump_probability"].fillna("").astype(str).str.upper()
    df.loc[df["pump_probability"].isin(["", "NAN", "NONE"]), "pump_probability"] = "LOW"
    df["orderflow_pressure"] = df["orderflow_pressure"].fillna("").astype(str).str.upper()
    df.loc[df["orderflow_pressure"].isin(["", "NAN", "NONE"]), "orderflow_pressure"] = "NEUTRAL"
    df["insight"] = df["insight"].fillna("").astype(str)

    return df[COLUMNS]


def get_trading_session(utc_time=None):
    try:
        from datetime import datetime
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        if utc_time is None:
            now = datetime.now(ist)
        else:
            now = utc_time.astimezone(ist)
        hour = now.hour
        if 6 <= hour < 10:
            return "ASIA_OPEN"
        elif 10 <= hour < 14:
            return "ASIA_LONDON"
        elif 14 <= hour < 20:
            return "LONDON"
        elif 20 <= hour < 24:
            return "NY_OPEN"
        else:
            return "NY_LATE"
    except:
        return "UNKNOWN"


def _build_signal_row(data):
    entry = data.get("entry", "")

    return {
        "time": datetime.datetime.utcnow(),
        "session": get_trading_session(),
        "signal_type": str(data.get("signal_type", "TREND")).upper(),
        "signal_quality": str(data.get("signal_quality", "C")).upper(),
        "coin": data.get("coin", ""),
        "timeframe": data.get("timeframe", data.get("tf", "")),
        "direction": data.get("direction", ""),
        "entry": entry,
        "tp": data.get("tp", ""),
        "sl": data.get("sl", ""),
        "basis_pct": data.get("basis_pct", 0.0),
        "ema_trend": data.get("ema_trend", 0),
        "macd": data.get("macd", 0),
        "volume": data.get("volume", 0),
        "adx": data.get("adx", 0),
        "momentum": data.get("momentum", 0),
        "ob_fvg": data.get("ob_fvg", 0),
        "crowd_emotion": str(data.get("crowd_emotion", data.get("emotion", "NEUTRAL"))).upper(),
        "crowd_phase": str(data.get("crowd_phase", "BALANCED")).upper(),
        "regime": str(data.get("regime", "NORMAL")).upper(),
        "liquidity_event": str(data.get("liquidity_event", "None")),
        "nakshatra_name": str(data.get("nakshatra_name", data.get("nakshatra", "UNKNOWN"))),
        "nakshatra_type": str(data.get("nakshatra_type", "OTHER")).upper(),
        "nakshatra_bias": str(data.get("nakshatra_bias", "NEUTRAL")).upper(),
        "nakshatra_psychology": str(data.get("nakshatra_psychology", "balanced")),
        "stop_hunt_risk": str(data.get("stop_hunt_risk", "NORMAL")).upper(),
        "planetary_bias": str(data.get("planetary_bias", "NEUTRAL")).upper(),
        "volatility_bias": str(data.get("volatility_bias", "NORMAL")).upper(),
        "sun_sign": str(data.get("sun_sign", "UNKNOWN")).upper(),
        "sun_sign_bias": str(data.get("sun_sign_bias", "NEUTRAL")).upper(),
        "sun_transit_effect": str(data.get("sun_transit_effect", "neutral")),
        "hora_planet": str(data.get("hora_planet", "NEUTRAL")).upper(),
        "tithi_group": str(data.get("tithi_group", "UNKNOWN")).upper(),
        "abhijit_active": bool(data.get("abhijit_active", False)),
        "reversal_warning": bool(data.get("reversal_warning", False)),
        "vedic_time": str(data.get("vedic_time", "NORMAL")).upper(),
        "timing_quality": str(data.get("timing_quality", "NEUTRAL")).upper(),
        "vedic_block": bool(data.get("vedic_block", False)),
        "lunar_volatility": str(data.get("lunar_volatility", "NORMAL")).upper(),
        "lunar_psychology": str(data.get("lunar_psychology", "Balanced sentiment with moderate volatility.")),
        "pump_probability": str(data.get("pump_probability", "LOW")).upper(),
        "orderflow_pressure": str(data.get("orderflow_pressure", "NEUTRAL")).upper(),
        "volume_accumulation": bool(data.get("volume_accumulation", False)),
        "oi_buildup": bool(data.get("oi_buildup", False)),
        "result": str(data.get("result", "OPEN")).upper(),
        "max_price_reached": data.get("max_price_reached", entry),
        "min_price_reached": data.get("min_price_reached", entry),
        "mfe_percent": "",
        "mae_percent": "",
        "insight": str(data.get("insight", "")),
        "close_time": "",
    }


def log_signal(data):
    result = str(data.get("result", "OPEN")).upper()
    if result == "REJECTED":
        log_rejected_signal(data)
        return

    df = _load_log_dataframe(CSV_PATH)
    row = _build_signal_row(data)

    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(CSV_PATH, index=False)


def log_rejected_signal(data):
    df = _load_log_dataframe(REJECTED_CSV_PATH)
    row = _build_signal_row(data)

    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(REJECTED_CSV_PATH, index=False)
