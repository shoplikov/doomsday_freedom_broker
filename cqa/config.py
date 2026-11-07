"""Every setting comes from the environment or a local .env file. See .env.example."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    # ponytail: 8-line .env reader instead of python-dotenv; real env vars always win.
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DB_PATH = Path(_get("CQA_DB_PATH") or DATA_DIR / "cqa.sqlite3")
RAW_DIR = Path(_get("CQA_RAW_DIR") or DATA_DIR / "raw")  # cached API responses (offline fallback)
DATASET_API_URL = _get("DATASET_API_URL")

LLM_PROVIDER = _get("LLM_PROVIDER", "mock")  # mock | openai
OPENAI_API_KEY = _get("OPENAI_API_KEY")
OPENAI_BASE_URL = _get("OPENAI_BASE_URL") or None
MODELS = {
    "inspector": _get("MODEL_INSPECTOR", "gpt-4.1-mini"),
    "advocate": _get("MODEL_ADVOCATE", "gpt-4.1-mini"),
    "judge": _get("MODEL_JUDGE", "gpt-4.1"),
}
JUDGE_MODE = _get("JUDGE_MODE", "always")  # always | disputed
TRUST_THRESHOLD = float(_get("TRUST_THRESHOLD", "0.67"))
CONCURRENCY = int(_get("CONCURRENCY", "8"))
RATIONALE_LANGUAGE = _get("RATIONALE_LANGUAGE", "Russian")
# Only for reasoning models (gpt-5*, o*): minimal | low | medium | high. Empty = provider default.
REASONING_EFFORT = _get("REASONING_EFFORT")
# Extra JSON merged into every chat request, for OpenAI-compatible servers (vLLM / SGLang), e.g.
# {"chat_template_kwargs": {"enable_thinking": false}} to turn off Qwen thinking.
LLM_EXTRA_BODY: dict = json.loads(_get("LLM_EXTRA_BODY") or "{}")
LLM_PRICES: dict[str, list[float]] = json.loads(_get("LLM_PRICES") or "{}")

LOG_DIR = Path(_get("CQA_LOG_DIR") or DATA_DIR / "logs")


def setup_logging() -> None:
    """Console + data/logs/cqa.log (rotating). One line per LLM call, validation failure and evaluation."""
    import logging
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    if any(getattr(h, "_cqa", False) for h in root.handlers):
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (logging.StreamHandler(), RotatingFileHandler(LOG_DIR / "cqa.log", maxBytes=5_000_000,
                                                                 backupCount=3, encoding="utf-8")):
        handler.setFormatter(fmt)
        handler._cqa = True
        root.addHandler(handler)
    root.setLevel(_get("LOG_LEVEL", "INFO"))
    for noisy in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel("WARNING")


# Bump whenever prompts change: it is part of the cache key, so old cached verdicts stop being reused.
PROMPT_VERSION = "p1"
