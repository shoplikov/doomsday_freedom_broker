"""Benchmark: score an approach on dialogs whose correct answers are known, so approaches and prompt or
checklist changes are compared by numbers, not impressions.

Three suites, all with reference answers fixed BEFORE the system runs:
  gold       real DEV dialogs labelled blind against the organizers' checklist (data/eval/gold_dev.json)
  synthetic  realistic dialogs written to a scenario, so the answer follows from the text (data/eval/scenarios/)
  mutations  a gold or synthetic dialog broken on purpose (greeting removed, side deal inserted, 5-minute
             silence...), so the target criterion MUST flip; "harmless" edits must change nothing

Every item belongs to the TUNE or HOLDOUT split. Tune on TUNE; look at HOLDOUT only as a number, rarely.
A gain on TUNE that does not show on HOLDOUT is overfitting.

  uv run python -m cqa.cli bench --split tune --out results/bench/p2.tune.json
  uv run python -m cqa.cli bench-compare results/bench/p1.tune.json results/bench/p2.tune.json
  uv run python -m cqa.cli bench-show results/bench/p2.tune.json --criterion c02
"""
import asyncio
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from . import config, importer, llm, pipeline, redteam, scoring, signals

EVAL_DIR = config.DATA_DIR / "eval"
NA_CRITERIA = ("c02", "c03", "c04", "c07")

# An approach takes (criteria, msgs, facts, conversation_id) and returns ({criterion_id: result}, usage)
# with the result shape of pipeline.evaluate_criteria (ai, votes, confidence, status, ...).
APPROACHES = {"panel": pipeline.evaluate_criteria}


def norm_label(x) -> str:
    s = str(x).strip().lower()
    try:
        return f"{float(s):g}"
    except ValueError:
        return {"n/a": "na", "да": "yes", "нет": "no"}.get(s, s)


def _final(label: str) -> dict:
    return {"verdict": label, "step": None} if label in ("yes", "no", "na") else {"verdict": "scored", "step": float(label)}


def _expect(labels: dict) -> dict:
    return {cid: {"label": norm_label(v["label"]), "accept": sorted({norm_label(v["label"])}
                                                                   | {norm_label(a) for a in v.get("accept") or []})}
            for cid, v in labels.items()}


def load_checklist(path: Path | None = None) -> dict:
    return json.loads((path or config.DATA_DIR / "checklist_v1.json").read_text(encoding="utf-8"))


# ---------- items ----------

def _alternate(items: list[dict]) -> None:
    """Deterministic split: within each (source, language, channel), sorted by id, every other item is HOLDOUT."""
    groups = defaultdict(list)
    for it in items:
        groups[(it["source"], it["language"], it["channel"])].append(it)
    for g in groups.values():
        for i, it in enumerate(sorted(g, key=lambda x: x["id"])):
            if not it.get("split"):
                it["split"] = "tune" if i % 2 == 0 else "holdout"


def gold_items() -> list[dict]:
    path = EVAL_DIR / "gold_dev.json"
    raw = config.RAW_DIR / "DEV.json"
    if not path.exists() or not raw.exists():
        return []
    records = {r["conversation_id"]: r for r in json.loads(raw.read_text(encoding="utf-8"))["data"]["records"]}
    out = []
    for g in json.loads(path.read_text(encoding="utf-8")):
        r = records.get(g["conversation_id"])
        if r:
            out.append({"id": r["conversation_id"], "source": "gold", "split": g.get("split"),
                        "language": r.get("language"), "channel": r.get("channel"), "record": r,
                        "expect": _expect(g["labels"])})
    _alternate(out)
    return out


def scenario_items() -> list[dict]:
    out = []
    for f in sorted((EVAL_DIR / "scenarios").glob("*.json")):
        for s in json.loads(f.read_text(encoding="utf-8")):
            rec = {k: s[k] for k in ("conversation_id", "channel", "language", "transcript")}
            out.append({"id": s["conversation_id"], "source": "synthetic", "split": s.get("split"),
                        "language": s.get("language"), "channel": s.get("channel"), "record": rec,
                        "expect": _expect(s["expected"]), "focus": s.get("focus"), "trap": s.get("trap")})
    _alternate(out)
    return out


