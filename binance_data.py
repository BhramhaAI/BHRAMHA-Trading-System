import time

import pandas as pd

from binance_http import BINANCE_HTTP_TIMEOUT, session
from config import (
    BINANCE_API_MAX_RETRIES,
    BINANCE_API_RETRY_DELAY_SECONDS,
)

def get_data(symbol, interval, limit=200):

    url = "https://api.binance.com/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    data = None
    for attempt in range(1, BINANCE_API_MAX_RETRIES + 1):
        try:
            r = session.get(url, params=params, timeout=BINANCE_HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as exc:
            if "timed out" in str(exc).lower() and attempt < BINANCE_API_MAX_RETRIES:
                print(f"[API] Binance timeout for {symbol} {interval} - retry {attempt}/{BINANCE_API_MAX_RETRIES}")
                time.sleep(BINANCE_API_RETRY_DELAY_SECONDS)
                continue
            if "timed out" in str(exc).lower():
                print(f"[API] Binance timeout — skipping {symbol}")
                return None
            print(f"[API] Binance fetch failed for {symbol} {interval}: {exc}")
            return None

    if not isinstance(data, list) or not data:
        return None

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","num_trades","taker_base","taker_quote","ignore"
    ])

    # convert to numeric
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    # convert time column to datetime
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)

    # set index
    df.set_index("time", inplace=True)

    return df
