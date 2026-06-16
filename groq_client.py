# -*- coding: utf-8 -*-
"""
groq_client.py — thin Groq LLM wrapper for the BHRAMHA Skill.
=============================================================

The Skill uses an LLM to author a human-readable *strategy spec* from BHRAMHA's
deterministic signal output — turning the numeric confluence (technical + Vedic +
sentiment) into the kind of natural-language rule set BNB Hack Track 2 asks for.

The deterministic math stays in `strategy_core` (so it is backtestable); the LLM
only narrates and explains it. The API key is read from `config.GROQ_API_KEY`
(env / .env), never hardcoded.
"""

from __future__ import annotations

import requests

from config import GROQ_API_KEY

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
_TIMEOUT = 45


def chat(prompt: str, system: str | None = None,
         model: str = DEFAULT_MODEL, temperature: float = 0.3,
         max_tokens: int = 700) -> str:
    """Single-turn completion. Returns the assistant text, or an error string
    (never raises) so the Skill degrades gracefully without a key/network."""
    if not GROQ_API_KEY:
        return "[groq] GROQ_API_KEY not set — skipping LLM narration."

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"[groq] LLM call failed: {exc}"
