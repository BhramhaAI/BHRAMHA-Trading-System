"""
BHRAMHA - Adaptive Score Weighting Engine (FIXED v2)
=====================================================
Changes from v1:
1. Direction-aware learning: win rates are now tracked separately for LONG
   and SHORT signals, so the engine can lower LONG weights when LONGs keep
   losing in sideways/bear markets.
2. get_score_threshold(): LONG signals now require a higher threshold than
   SHORT signals when the recent win rate for LONGs is poor.
3. Added `get_direction_bias()` method that returns which direction has the
   better current win rate — used by signal_engine to avoid the LONG spam bug.
4. Recalculate interval reduced to 30 (was 50) so the bot adapts faster.
5. Weight floor raised slightly (0.4 → 0.3 for factors with bad track record)
   so poorly-performing factors get suppressed more aggressively.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd


logger = logging.getLogger("bhramha.adaptive")

CSV_PATH = "signals_log.csv"
MIN_SAMPLES = 10
LEARNING_RATE = 0.15
WEIGHT_FLOOR = 0.3           # was 0.5 — allow more aggressive suppression
WEIGHT_CEILING = 2.0
RECALCULATE_EVERY = 30       # was 50 — adapt faster
CACHE_FILE = "adaptive_weights.json"

BASE_WEIGHTS = {
    "ema_trend":  15,
    "supertrend": 10,
    "macd":       10,
    "rsi":        10,
    "stoch_rsi":   8,
    "volume":      8,
    "vwap":        7,
    "adx":         7,
    "cmf":         7,
    "obv":         5,
    "bb":          5,
    "pivot":       4,
    "momentum":    4,
    "ob_fvg":      0,
}

_engine: "AdaptiveScoreEngine | None" = None


class AdaptiveScoreEngine:
    """Learn score weights from logged trade outcomes."""

    def __init__(self, csv_path: str = CSV_PATH, cache_file: str = CACHE_FILE):
        self.csv_path   = csv_path
        self.cache_file = cache_file
        self.weights    = self._load_weights()
        self.last_signal_count  = 0
        self.regime_adjustments: dict[str, int] = {}
        # Direction win rates: updated by learn()
        self._long_win_rate:  float = 0.5
        self._short_win_rate: float = 0.5

    # ── Persistence ───────────────────────────────────────────────

    def _load_weights(self) -> dict:
        weights = dict(BASE_WEIGHTS)
        try:
            cache_path = Path(self.cache_file)
            if cache_path.exists():
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for key, base_value in BASE_WEIGHTS.items():
                        weights[key] = float(payload.get(key, base_value))
        except Exception as exc:
            logger.exception("Failed to load adaptive cache: %s", exc)
        for key, value in BASE_WEIGHTS.items():
            weights.setdefault(key, value)
        return weights

    def _save_weights(self) -> None:
        try:
            Path(self.cache_file).write_text(
                json.dumps(self.weights, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.exception("Failed to save adaptive cache: %s", exc)

    # ── History loading ───────────────────────────────────────────

    def _load_history(self) -> pd.DataFrame | None:
        try:
            csv_path = Path(self.csv_path)
            if not csv_path.exists():
                return None
            df = pd.read_csv(csv_path)
            if df.empty:
                return None
            df.columns = [str(col).lower().strip() for col in df.columns]
            required = ["result", "score"]
            if any(col not in df.columns for col in required):
                return None
            df["result"] = df["result"].astype(str).str.upper().str.strip()
            df = df[df["result"].isin(["WIN", "LOSS"])].copy()
            if df.empty:
                return None
            df["score"] = pd.to_numeric(df["score"], errors="coerce")
            return df.dropna(subset=["score"])
        except Exception as exc:
            logger.exception("Failed to load signal history: %s", exc)
            return None

    def _win_rate_for_condition(
        self, df: pd.DataFrame, column: str, value
    ) -> float:
        try:
            if column not in df.columns:
                return 0.5
            filtered = df[df[column] == value]
            if len(filtered) < MIN_SAMPLES:
                return 0.5
            wins = int((filtered["result"] == "WIN").sum())
            return wins / max(len(filtered), 1)
        except Exception as exc:
            logger.exception(
                "Failed to compute condition win rate for %s: %s", column, exc
            )
            return 0.5

    # ── Main learning pass ────────────────────────────────────────

    def learn(self) -> dict:
        """Read history, adjust weights, and return a summary of changes."""
        history = self._load_history()
        samples = 0 if history is None else len(history)
        if history is None or samples < MIN_SAMPLES:
            return {"status": "insufficient_data", "samples": samples}

        if self.last_signal_count and (samples - self.last_signal_count) < RECALCULATE_EVERY:
            return {"status": "skipped", "reason": "not enough new signals"}

        overall_win_rate = float((history["result"] == "WIN").mean())

        # ── Direction-aware win rates (KEY FIX v2) ────────────────
        if "direction" in history.columns:
            long_hist  = history[history["direction"].astype(str).str.upper() == "LONG"]
            short_hist = history[history["direction"].astype(str).str.upper() == "SHORT"]
            self._long_win_rate  = float((long_hist["result"]  == "WIN").mean()) if len(long_hist)  >= MIN_SAMPLES else 0.5
            self._short_win_rate = float((short_hist["result"] == "WIN").mean()) if len(short_hist) >= MIN_SAMPLES else 0.5
            logger.info(
                "Direction win rates — LONG: %.1f%%, SHORT: %.1f%%",
                self._long_win_rate * 100,
                self._short_win_rate * 100,
            )
        else:
            self._long_win_rate  = overall_win_rate
            self._short_win_rate = overall_win_rate

        # ── Factor weight adjustment ──────────────────────────────
        weights_changed: dict[str, tuple[float, float]] = {}

        for key, current_weight in list(self.weights.items()):
            factor_wr = overall_win_rate
            if key in history.columns:
                active = history[key]
                if pd.api.types.is_bool_dtype(active):
                    subset = history[active.fillna(False)]
                else:
                    numeric_active = pd.to_numeric(active, errors="coerce")
                    subset = history[numeric_active.fillna(0) > 0]
                if len(subset) >= MIN_SAMPLES:
                    factor_wr = float((subset["result"] == "WIN").mean())

            delta      = (factor_wr - overall_win_rate) * LEARNING_RATE
            base_weight = float(BASE_WEIGHTS.get(key, 5))
            clamp_base  = base_weight if base_weight > 0 else 5.0
            new_weight  = float(current_weight) * (1 + delta)
            new_weight  = max(
                clamp_base * WEIGHT_FLOOR,
                min(new_weight, clamp_base * WEIGHT_CEILING),
            )
            new_weight = round(new_weight, 2)
            if new_weight != current_weight:
                weights_changed[key] = (float(current_weight), new_weight)
            self.weights[key] = new_weight

        # ── Regime adjustments ────────────────────────────────────
        regime_adjustments: dict[str, int] = {}
        if "regime" in history.columns:
            for regime_name in sorted(
                set(history["regime"].astype(str).str.upper().str.strip())
            ):
                regime_wr = self._win_rate_for_condition(
                    history.assign(
                        regime=history["regime"].astype(str).str.upper().str.strip()
                    ),
                    "regime",
                    regime_name,
                )
                if regime_wr > 0.65:
                    regime_adjustments[regime_name] = -2
                elif regime_wr < 0.40:
                    regime_adjustments[regime_name] = 3
        self.regime_adjustments = regime_adjustments

        self._save_weights()
        self.last_signal_count = samples
        ranked = sorted(self.weights.items(), key=lambda item: item[1], reverse=True)

        return {
            "status":            "updated",
            "samples_used":      samples,
            "overall_win_rate":  round(overall_win_rate, 4),
            "long_win_rate":     round(self._long_win_rate,  4),
            "short_win_rate":    round(self._short_win_rate, 4),
            "weights_changed":   weights_changed,
            "regime_adjustments": dict(self.regime_adjustments),
            "top_factors":       [name for name, _ in ranked[:3]],
            "weak_factors":      [name for name, _ in ranked[-3:]],
        }

    # ── Public API ────────────────────────────────────────────────

    def get_weights(self) -> dict:
        """Return weights after attempting a silent learning pass."""
        try:
            self.learn()
        except Exception as exc:
            logger.exception("Adaptive learn failed: %s", exc)
        return dict(self.weights)

    def get_score_threshold(self, regime: str = "NORMAL", direction: str = "LONG") -> int:
        """
        Return the adaptive minimum score threshold for this regime + direction.

        FIX v2: LONG threshold is raised when LONG win rate is poor; SHORT
        threshold is raised when SHORT win rate is poor.
        """
        regime_name = str(regime).upper()
        direction   = str(direction).upper()
        base        = 90 + int(self.regime_adjustments.get(regime_name, 0))

        # Adjust per direction performance
        if direction == "LONG":
            if self._long_win_rate < 0.35:
                base += 4   # LONGs are losing badly — require very high score
            elif self._long_win_rate < 0.45:
                base += 2
        else:
            if self._short_win_rate < 0.35:
                base += 4
            elif self._short_win_rate < 0.45:
                base += 2

        return int(max(95, min(98, base)))

    def get_weight(self, factor: str) -> float:
        """Return the learned weight for a factor."""
        return float(self.weights.get(factor, BASE_WEIGHTS.get(factor, 5)))

    def get_direction_bias(self) -> str:
        """
        Returns which direction has the better historical win rate.
        "LONG" | "SHORT" | "NEUTRAL"
        Used by signal_engine to add a small tiebreaker bonus.
        """
        diff = self._long_win_rate - self._short_win_rate
        if diff > 0.10:
            return "LONG"
        elif diff < -0.10:
            return "SHORT"
        return "NEUTRAL"

    def explain(self) -> str:
        """Return a readable summary of adaptive weights and thresholds."""
        lines = [
            "Adaptive weights:",
            f"  LONG win rate:  {self._long_win_rate:.1%}",
            f"  SHORT win rate: {self._short_win_rate:.1%}",
        ]
        boosted:  list[str] = []
        weakened: list[str] = []
        for key, base_value in BASE_WEIGHTS.items():
            current = float(self.weights.get(key, base_value))
            lines.append(f"  - {key}: {current} (base {base_value})")
            if current > base_value:
                boosted.append(key)
            elif current < base_value:
                weakened.append(key)
        lines.append(f"Boosted:  {', '.join(boosted)  if boosted  else 'None'}")
        lines.append(f"Weakened: {', '.join(weakened) if weakened else 'None'}")
        lines.append(
            "Regime adjustments: "
            + (
                ", ".join(
                    f"{k}={v:+d}"
                    for k, v in sorted(self.regime_adjustments.items())
                )
                if self.regime_adjustments
                else "None"
            )
        )
        return "\n".join(lines)


# ── Singleton helpers ──────────────────────────────────────────────────────────

def get_adaptive_engine(csv_path: str = CSV_PATH) -> AdaptiveScoreEngine:
    """Return a singleton adaptive engine instance."""
    global _engine
    if _engine is None:
        _engine = AdaptiveScoreEngine(csv_path=csv_path)
    return _engine


def apply_adaptive_weights(
    base_score_breakdown: dict,
    adaptive_engine: AdaptiveScoreEngine,
) -> dict:
    """Reweight a score breakdown using adaptive learned factor weights."""
    factor_weights_used: dict[str, float] = {}
    boosted_factors:    list[str] = []
    weakened_factors:   list[str] = []
    adjusted_total  = 0.0
    original_total  = 0.0

    try:
        for factor, raw_points in base_score_breakdown.items():
            raw_value      = float(raw_points)
            base_weight    = float(BASE_WEIGHTS.get(factor, 5))
            adaptive_weight = float(adaptive_engine.get_weight(factor))
            factor_weights_used[factor] = adaptive_weight
            scale           = (adaptive_weight / base_weight) if base_weight > 0 else 1.0
            adjusted_total += raw_value * scale
            original_total += raw_value
            if scale > 1.05:
                boosted_factors.append(factor)
            elif scale < 0.95:
                weakened_factors.append(factor)

        max_score = sum(float(BASE_WEIGHTS.get(f, 5)) for f in base_score_breakdown)
        if max_score <= 0:
            adjusted_score = 0
        else:
            adjusted_score = int(
                round(max(0.0, min(100.0, (adjusted_total / max_score) * 100.0)))
            )
        original_score = int(
            round(max(0.0, min(100.0, (original_total / max(max_score, 1.0)) * 100.0)))
        )

        return {
            "adjusted_score":      adjusted_score,
            "adjustment_delta":    int(adjusted_score - original_score),
            "factor_weights_used": factor_weights_used,
            "boosted_factors":     boosted_factors,
            "weakened_factors":    weakened_factors,
        }

    except Exception as exc:
        logger.exception("Failed to apply adaptive weights: %s", exc)
        return {
            "adjusted_score":      0,
            "adjustment_delta":    0,
            "factor_weights_used": factor_weights_used,
            "boosted_factors":     boosted_factors,
            "weakened_factors":    weakened_factors,
        }