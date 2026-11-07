"""The only place that talks to an LLM provider. Messages in -> (parsed JSON, usage, raw text) out."""
import json
import logging

from . import config

log = logging.getLogger(__name__)
_client = None


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


async def chat_json(model: str, messages: list[dict]) -> tuple[dict, dict, str]:
    extra = {"reasoning_effort": config.REASONING_EFFORT} if config.REASONING_EFFORT else {}
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
