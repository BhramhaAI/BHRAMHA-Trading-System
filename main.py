import time
import os
from datetime import datetime, timezone

import pandas as pd

from signal_engine import generate_signal
from scalp_engine import generate_scalp_signal
from telegram_bot import send_signal
from logger import log_signal
from outcome_engine import check_trade_outcomes
from config import (
    SCAN_INTERVAL,
    SCALP_SCAN_INTERVAL,
    AUTO_TRADE,
    ENTRY_CONFIRMATION_ENABLED,
    MIN_CANDLE_AGE_MINUTES,
    TIMEFRAMES,
)
from binance_data import get_data
from trade_engine import (
    place_trade,
    can_place_trade,
    check_portfolio_stop_loss
)


def _trend_scan_ready(now_ts):
    if not ENTRY_CONFIRMATION_ENABLED or "5m" not in TIMEFRAMES:
        return True
    candle_age_seconds = now_ts % 300
    return candle_age_seconds >= (float(MIN_CANDLE_AGE_MINUTES) * 60.0)


def wait_for_candle_maturity():
    if not ENTRY_CONFIRMATION_ENABLED:
        return

    try:
        # Check BTC candle age as reference
        df = get_data("BTCUSDT", "5m", limit=3)
        if df is None or df.empty:
            return

        last_candle = df.index[-1]
        if hasattr(last_candle, 'timestamp'):
            candle_ts = last_candle.timestamp()
        else:
            candle_ts = pd.Timestamp(last_candle, tz='UTC').timestamp()

        now_ts = datetime.now(timezone.utc).timestamp()
        age_minutes = (now_ts - candle_ts) / 60.0

        if age_minutes < MIN_CANDLE_AGE_MINUTES:
            wait_seconds = (MIN_CANDLE_AGE_MINUTES - age_minutes) * 60.0
            if wait_seconds > 0:
                print(f"[CANDLE WAIT] New candle opened — waiting {wait_seconds:.0f}s for confirmation")
                time.sleep(wait_seconds)
    except Exception as e:
        print(f"Candle wait error: {e}")


def run_bot():
    print("BHRAMHA SCANNER STARTED")

    print("[CONFIG] LONG min score: 95")
    print("[CONFIG] SHORT min score: 88")
    print("[CONFIG] Hard blocks active: HIGH_LUNAR, SATURN_HORA, ASIA_OPEN, BULL_LONG")


    while True:
        # 1. Wait for candle maturity (blocks if candle too young)
        wait_for_candle_maturity()

        # 2. Trend scans (5m) - Always runs first
        signals = generate_signal()
        for s in signals:
            coin = s.get("data", {}).get("coin", "Unknown")
            sent = send_signal(s)

            if sent:
                log_signal(s["data"])
            elif str(s.get("data", {}).get("result", "OPEN")).upper() != "REJECTED":
                print(f"ALL send methods failed for {coin} - check Telegram config")

            # --- AUTO TRADING INTEGRATION ---
            if AUTO_TRADE and sent:
                if check_portfolio_stop_loss():
                    print("Portfolio SL hit - trading halted")
                elif not bool(s.get("data", {}).get("auto_trade_allowed", True)):
                    reason = str(s.get("data", {}).get("auto_trade_reason", "auto-trade block")).strip()
                    print(f"Auto trade skipped for {coin} due to {reason}")
                elif can_place_trade():
                    trade_placed = place_trade(s["data"])
                    if trade_placed:
                        print(f"Trade placed: {coin}")
                    else:
                        print(f"Trade failed: {coin}")
                else:
                    print("Waiting for funds...")

        # 3. Scalp scans (1m + 5m) - Runs after trend
        scalp_signals = generate_scalp_signal()
        for s in scalp_signals[:3]:
            coin = s.get("data", {}).get("coin", "Unknown")
            sent = send_signal(s)

            if sent:
                log_signal(s["data"])
            elif str(s.get("data", {}).get("result", "OPEN")).upper() != "REJECTED":
                print(f"ALL send methods failed for scalp {coin}")

        # 4. Check outcomes & Sleep
        check_trade_outcomes()
        print("Cycle completed. Waiting 60s...")
        time.sleep(60)


if __name__ == "__main__":
    run_bot()