# ---------- mutations (on organizer-format turns) ----------

P = redteam.PHRASES
EXTRA = {
    "side_deal_soft": {
        "RU": "Кстати, если захотите, могу вести ваш портфель лично, беру небольшой процент. Напишите мне в WhatsApp, обсудим.",
        "KZ": "Айтпақшы, қаласаңыз портфеліңізді өзім жеке басқара аламын, аз ғана пайыз аламын. WhatsApp-қа жазыңыз.",
        "EN": "By the way, if you like I can manage your portfolio for you privately for a small fee. Message me on WhatsApp."},
    "lost": {"RU": "Соединение с клиентом потеряно.", "KZ": "Клиентпен байланыс үзілді.", "EN": "Connection to customer lost."},
}
WAIT_WORDS = ("минут", "момент", "ожида", "подожд", "күт", "minute", "moment", "wait", "hold")
_CLOCK = re.compile(r"^\d{1,3}:\d{2}$")


def _ops(turns):
    return [i for i, t in enumerate(turns) if str(t.get("speaker")).lower() == "operator"]


def _set(turns, i, text):
    out = [dict(t) for t in turns]
    out[i]["text"] = text
    return out


def _insert(turns, i, text):
    return turns[:i + 1] + [{**turns[i], "speaker": "operator", "text": text}] + turns[i + 1:]


