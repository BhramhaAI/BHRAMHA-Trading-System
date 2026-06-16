"""
BHRAMHA - Enhanced Smart Exit Engine
Dynamic TP/SL with liquidity zone awareness,
ATR-based sizing, and risk/reward filter (min 1.8:1).
"""

import pandas as pd
import numpy as np
from binance_http import BINANCE_HTTP_TIMEOUT, session
from score_engine import detect_low_volume_nodes, find_lvn_between, LVN_SCORING_ENABLED
from config import (
    BASIS_ADJUSTMENT_ENABLED,
    BASIS_SL_BUFFER,
    DYNAMIC_SL_ENABLED,
    MAX_BASIS_PCT,
    MIN_RR,
)


MIN_RR_RATIO = MIN_RR   # minimum risk/reward to take a trade
ATR_SL_MULT  = 1.5      # SL = entry ± ATR * multiplier
ATR_TP_MULT  = 3.0      # TP = entry ± ATR * multiplier (adjusted by market regime)
LVN_TP_MIN_RR = 1.5


def fetch_spot_perp_prices(symbol: str) -> dict:
    spot_resp = session.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": symbol},
        timeout=BINANCE_HTTP_TIMEOUT,
    )
    spot_resp.raise_for_status()
    perp_resp = session.get(
        "https://fapi.binance.com/fapi/v1/ticker/price",
        params={"symbol": symbol},
        timeout=BINANCE_HTTP_TIMEOUT,
    )
    perp_resp.raise_for_status()
    spot_price = float(spot_resp.json()["price"])
    perp_price = float(perp_resp.json()["price"])
    basis_pct = ((perp_price - spot_price) / max(spot_price, 1e-9)) * 100.0
    return {
        "spot_price": spot_price,
        "perp_price": perp_price,
        "basis_pct": basis_pct,
    }


def apply_basis_adjustment(symbol: str, entry: float, sl: float, tp: float, direction: str) -> dict:
    if not BASIS_ADJUSTMENT_ENABLED:
        return {
            "basis_pct": 0.0,
            "adjusted_entry": entry,
            "adjusted_sl": sl,
            "adjusted_tp": tp,
            "blocked": False,
            "warning": False,
        }

    prices = fetch_spot_perp_prices(symbol)
    basis_pct = float(prices["basis_pct"])
    factor = 1.0 + (basis_pct / 100.0)
    adjusted_entry = float(prices["perp_price"])
    adjusted_sl = float(sl) * factor
    adjusted_tp = float(tp) * factor
    direction = str(direction).upper()
    if direction == "LONG":
        adjusted_sl *= (1.0 - BASIS_SL_BUFFER)
    else:
        adjusted_sl *= (1.0 + BASIS_SL_BUFFER)

    abs_basis = abs(basis_pct)
    return {
        "basis_pct": basis_pct,
        "spot_price": prices["spot_price"],
        "perp_price": prices["perp_price"],
        "adjusted_entry": adjusted_entry,
        "adjusted_sl": adjusted_sl,
        "adjusted_tp": adjusted_tp,
        "blocked": abs_basis > MAX_BASIS_PCT,
        "warning": abs_basis > 0.3,
    }


def _get_atr(df: pd.DataFrame, period: int = 14) -> float:
    for col in ("atr", "ATR"):
        if col in df.columns:
            val = df[col].iloc[-1]
            if pd.notna(val):
                return float(val)
    high  = pd.to_numeric(df["high"],  errors="coerce")
    low   = pd.to_numeric(df["low"],   errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(com=period - 1, adjust=False).mean().iloc[-1])


