"""Model-agnostic language model interface.

RAHMAH_PROVIDER selects the backend:
  anthropic  - Claude via the Anthropic Messages API (ANTHROPIC_API_KEY)
  openai     - any OpenAI-compatible endpoint (OPENAI_API_KEY, OPENAI_BASE_URL);
               this also covers self-hosted open-weight models served by vLLM or Ollama
  offline    - no model; template replies built from the vetted library (default, for demos and tests)

For the pilot, use a provider account with a zero-data-retention agreement.
"""
from __future__ import annotations

import json
import os

import httpx

TIMEOUT = 30.0


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _groq_defaults():
    """GROQ_API_KEY alone is enough: it points the OpenAI-compatible client at Groq's free open-weight models."""
    from .config import env
    if env("GROQ_API_KEY") and not env("OPENAI_API_KEY"):
        os.environ.setdefault("OPENAI_BASE_URL", GROQ_BASE_URL)
        os.environ.setdefault("RAHMAH_MODEL", GROQ_DEFAULT_MODEL)
        os.environ["OPENAI_API_KEY"] = env("GROQ_API_KEY")


def provider() -> str:
    _groq_defaults()
    p = os.getenv("RAHMAH_PROVIDER")
    if p:
        return p
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        return "openai"
    return "offline"


def complete(system: str, messages: list[dict], max_tokens: int = 500) -> str:
    """messages: [{'role': 'user'|'assistant', 'content': str}, ...]"""
    p = provider()
    if p == "anthropic":
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.getenv("RAHMAH_MODEL", "claude-sonnet-5-5"),
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text")
    if p == "openai":
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', 'none')}"},
            json={
                "model": os.getenv("RAHMAH_MODEL", "gpt-4o-mini"),
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}] + messages,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("offline provider has no model")


def classify_risk(text: str) -> str:
    """Safety layer 2: ask the model whether a message shows risk of self-harm.
    Returns 'high', 'low', or 'unknown' (offline or error). Callers treat 'unknown'
    as low ONLY because layer 1 has already screened the message; tune this policy
    with counsellors."""
    if provider() == "offline":
        return "unknown"
    system = (
        "You are a safety classifier for a student wellbeing service. Decide whether the message "
        "suggests any risk of suicide, self-harm, or danger to self or others, including indirect, "
        "metaphorical or code-switched (English/Malay/Arabic) expressions. When unsure, answer high. "
        'Reply with JSON only: {"risk": "high"} or {"risk": "low"}.'
    )
    try:
        out = complete(system, [{"role": "user", "content": text}], max_tokens=20)
    except (httpx.HTTPError, KeyError, RuntimeError):
        # Model unreachable or rate-limited (e.g. free-tier quota used up). Layer 1 has already
        # screened the message, so fall back to it rather than sending every student a crisis reply.
        return "unknown"
    try:
        return json.loads(out[out.index("{"): out.rindex("}") + 1]).get("risk", "high")
    except Exception:
        return "high"  # the model answered but not in the expected form: be cautious
