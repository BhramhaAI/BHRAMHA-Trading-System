from __future__ import annotations

from binance_http import BINANCE_HTTP_TIMEOUT, session


def analyze_orderflow(symbol):
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=100"

    try:
        response = session.get(url, timeout=BINANCE_HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

        bids = payload.get("bids", [])[:20]
        asks = payload.get("asks", [])[:20]

        buy_volume = sum(float(level[1]) for level in bids if len(level) >= 2)
        sell_volume = sum(float(level[1]) for level in asks if len(level) >= 2)
        imbalance = buy_volume - sell_volume
        imbalance_ratio = buy_volume / max(sell_volume, 1e-9)

        if imbalance_ratio > 1.3:
            bias = "BUYERS"
        elif imbalance_ratio < 0.7:
            bias = "SELLERS"
        else:
            bias = "NEUTRAL"

        return {
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "bias": bias,
            "imbalance": imbalance,
            "imbalance_ratio": imbalance_ratio,
        }
    except Exception as e:
        print(f"Order flow error for {symbol}: {e}")
        return {
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "bias": "NEUTRAL",
            "imbalance": 0.0,
            "imbalance_ratio": 1.0,
        }
