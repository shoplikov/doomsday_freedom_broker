"""The only place that talks to an LLM provider. Messages in -> (parsed JSON, usage, raw text) out."""
import asyncio
import json
import logging
import time

from . import config

log = logging.getLogger(__name__)
_client = None
_rate_lock: asyncio.Lock | None = None
_next_slot = 0.0


async def throttle() -> None:
    """Spread request starts to stay under LLM_RPM requests per minute (a proxy that answers 429 above it)."""
    global _rate_lock, _next_slot
    if not config.LLM_RPM:
        return
    _rate_lock = _rate_lock or asyncio.Lock()
    async with _rate_lock:
        now = time.monotonic()
        wait = _next_slot - now
        _next_slot = max(now, _next_slot) + 60.0 / config.LLM_RPM
    if wait > 0:
        await asyncio.sleep(wait)


def _openai():
    global _client
    if _client is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set (or run with LLM_PROVIDER=mock)")
        from openai import AsyncOpenAI
        # SDK retries 429/5xx with backoff on its own.
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL,
                              max_retries=4, timeout=180)
    return _client


def parse_json(text: str) -> dict:
    """Strict JSON, else the outermost {...} block (models sometimes wrap JSON in prose or fences)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start:end + 1]) if 0 <= start < end else {}
        except json.JSONDecodeError:
            log.warning("invalid JSON from model: %.200s", text)
            data = {}
    return data if isinstance(data, dict) else {}


async def chat_json(model: str, messages: list[dict], temperature: float | None = None) -> tuple[dict, dict, str]:
    """temperature: None = config.LLM_TEMPERATURE (0 by default); the pipeline passes a higher one to resample."""
    extra = {"reasoning_effort": config.REASONING_EFFORT} if config.REASONING_EFFORT else {}
    t = config.LLM_TEMPERATURE if temperature is None else temperature
    # Reasoning models accept a temperature only with reasoning_effort "none" (gpt-5.4+, gpt-6 luna/terra).
    if t is not None and config.REASONING_EFFORT in (None, "", "none"):
        extra["temperature"] = t
    # api.openai.com wants max_completion_tokens (reasoning models reject max_tokens); SGLang/vLLM want max_tokens.
    cap = "max_tokens" if config.OPENAI_BASE_URL else "max_completion_tokens"
    extra[cap] = config.LLM_MAX_TOKENS
    await throttle()
    resp = await _openai().chat.completions.create(
        model=model, messages=messages, response_format={"type": "json_object"},
        extra_body=config.LLM_EXTRA_BODY or None, **extra,
    )
    u = resp.usage
    usage = {"model": model, "in": u.prompt_tokens if u else 0, "out": u.completion_tokens if u else 0}
    text = resp.choices[0].message.content or ""
    return parse_json(text), usage, text


def cost_usd(usages: list[dict]) -> float:
    total = 0.0
    for u in usages:
        price_in, price_out = config.LLM_PRICES.get(u["model"], (0.0, 0.0))
        total += u["in"] / 1e6 * price_in + u["out"] / 1e6 * price_out
    return round(total, 6)
