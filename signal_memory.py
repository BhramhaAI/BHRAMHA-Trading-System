import time

signal_history = {}

COOLDOWN_SECONDS = 1800   # 30 minutes


def is_duplicate(signal_key):

    now = time.time()

    if signal_key in signal_history:

        last_time = signal_history[signal_key]

        if now - last_time < COOLDOWN_SECONDS:

            return True

    signal_history[signal_key] = now

    return False