def _mmss(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"


def mut_drop_greeting(t, lang, exp):
    return _set(t, _ops(t)[0], P["neutral_reply"][lang]), {"c01": {"no"}}


def mut_side_deal(t, lang, exp):
    return _insert(t, _ops(t)[0], P["side_deal"][lang]), {"c08": {"yes"}}


def mut_side_deal_soft(t, lang, exp):
    ops = _ops(t)
    return _insert(t, ops[-2] if len(ops) > 1 else ops[0], EXTRA["side_deal_soft"][lang]), {"c08": {"yes"}}


def mut_insult(t, lang, exp):
    return _insert(t, _ops(t)[0], P["insult"][lang]), {"c08": {"yes"}, "c05": {"no"}}


def mut_drop_farewell(t, lang, exp):
    return _set(t, _ops(t)[-1], P["ok"][lang]), {"c07": {"no", "na"}}


def mut_leak(t, lang, exp):
    if exp.get("c03", {}).get("label") != "na" or any(
            any(k in str(x.get("text", "")).lower() for k in redteam.VERIFIED_MARKERS + ("verified", "подтвержд"))
            for x in t if str(x.get("speaker")).lower() == "system"):
        return None
    return _insert(t, _ops(t)[0], P["leak"][lang]), {"c03": {"no"}}


def mut_client_left(t, lang, exp):
    i = _ops(t)[0]
    return t[:i + 1] + [{**t[i], "speaker": "system", "text": EXTRA["lost"][lang]}], {"c07": {"na"}}


def mut_wait(t, lang, exp):
    """5 silent minutes before an operator reply to a client message, with no warning: c06 can no longer be 10."""
    if exp.get("c06", {}).get("label") != "10" or not all(_CLOCK.match(str(x.get("timestamp") or "")) for x in t):
        return None
    ops = _ops(t)
    for j in ops[1:]:
        prev_op = max((k for k in ops if k < j), default=None)
        if str(t[j - 1].get("speaker")).lower() == "client" and prev_op is not None \
                and not any(w in str(t[prev_op]["text"]).lower() for w in WAIT_WORDS):
            out = [dict(x) for x in t]
            for x in out[j:]:
                m, s = str(x["timestamp"]).split(":")
                x["timestamp"] = _mmss(int(m) * 60 + int(s) + 300)
            return out, {"c06": {"7", "4"}}
    return None


def mut_harmless(t, lang, exp):
    """Sloppier client typing (lowercase, no final punctuation, one swapped letter pair). Changes nothing."""
    out = [dict(x) for x in t]
    hit = False
    for x in out:
        if str(x.get("speaker")).lower() == "client" and len(str(x["text"])) > 12:
            s = str(x["text"]).lower().rstrip(".!?")
            k = len(s) // 2
            x["text"] = s[:k] + s[k + 1:k + 2] + s[k:k + 1] + s[k + 2:] if s[k].isalpha() and s[k + 1].isalpha() else s
            hit = True
    return (out, {c: set(v["accept"]) for c, v in exp.items()}) if hit else None


MUTATIONS = {
    "drop_greeting": mut_drop_greeting, "side_deal": mut_side_deal, "side_deal_soft": mut_side_deal_soft,
    "insult": mut_insult, "drop_farewell": mut_drop_farewell, "leak_before_verification": mut_leak,
    "client_left": mut_client_left, "unannounced_wait": mut_wait, "harmless": mut_harmless,
}


def mutation_items(bases: list[dict], per_mutation: int) -> list[dict]:
    """Up to per_mutation items per mutation and split, spread over languages and sources."""
    out = []
    chats = [b for b in bases if b["channel"] == "agent chat" and len(_ops(b["record"]["transcript"])) >= 2]
    for name, fn in MUTATIONS.items():
        for split in ("tune", "holdout"):
            pool = sorted((b for b in chats if b["split"] == split), key=lambda b: (b["id"][-3:], b["id"]))
            taken = 0
            for b in pool:
                if taken >= per_mutation:
                    break
                lang = b["language"] if b["language"] in ("RU", "KZ", "EN") else "RU"
                res = fn(b["record"]["transcript"], lang, b["expect"])
                if not res:
                    continue
                turns, targets = res
                mid = f"{b['id']}~{name}"
                out.append({"id": mid, "source": "mutation", "mutation": name, "base": b["id"], "split": split,
                            "language": b["language"], "channel": b["channel"],
                            "record": {**b["record"], "conversation_id": mid, "transcript": turns},
                            "expect": {c: {"label": None, "accept": sorted(v)} for c, v in targets.items()}})
                taken += 1
    return out


def build(suites: list[str], split: str, per_mutation: int = 6) -> list[dict]:
    gold, syn = gold_items(), scenario_items()
    items = (gold if "gold" in suites else []) + (syn if "synthetic" in suites else [])
    if "mutations" in suites:
        items += mutation_items(gold + syn, per_mutation)
    return [it for it in items if split == "all" or it["split"] == split]


# ---------- running ----------

async def run_item(item: dict, cl: dict, approach: str, tag: str) -> dict:
    conv, msgs = importer.normalize(item["record"])
    # A mutation is judged only on the criteria it targets (cheaper; the rest is covered by its base dialog).
    criteria = [c for c in cl["criteria"] if item["source"] != "mutation" or c["id"] in item["expect"]]
    t0 = time.perf_counter()
    try:
        out, usage = await APPROACHES[approach](criteria, msgs, signals.compute(msgs), f"bench:{tag}:{item['id']}")
        error = None
    except Exception as exc:  # one broken item must not sink the run
        out, usage, error = {}, [], f"{type(exc).__name__}: {exc}"
    res = {k: item.get(k) for k in ("id", "source", "mutation", "base", "split", "language", "channel")}
    res |= {"seconds": round(time.perf_counter() - t0, 1), "usage": usage, "error": error, "criteria": {}}
    for c in criteria:
        r = out.get(c["id"]) or {}
        e = item["expect"].get(c["id"])
        final = pipeline.vote_label(r.get("ai"))
        res["criteria"][c["id"]] = {
            "expect": e["accept"] if e else None, "label": e["label"] if e else None,
            "final": final, "ok": (final in e["accept"]) if e else None,
            "votes": r.get("votes"), "confidence": r.get("confidence"), "status": r.get("status"),
            "rationale": r.get("rationale"), "evidence": r.get("evidence"), "checked_range": r.get("checked_range"),
            "na_reason": r.get("na_reason"), "errors": r.get("errors"), "trace_ids": r.get("trace_ids"),
        }
    if item["source"] != "mutation" and all(item["expect"].get(c["id"]) for c in cl["criteria"]):
        exp = scoring.score(cl, {c: _final(v["label"]) for c, v in item["expect"].items()})["total"]
        finals = {c: _final(v["final"]) for c, v in res["criteria"].items() if v["final"]}
        pred = scoring.score(cl, finals)["total"] if len(finals) == len(cl["criteria"]) else None
        res["total"] = {"expected": exp, "predicted": pred}
    return res


async def run(items: list[dict], cl: dict, approach: str = "panel", tag: str = "",
              checkpoint: Path | None = None) -> list[dict]:
    """Score all items. With `checkpoint` (a .jsonl file) every finished dialog is appended as it completes and a
    re-run skips dialogs already there without an error, so a killed run resumes where it stopped."""
    done: dict[str, dict] = {}
    if checkpoint and checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:  # a line cut by a kill
                continue
            if not r.get("error"):
                done[r["id"]] = r
    sem = asyncio.Semaphore(max(1, config.CONCURRENCY // 2))  # each dialog makes 2 parallel calls, then 1

    async def one(it):
        if it["id"] in done:
            return done[it["id"]]
        async with sem:
            r = await run_item(it, cl, approach, tag)
        if checkpoint:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            with checkpoint.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return r
    return await asyncio.gather(*(one(it) for it in items))


# ---------- metrics ----------

def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def _acc(rows) -> dict:
    """{criterion: [ok, n]} over scored cells."""
    acc = defaultdict(lambda: [0, 0])
    for r in rows:
        for cid, x in r["criteria"].items():
            if x["ok"] is not None:
                acc[cid][0] += x["ok"]
                acc[cid][1] += 1
    return acc


def _macro(acc) -> float | None:
    vals = [a / n for a, n in acc.values() if n]
    return round(100 * sum(vals) / len(vals), 1) if vals else None


def metrics(runs: list[list[dict]]) -> dict:
    rows = runs[0]
    acc = _acc(rows)
    cells = [(r, cid, x) for r in rows for cid, x in r["criteria"].items() if x["ok"] is not None]
    m = {
        "items": len(rows), "scored_cells": len(cells),
        "macro_acc": _macro(acc), "micro_acc": _pct(sum(x["ok"] for _, _, x in cells), len(cells)),
        "by_criterion": {cid: {"acc": _pct(a, n), "n": n} for cid, (a, n) in sorted(acc.items())},
        "by_source": {}, "by_language": {}, "by_mutation": {},
    }
    for key, field in (("by_source", "source"), ("by_language", "language")):
        groups = defaultdict(list)
        for r in rows:
            groups[r[field]].append(r)
        m[key] = {g: {"macro_acc": _macro(_acc(rs)), "items": len(rs)} for g, rs in sorted(groups.items())}
    muts = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["source"] == "mutation":
            ok = [x["ok"] for x in r["criteria"].values() if x["ok"] is not None]
            muts[r["mutation"]][0] += all(ok)
            muts[r["mutation"]][1] += 1
    m["by_mutation"] = {k: {"pass": _pct(a, n), "n": n} for k, (a, n) in sorted(muts.items())}

    roles = defaultdict(lambda: [0, 0])
    for _, _, x in cells:
        for role, v in (x["votes"] or {}).items():
            roles[role][0] += v in x["expect"]
            roles[role][1] += 1
    m["role_acc"] = {k: _pct(a, n) for k, (a, n) in roles.items()}

    c08 = [x for _, cid, x in cells if cid == "c08"]
    pos = [x for x in c08 if "yes" in x["expect"] and "no" not in x["expect"]]
    neg = [x for x in c08 if "no" in x["expect"] and "yes" not in x["expect"]]
    m["c08_recall"] = _pct(sum(x["final"] == "yes" for x in pos), len(pos))
    m["c08_false_alarm"] = _pct(sum(x["final"] == "yes" for x in neg), len(neg))
    m["c08_n"] = [len(pos), len(neg)]
    m["c08_missed"] = [r["id"] for r, cid, x in cells if cid == "c08" and x in pos and x["final"] != "yes"]

    m["na"] = {}
    for cid in NA_CRITERIA:
        xs = [x for _, c, x in cells if c == cid and x["label"]]
        tp = sum(x["final"] == "na" and x["label"] == "na" for x in xs)
        pp = sum(x["final"] == "na" for x in xs)
        ap = sum(x["label"] == "na" for x in xs)
        m["na"][cid] = {"precision": _pct(tp, pp), "recall": _pct(tp, ap), "gold_na": ap, "pred_na": pp}

    def near(x):  # scale: within one step of an accepted value
        try:
            return any(abs(float(x["final"]) - float(a)) <= 5.01 for a in x["expect"] if a != "na")
        except (TypeError, ValueError):
            return x["ok"]
    m["scale_within_one_step"] = {cid: _pct(sum(near(x) for _, c, x in cells if c == cid),
                                            sum(1 for _, c, _ in cells if c == cid)) for cid in ("c04",)}
    totals = [r["total"] for r in rows if r.get("total") and r["total"]["predicted"] is not None
              and r["total"]["expected"] is not None]
    m["total_mae"] = round(statistics.mean(abs(t["predicted"] - t["expected"]) for t in totals), 2) if totals else None

    auto = [x for _, _, x in cells if x["status"] == "auto_accepted"]
    m["auto_accept"] = {"coverage": _pct(len(auto), len(cells)), "acc": _pct(sum(x["ok"] for x in auto), len(auto))}
    review = [x for _, _, x in cells if x["status"] != "auto_accepted"]
    m["needs_review_acc"] = _pct(sum(x["ok"] for x in review), len(review))

    harmless = {r["base"]: r for r in rows if r.get("mutation") == "harmless"}
    by_id = {r["id"]: r for r in rows}
    flips = [(b, cid) for b, h in harmless.items() if b in by_id
             for cid, x in h["criteria"].items() if x["final"] != by_id[b]["criteria"][cid]["final"]]
    pairs = sum(len(h["criteria"]) for b, h in harmless.items() if b in by_id)
    m["harmless_flip_rate"] = _pct(len(flips), pairs)

    if len(runs) > 1:
        a, b = {r["id"]: r for r in runs[0]}, {r["id"]: r for r in runs[1]}
        diff = n = 0
        for i in a.keys() & b.keys():
            for cid in a[i]["criteria"]:
                n += 1
                diff += a[i]["criteria"][cid]["final"] != b[i]["criteria"][cid]["final"]
        m["repeat_flip_rate"] = _pct(diff, n)

    usage = [u for r in rows for u in r["usage"]]
    m["ops"] = {
        "failed_cells": sum(1 for r in rows for x in r["criteria"].values() if x["final"] is None),
        "item_errors": [r["id"] for r in rows if r["error"]],
        "validation_issues": sum(1 for r in rows for x in r["criteria"].values() if x["errors"]),
        "p50_seconds": statistics.median([r["seconds"] for r in rows]) if rows else None,
        "calls": len(usage), "tokens_in": sum(u["in"] for u in usage), "tokens_out": sum(u["out"] for u in usage),
        "cost_usd": llm.cost_usd(usage),
    }
    return m


# ---------- compare / show ----------

def cells_of(doc: dict) -> dict:
    return {(r["id"], cid): x for r in doc["runs"][0] for cid, x in r["criteria"].items() if x["ok"] is not None}


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided sign test on discordant pairs (b fixed, c broken)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def compare(a: dict, b: dict) -> dict:
    ca, cb = cells_of(a), cells_of(b)
    common = ca.keys() & cb.keys()
    fixed = sorted(k for k in common if not ca[k]["ok"] and cb[k]["ok"])
    broken = sorted(k for k in common if ca[k]["ok"] and not cb[k]["ok"])
    return {"common_cells": len(common), "fixed": fixed, "broken": broken, "p": round(mcnemar_p(len(fixed), len(broken)), 4)}


def save(path: Path, meta: dict, runs: list[list[dict]]) -> dict:
    doc = {"meta": meta, "metrics": metrics(runs), "runs": runs}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def merge(paths: list[Path], out: Path) -> dict:
    """Combine bench runs of the same configuration (e.g. a suite run later); later files win on duplicate ids."""
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    rows: dict[str, dict] = {}
    for d in docs:
        rows.update({r["id"]: r for r in d["runs"][0]})
    meta = docs[-1]["meta"] | {"merged_from": [str(p) for p in paths]}
    return save(out, meta, [list(rows.values())])
