"""Precedents: decided cases shown to the model, so it applies each criterion the way our reviewers do.

A strong model rarely misreads a dialog; its remaining misses are about the house reading of a rule (re-asking a known
detail is "no", not N/A; where step 7 ends and 4 begins). Rewording the checklist for each miss overfits, so instead
the model sees the 4 most similar cases already decided by a reviewer, with the reviewer's reason and the cited
messages. The pool is the blind gold labels and verified scenarios (data/eval); a bench run uses only the TUNE half.

Similarity: OpenAI embeddings of the client and operator text (multilingual). ponytail: the pool is re-embedded once
per process (~100 short calls batched into one); fine for a few hundred cases.
"""
import asyncio
import json
import logging
import math
import re
from collections import Counter

from . import bench, config, importer, llm, pipeline

log = logging.getLogger(__name__)
K = 4  # cases per criterion
EMBED_MODEL = config._get("PRECEDENT_EMBED_MODEL", "text-embedding-3-small")
_MID = re.compile(r"\bm(\d+)\b")

_pool: list[dict] | None = None
_lock = asyncio.Lock()
# A bench run sets PRECEDENT_SPLITS=tune so HOLDOUT and fresh stay unseen.
POOL_SPLITS = set(config._get("PRECEDENT_SPLITS", "tune,holdout").split(","))


def _reasons() -> dict[str, dict]:
    """conversation_id -> {criterion_id: why} from the label files."""
    out = {}
    for g in json.loads((bench.EVAL_DIR / "gold_dev.json").read_text(encoding="utf-8")):
        out[g["conversation_id"]] = {c: v.get("why", "") for c, v in g["labels"].items()}
    for f in sorted((bench.EVAL_DIR / "scenarios").glob("*.json")):
        for s in json.loads(f.read_text(encoding="utf-8")):
            out[s["conversation_id"]] = {c: v.get("why", "") for c, v in s["expected"].items()}
    return out


def _text(msgs: list[dict]) -> str:
    return "\n".join(f"{m['speaker']}: {m['text_masked']}" for m in msgs
                     if m["speaker"] in ("client", "operator"))[:6000]


async def _embed(texts: list[str]) -> list[list[float]]:
    await llm.throttle()
    resp = await llm._openai().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


async def pool() -> list[dict]:
    global _pool
    async with _lock:
        if _pool is None:
            why = _reasons()
            cases = []
            for it in bench.gold_items() + bench.scenario_items():
                if it["split"] not in POOL_SPLITS:
                    continue
                _, msgs = importer.normalize(it["record"])
                cases.append({"id": it["id"], "language": it["language"], "msgs": {m["mid"]: m for m in msgs},
                              "text": _text(msgs), "expect": it["expect"], "why": why.get(it["id"], {})})
            try:
                vecs = await _embed([c["text"] for c in cases])
                for c, v in zip(cases, vecs):
                    c["vec"] = v
            except Exception:  # no embeddings (e.g. a local server): character trigrams instead
                log.warning("precedent embeddings unavailable; ranking by character trigrams", exc_info=True)
            _pool = cases
            log.info("precedent pool: %d cases (splits %s)", len(cases), sorted(POOL_SPLITS))
    return _pool


def _grams(text: str) -> Counter:
    t = re.sub(r"\s+", " ", text.lower())
    return Counter(t[i:i + 3] for i in range(len(t) - 2))


def _sparse_cos(a: Counter, b: Counter) -> float:
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())) or 1)


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def _case_text(case: dict, cid: str) -> str:
    label = case["expect"][cid]["label"]
    why = case["why"].get(cid, "")
    lines = [f"- Case {case['id']}: answer = {label}. Reviewer: {why}"]
    for n in sorted({int(x) for x in _MID.findall(why)})[:4]:
        m = case["msgs"].get(f"m{n}")
        if m:
            lines.append(f"    [m{n} {m['speaker']}] {m['text_masked'][:300]}")
    return "\n".join(lines)


async def select(criteria: list[dict], msgs: list[dict], language: str | None, exclude: set[str]) -> dict[str, str]:
    """{criterion_id: text block of K precedents}. Label diversity first: the nearest case of each answer among the
    12 nearest, then the next nearest, so the model sees where the line is drawn."""
    cases = [c for c in await pool() if c["id"] not in exclude]
    if not cases:
        return {}
    ranked = None
    if all("vec" in c for c in cases):
        try:
            q = (await _embed([_text(msgs)]))[0]
            ranked = sorted(cases, key=lambda c: -_cos(q, c["vec"]))
        except Exception:
            log.warning("precedent query embedding failed; using character trigrams", exc_info=True)
    if ranked is None:
        q = _grams(_text(msgs))
        ranked = sorted(cases, key=lambda c: -_sparse_cos(q, c.setdefault("grams", _grams(c["text"]))))
    out = {}
    for c in criteria:
        cid = c["id"]
        with_label = [x for x in ranked if cid in x["expect"] and x["expect"][cid]["label"]]
        picked, seen = [], set()
        for x in with_label[:12]:
            lab = x["expect"][cid]["label"]
            if lab not in seen:
                seen.add(lab)
                picked.append(x)
        for x in with_label:
            if len(picked) >= K:
                break
            if x not in picked:
                picked.append(x)
        picked = sorted(picked[:K], key=with_label.index)
        if picked:
            out[cid] = "\n".join(_case_text(x, cid) for x in picked)
    return out


async def evaluate_criteria(criteria, msgs, facts, conversation_id="") -> tuple[dict, list[dict]]:
    """The panel with precedents in every role's prompt. Same signature as pipeline.evaluate_criteria."""
    item_id = conversation_id.rsplit(":", 1)[-1]  # bench passes "bench:<tag>:<item id>"
    exclude = {item_id, item_id.split("~")[0]}  # never the dialog itself or the base of a mutation
    lang = next((m.get("language") for m in msgs if m.get("language")), None)
    cases = await select(criteria, msgs, lang, exclude)
    return await pipeline.evaluate_criteria(criteria, msgs, facts, conversation_id, precedents=cases)
