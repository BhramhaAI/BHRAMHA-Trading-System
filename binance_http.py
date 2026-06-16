from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import BINANCE_API_CONNECT_TIMEOUT, BINANCE_API_READ_TIMEOUT


BINANCE_HTTP_TIMEOUT = (BINANCE_API_CONNECT_TIMEOUT, BINANCE_API_READ_TIMEOUT)

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET", "POST", "DELETE"]),
)
adapter = HTTPAdapter(max_retries=retry)
session = requests.Session()
session.mount("https://", adapter)

