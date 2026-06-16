"""Order block and fair value gap analysis for BHRAMHA."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal

import pandas as pd


logger = logging.getLogger("bhramha.orderblock")

OB_IMPULSE_ATR_MULT = 1.5
OB_LOOKBACK = 100
FVG_MIN_SIZE_ATR_PCT = 0.3
FVG_LOOKBACK = 50
MAX_OB_AGE = 80
PROXIMITY_ATR_MULT = 0.5


@dataclass(slots=True)
class OrderBlock:
    """Represents a detected order block."""

    kind: Literal["bull", "bear"]
    top: float
    bottom: float
    mid: float
    bar_index: int
    strength: float
    mitigated: bool = False


@dataclass(slots=True)
class FairValueGap:
    """Represents a detected fair value gap."""

    kind: Literal["bull", "bear"]
    top: float
    bottom: float
    mid: float
    bar_index: int
    filled: bool = False


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return a sanitized OHLC dataframe with ATR."""
    frame = df.copy()
    for col in ["open", "high", "low", "close"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    prev_close = frame["close"].shift(1)
    tr = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_14"] = tr.ewm(com=13, adjust=False).mean()
    return frame


def detect_order_blocks(df: pd.DataFrame) -> list[OrderBlock]:
    """Detect bullish and bearish order blocks from recent price action."""
    try:
        frame = _prepare_df(df)
        if len(frame) < 5:
            return []

        start = max(1, len(frame) - OB_LOOKBACK)
        current_close = float(frame["close"].iloc[-1])
        latest_index = len(frame) - 1
        blocks: list[OrderBlock] = []

        for i in range(start, len(frame)):
            atr = float(frame["atr_14"].iloc[i])
            if atr <= 0:
                continue

            candle = frame.iloc[i]
            prev = frame.iloc[i - 1]
            body = abs(float(candle["close"]) - float(candle["open"]))
            if body <= (OB_IMPULSE_ATR_MULT * atr):
                continue

            age = latest_index - (i - 1)
            if age > MAX_OB_AGE:
                continue

            if float(candle["close"]) > float(candle["open"]) and float(prev["close"]) < float(prev["open"]):
                top = float(prev["high"])
                bottom = float(prev["low"])
                blocks.append(
                    OrderBlock(
                        kind="bull",
                        top=top,
                        bottom=bottom,
                        mid=(top + bottom) / 2.0,
                        bar_index=i - 1,
                        strength=round(body / max(atr, 1e-9), 2),
                        mitigated=current_close < bottom,
                    )
                )
            elif float(candle["close"]) < float(candle["open"]) and float(prev["close"]) > float(prev["open"]):
                top = float(prev["high"])
                bottom = float(prev["low"])
                blocks.append(
                    OrderBlock(
                        kind="bear",
                        top=top,
                        bottom=bottom,
                        mid=(top + bottom) / 2.0,
                        bar_index=i - 1,
                        strength=round(body / max(atr, 1e-9), 2),
                        mitigated=current_close > top,
                    )
                )

        blocks.sort(key=lambda item: item.bar_index, reverse=True)
        return blocks
    except Exception as exc:
        logger.exception("Failed to detect order blocks: %s", exc)
        return []


def detect_fvg(df: pd.DataFrame) -> list[FairValueGap]:
    """Detect bullish and bearish fair value gaps in recent candles."""
    try:
        frame = _prepare_df(df)
        if len(frame) < 3:
            return []

        start = max(2, len(frame) - FVG_LOOKBACK)
        current_close = float(frame["close"].iloc[-1])
        gaps: list[FairValueGap] = []

        for i in range(start, len(frame)):
            atr = float(frame["atr_14"].iloc[i])
            if atr <= 0:
                continue

            left = frame.iloc[i - 2]
            right = frame.iloc[i]

            if float(left["high"]) < float(right["low"]):
                bottom = float(left["high"])
                top = float(right["low"])
                if (top - bottom) > (FVG_MIN_SIZE_ATR_PCT * atr):
                    gaps.append(
                        FairValueGap(
                            kind="bull",
                            top=top,
                            bottom=bottom,
                            mid=(top + bottom) / 2.0,
                            bar_index=i,
                            filled=bottom <= current_close <= top,
                        )
                    )
            elif float(left["low"]) > float(right["high"]):
                bottom = float(right["high"])
                top = float(left["low"])
                if (top - bottom) > (FVG_MIN_SIZE_ATR_PCT * atr):
                    gaps.append(
                        FairValueGap(
                            kind="bear",
                            top=top,
                            bottom=bottom,
                            mid=(top + bottom) / 2.0,
                            bar_index=i,
                            filled=bottom <= current_close <= top,
                        )
                    )

        gaps.sort(key=lambda item: item.bar_index, reverse=True)
        return gaps
    except Exception as exc:
        logger.exception("Failed to detect FVGs: %s", exc)
        return []


def analyze_ob_fvg(df: pd.DataFrame, direction: str) -> dict:
    """Analyze order-block and FVG confluence for the requested direction."""
    try:
        frame = _prepare_df(df)
        if frame.empty:
            return {
                "ob_score": 0,
                "fvg_score": 0,
                "combined_score": 0,
                "nearest_bull_ob": None,
                "nearest_bear_ob": None,
                "nearest_bull_fvg": None,
                "nearest_bear_fvg": None,
                "price_in_ob": False,
                "price_in_fvg": False,
                "ob_count": 0,
                "fvg_count": 0,
                "signal_note": "Institutional context unavailable",
            }

        direction = str(direction).upper()
        current_price = float(frame["close"].iloc[-1])
        atr = float(frame["atr_14"].iloc[-1])
        proximity = PROXIMITY_ATR_MULT * atr
        order_blocks = detect_order_blocks(frame)
        fvgs = detect_fvg(frame)

        unmitigated_obs = [ob for ob in order_blocks if not ob.mitigated]
        unfilled_fvgs = [gap for gap in fvgs if not gap.filled]

        bull_obs = [ob for ob in order_blocks if ob.kind == "bull"]
        bear_obs = [ob for ob in order_blocks if ob.kind == "bear"]
        bull_fvgs = [gap for gap in fvgs if gap.kind == "bull"]
        bear_fvgs = [gap for gap in fvgs if gap.kind == "bear"]

        fresh_bull_obs = [ob for ob in unmitigated_obs if ob.kind == "bull"]
        fresh_bear_obs = [ob for ob in unmitigated_obs if ob.kind == "bear"]
        fresh_bull_fvgs = [gap for gap in unfilled_fvgs if gap.kind == "bull"]
        fresh_bear_fvgs = [gap for gap in unfilled_fvgs if gap.kind == "bear"]

        nearest_bull_ob = min(fresh_bull_obs if fresh_bull_obs else bull_obs, key=lambda ob: abs(current_price - ob.mid), default=None)
        nearest_bear_ob = min(fresh_bear_obs if fresh_bear_obs else bear_obs, key=lambda ob: abs(current_price - ob.mid), default=None)
        nearest_bull_fvg = min(fresh_bull_fvgs if fresh_bull_fvgs else bull_fvgs, key=lambda gap: abs(current_price - gap.mid), default=None)
        nearest_bear_fvg = min(fresh_bear_fvgs if fresh_bear_fvgs else bear_fvgs, key=lambda gap: abs(current_price - gap.mid), default=None)

        matching_obs = [ob for ob in unmitigated_obs if (direction == "LONG" and ob.kind == "bull") or (direction == "SHORT" and ob.kind == "bear")]
        matching_fvgs = [gap for gap in unfilled_fvgs if (direction == "LONG" and gap.kind == "bull") or (direction == "SHORT" and gap.kind == "bear")]
        nearest_matching_ob = min(matching_obs, key=lambda ob: abs(current_price - ob.mid), default=None)
        nearest_matching_fvg = min(matching_fvgs, key=lambda gap: abs(current_price - gap.mid), default=None)

        price_in_ob = False
        price_in_fvg = False
        ob_score = 0
        fvg_score = 0

        if nearest_matching_ob is not None:
            price_in_ob = nearest_matching_ob.bottom <= current_price <= nearest_matching_ob.top
            if price_in_ob or abs(current_price - nearest_matching_ob.mid) < proximity:
                ob_score = 20
            else:
                ob_score = 10
        elif any(((direction == "LONG" and ob.kind == "bull") or (direction == "SHORT" and ob.kind == "bear")) for ob in order_blocks):
            ob_score = 5

        if nearest_matching_fvg is not None:
            price_in_fvg = nearest_matching_fvg.bottom <= current_price <= nearest_matching_fvg.top
            if price_in_fvg:
                fvg_score = 20
            elif abs(current_price - nearest_matching_fvg.mid) < proximity:
                fvg_score = 15
            else:
                fvg_score = 0
        elif any(((direction == "LONG" and gap.kind == "bull") or (direction == "SHORT" and gap.kind == "bear")) for gap in fvgs):
            fvg_score = 5

        signal_note = (
            f"{direction} setup: {len(matching_obs)} matching OBs and {len(matching_fvgs)} matching FVGs near price."
            if matching_obs or matching_fvgs
            else f"{direction} setup: no fresh institutional zones near price."
        )

        return {
            "ob_score": int(ob_score),
            "fvg_score": int(fvg_score),
            "combined_score": int(ob_score + fvg_score),
            "nearest_bull_ob": nearest_bull_ob,
            "nearest_bear_ob": nearest_bear_ob,
            "nearest_bull_fvg": nearest_bull_fvg,
            "nearest_bear_fvg": nearest_bear_fvg,
            "price_in_ob": bool(price_in_ob),
            "price_in_fvg": bool(price_in_fvg),
            "ob_count": len(unmitigated_obs),
            "fvg_count": len(unfilled_fvgs),
            "signal_note": signal_note,
        }
    except Exception as exc:
        logger.exception("Failed to analyze OB/FVG: %s", exc)
        return {
            "ob_score": 0,
            "fvg_score": 0,
            "combined_score": 0,
            "nearest_bull_ob": None,
            "nearest_bear_ob": None,
            "nearest_bull_fvg": None,
            "nearest_bear_fvg": None,
            "price_in_ob": False,
            "price_in_fvg": False,
            "ob_count": 0,
            "fvg_count": 0,
            "signal_note": "Institutional context unavailable",
        }


def format_ob_fvg_for_telegram(result: dict, direction: str) -> str:
    """Format OB/FVG analysis for Telegram."""
    try:
        direction = str(direction).upper()
        label = "Bull" if direction == "LONG" else "Bear"
        if result.get("price_in_ob"):
            ob_text = f"🧱 OB: INSIDE {label.upper()} OB (score: {int(result.get('ob_score', 0))}/30)"
        elif int(result.get("ob_score", 0)) >= 10:
            ob_text = f"🧱 OB: Approaching {label} OB (score: {int(result.get('ob_score', 0))}/30)"
        elif int(result.get("ob_score", 0)) > 0:
            ob_text = f"🧱 OB: Only mitigated {label} OBs found (score: {int(result.get('ob_score', 0))}/30)"
        else:
            ob_text = "🧱 OB: No relevant order block"

        if result.get("price_in_fvg"):
            fvg_text = f"📐 FVG: Inside {label} Gap (score: {int(result.get('fvg_score', 0))}/20)"
        elif int(result.get("fvg_score", 0)) >= 15:
            fvg_text = f"📐 FVG: Approaching {label} Gap (score: {int(result.get('fvg_score', 0))}/20)"
        elif int(result.get("fvg_score", 0)) > 0:
            fvg_text = f"📐 FVG: {label} gap seen but already filled (score: {int(result.get('fvg_score', 0))}/20)"
        else:
            fvg_text = "📐 FVG: No relevant gap"

        combined_score = int(result.get("combined_score", 0))
        if combined_score >= 35:
            confluence = "HIGH"
        elif combined_score >= 20:
            confluence = "MODERATE"
        else:
            confluence = "LOW"

        return f"{ob_text}\n{fvg_text}\n💡 Institutional confluence: {confluence}"
    except Exception as exc:
        logger.exception("Failed to format OB/FVG text: %s", exc)
        return "🧱 OB/FVG: unavailable"
