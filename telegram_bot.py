# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from resilience import safe_execute


def _post_telegram(url: str, data: dict) -> bool:
    try:
        import requests
    except Exception as e:
        print("Telegram error: requests import failed:", str(e))
        return False

    response = requests.post(url, data=data, timeout=15)
    print("Telegram response status:", response.status_code)
    print("Telegram response:", response.text)
    response.raise_for_status()

    try:
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(f"Telegram API error payload: {payload}")
    except ValueError:
        # Non-JSON response is unexpected for Telegram API.
        raise RuntimeError(f"Telegram returned non-JSON response: {response.text}")

    return True


def send_message(msg: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}

    result = safe_execute(lambda: _post_telegram(url, data), retries=3, delay=5)
    if not result:
        print("Telegram send failed for chat_id:", TELEGRAM_CHAT_ID)
    return bool(result)


def send_signal(signal: dict) -> bool:
    data = signal.get("data", {}) if isinstance(signal, dict) else {}
    status = str(data.get("status", "")).upper()
    result = str(data.get("result", "OPEN")).upper()
    if status != "APPROVED" or result == "REJECTED":
        print("[TELEGRAM BLOCKED] Rejected signal — not sending")
        return False

    chart = signal.get("chart") if isinstance(signal, dict) else None
    message = signal.get("message", "") if isinstance(signal, dict) else ""
    print("[TELEGRAM] Signal approved — sending to Telegram")
    if chart and os.path.exists(chart):
        sent = send_photo(chart, message)
        if sent:
            return True
        print(f"Photo send failed for {data.get('coin', 'UNKNOWN')}, trying text...")
    elif chart:
        print(f"Chart missing for {data.get('coin', 'UNKNOWN')}, sending text signal...")

    return send_message(message)


def test_telegram() -> bool:
    return send_message("BHRAMHA test message")


def send_photo(photo_path: str, caption: str) -> bool:
    if not os.path.exists(photo_path):
        print("Chart image missing:", photo_path)
        # If chart is missing, just send the text. The success of this operation is the result.
        return send_message(caption)

    def _post_photo() -> bool:
        """Inner function to be wrapped by safe_execute for retries."""
        import requests

        with open(photo_path, "rb") as photo_file:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            files = {"photo": photo_file}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}

            response = requests.post(url, data=data, files=files, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok", False):
                raise RuntimeError(f"Telegram API error on sendPhoto: {payload}")
            return True

    photo_sent = safe_execute(_post_photo, retries=2, delay=5)
    if photo_sent:
        return True

    print("Failed to send photo, falling back to text message.")
    return send_message(caption)