def detect_liquidity_zones(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Identify recent swing highs (resistance / sell-side liquidity)
    and swing lows (support / buy-side liquidity).
    """
    window = df.tail(lookback)
    high  = pd.to_numeric(window["high"],  errors="coerce")
    low   = pd.to_numeric(window["low"],   errors="coerce")
    close = pd.to_numeric(window["close"], errors="coerce")

    # Swing highs: bars where high is highest of 5-bar window
    swing_highs = []
    swing_lows  = []
    for i in range(2, len(window) - 2):
        if high.iloc[i] == high.iloc[i-2:i+3].max():
            swing_highs.append(float(high.iloc[i]))
        if low.iloc[i] == low.iloc[i-2:i+3].min():
            swing_lows.append(float(low.iloc[i]))

    current = float(close.iloc[-1])

    # Nearest resistance above current price
    resistance_levels = sorted([h for h in swing_highs if h > current])
    support_levels    = sorted([l for l in swing_lows  if l < current], reverse=True)

    return {
        "nearest_resistance": resistance_levels[0]  if resistance_levels else None,
        "second_resistance":  resistance_levels[1]  if len(resistance_levels) > 1 else None,
        "nearest_support":    support_levels[0]     if support_levels     else None,
        "second_support":     support_levels[1]     if len(support_levels) > 1 else None,
        "all_resistance":     resistance_levels[:5],
        "all_support":        support_levels[:5],
    }


def smart_stop_loss(
    df: pd.DataFrame,
    entry: float,
    direction: str,
    regime: str = "NORMAL",
    lunar_volatility: str = "NORMAL",
    crowd_phase: str = "BALANCED",
    nakshatra_type: str = "OTHER",
    hora_planet: str = "NEUTRAL",
) -> float:
    """
    Calculate a smart stop loss that:
    - Uses ATR for base distance
    - Widens slightly in VOLATILE regimes
    - Tightens in RANGING regimes
    - Avoids placing SL at obvious retail cluster levels
    """
    atr = _get_atr(df)
    lz  = detect_liquidity_zones(df)

    mult = ATR_SL_MULT
    if regime == "VOLATILE":
        mult = ATR_SL_MULT * 1.4
    elif regime == "RANGING":
        mult = ATR_SL_MULT * 0.9

    if direction == "LONG":
        raw_sl = entry - atr * mult
        # Push SL just below nearest support to avoid stop hunt
        sup = lz["nearest_support"]
        if sup and sup < entry and abs(raw_sl - sup) / entry < 0.005:
            raw_sl = sup * 0.998   # 0.2% below support
        adjusted_sl = raw_sl
    else:
        raw_sl = entry + atr * mult
        res = lz["nearest_resistance"]
        if res and res > entry and abs(raw_sl - res) / entry < 0.005:
            raw_sl = res * 1.002
        adjusted_sl = raw_sl

    if DYNAMIC_SL_ENABLED:
        direction = str(direction).upper()
        regime = str(regime).upper()
        lunar_volatility = str(lunar_volatility).upper()
        crowd_phase = str(crowd_phase).upper()
        nakshatra_type = str(nakshatra_type).upper()
        hora_planet = str(hora_planet).upper()

        sl_distance = abs(entry - raw_sl)
        distance_mult = 1.0
        reasons = []

        if (
            direction == "LONG"
            and lunar_volatility == "HIGH"
            and crowd_phase == "CONSOLIDATION"
            and regime == "NORMAL"
        ):
            distance_mult = 0.70
            reasons.append("HIGH lunar volatility + CONSOLIDATION + NORMAL LONG")
        elif nakshatra_type in {"UGRA", "TIKSHNA"} or hora_planet == "SATURN":
            distance_mult = 0.80
            if nakshatra_type in {"UGRA", "TIKSHNA"}:
                reasons.append(f"{nakshatra_type} Nakshatra")
            if hora_planet == "SATURN":
                reasons.append("Saturn hora")
        elif direction == "SHORT" and regime == "TRENDING_BEAR":
            distance_mult = 1.10
            reasons.append("TRENDING_BEAR SHORT")

        if distance_mult != 1.0:
            adjusted_distance = sl_distance * distance_mult
            if direction == "LONG":
                adjusted_sl = entry - adjusted_distance
            else:
                adjusted_sl = entry + adjusted_distance
            pct_change = int(round(abs(1.0 - distance_mult) * 100))
            action = "tightened" if distance_mult < 1.0 else "loosened"
            print(f"[SL-ADJUST] SL {action} by {pct_change}% due to {' + '.join(reasons)}")

    return round(adjusted_sl, 8)


def smart_take_profit(
    df: pd.DataFrame,
    entry: float,
    sl: float,
    direction: str,
    regime: str = "NORMAL",
    score: int = 90,
) -> dict:
    """
    Calculate tiered take profit levels (TP1, TP2, TP3).
    - TP1: conservative (1.5× risk) – partial exit
    - TP2: primary (2.5× risk)
    - TP3: extended (4× risk, only for trending regime + high score)

    Returns dict: {tp1, tp2, tp3, rr_tp1, rr_tp2, rr_tp3, passes_filter}
    """
    risk = abs(entry - sl)
    if risk == 0:
        return {"tp1": entry, "tp2": entry, "tp3": entry,
                "rr_tp1": 0, "rr_tp2": 0, "rr_tp3": 0, "passes_filter": False}

    lz = detect_liquidity_zones(df)
    lvns = detect_low_volume_nodes(df) if LVN_SCORING_ENABLED else []
    lvn_tp_used = False
    lvn_target = None

    if direction == "LONG":
        tp1_raw = entry + risk * 1.5
        tp2_raw = entry + risk * 2.5
        tp3_raw = entry + risk * 4.0

        # Pull back TP2 if it runs into resistance
        res1 = lz["nearest_resistance"]
        if res1 and res1 < tp2_raw and res1 > entry:
            tp2_raw = res1 * 0.998
            if abs(tp2_raw - entry) / max(entry, 1e-9) < 0.005:
                tp2_raw = entry + risk * 2.5

        res2 = lz["second_resistance"]
        if res2 and res2 < tp3_raw:
            tp3_raw = res2 * 0.998

        lvn_between = find_lvn_between(entry, tp2_raw, direction, lvns)
        if lvn_between:
            lvn_candidate = float(lvn_between["mid"])
            lvn_rr = abs(lvn_candidate - entry) / risk
            if lvn_rr >= LVN_TP_MIN_RR:
                tp2_raw = lvn_candidate
                lvn_target = lvn_candidate
                lvn_tp_used = True
    else:
        tp1_raw = entry - risk * 1.5
        tp2_raw = entry - risk * 2.5
        tp3_raw = entry - risk * 4.0

        sup1 = lz["nearest_support"]
        if sup1 and sup1 > tp2_raw and sup1 < entry:
            tp2_raw = sup1 * 1.002
            if abs(tp2_raw - entry) / max(entry, 1e-9) < 0.005:
                tp2_raw = entry - risk * 2.5

        sup2 = lz["second_support"]
        if sup2 and sup2 > tp3_raw and sup2 < entry:
            tp3_raw = sup2 * 1.002

        lvn_between = find_lvn_between(entry, tp2_raw, direction, lvns)
        if lvn_between:
            lvn_candidate = float(lvn_between["mid"])
            lvn_rr = abs(lvn_candidate - entry) / risk
            if lvn_rr >= LVN_TP_MIN_RR:
                tp2_raw = lvn_candidate
                lvn_target = lvn_candidate
                lvn_tp_used = True

    rr_tp1 = round(abs(tp1_raw - entry) / risk, 2)
    rr_tp2 = round(abs(tp2_raw - entry) / risk, 2)
    rr_tp3 = round(abs(tp3_raw - entry) / risk, 2)

    # Only allow trades that pass minimum R:R at TP2
    passes = rr_tp2 >= MIN_RR_RATIO

    # For high-score trending markets, use aggressive TP3
    use_tp3 = (regime == "TRENDING" and score >= 92)

    return {
        "tp1":          round(tp1_raw, 8),
        "tp2":          round(tp2_raw, 8),
        "tp3":          round(tp3_raw, 8) if use_tp3 else None,
        "rr_tp1":       rr_tp1,
        "rr_tp2":       rr_tp2,
        "rr_tp3":       rr_tp3 if use_tp3 else None,
        "risk":         round(risk, 8),
        "passes_filter": passes,
        "primary_tp":   round(tp2_raw, 8),
        "lvn_tp_used":  lvn_tp_used,
        "lvn_target":   round(lvn_target, 8) if lvn_target is not None else None,
    }


def calculate_position_size(
    account_balance: float,
    entry: float,
    sl: float,
    risk_pct: float = 1.0,    # % of account to risk per trade
) -> dict:
    """
    Calculate recommended position size using fixed fractional risk.
    risk_pct = 1.0 means risk 1% of account per trade.
    """
    risk_amount = account_balance * (risk_pct / 100)
    risk_per_unit = abs(entry - sl)
    if risk_per_unit == 0:
        return {"units": 0, "risk_amount": 0, "risk_pct": 0}
    units = risk_amount / risk_per_unit
    return {
        "units":       round(units, 4),
        "risk_amount": round(risk_amount, 2),
        "risk_pct":    risk_pct,
        "note":        f"Risk ${risk_amount:.2f} → {units:.4f} units",
    }


def calculate_scalp_tp1(entry: float, direction: str, pct: float = 0.003) -> float:
    try:
        if str(direction).upper() == "LONG":
            return round(entry * (1 + pct), 8)
        else:
            return round(entry * (1 - pct), 8)
    except Exception:
        return entry


def calculate_breakeven_sl(entry: float, direction: str, buffer_pct: float = 0.001) -> float:
    try:
        if str(direction).upper() == "LONG":
            return round(entry * (1 + buffer_pct), 8)
        else:
            return round(entry * (1 - buffer_pct), 8)
    except Exception:
        return entry


def apply_timeframe_tp_cap(entry: float, tp: float, 
                            direction: str, 
                            timeframe: str) -> float:
    try:
        caps = {
            "5m":  0.15,
            "15m": 0.08,
            "1h":  0.06,
            "4h":  0.05,
        }
        cap_pct = caps.get(str(timeframe).lower(), 0.08)
        
        if str(direction).upper() == "LONG":
            capped_tp = entry * (1 + cap_pct)
            return min(tp, capped_tp)
        else:
            capped_tp = entry * (1 - cap_pct)
            return max(tp, capped_tp)
    except Exception:
        return tp
