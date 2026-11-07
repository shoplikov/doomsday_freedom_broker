"""Features that make the AI's verdicts trustworthy and the checklist better, not just applied.

1. Stress test: break a dialog on purpose (side deal, insult, leak, no greeting, 5-minute silence, or your own
   operator line) and watch whether the panel notices. The correct answer is known by construction.
2. Checklist health: per criterion, how often the three roles disagree, how often humans overturned the AI.
   A criterion the models keep disagreeing on is usually worded ambiguously.
3. Test drive: run a draft criterion on a few dialogs BEFORE publishing it, and see which verdicts would change.
4. Review impact: how many points each uncertain verdict can move the total, so reviewers start with what matters.
"""
import asyncio
import json
import re
from datetime import datetime, timedelta

from . import checklist, db, importer, pipeline, redteam, scoring, signals

# ---------- 1. stress test ----------

WAIT_WORDS = ("минут", "момент", "ожида", "подожд", "провер", "уточн", "күт", "тексер", "minute", "moment", "wait",
              "hold", "check")


def _mark(m: dict) -> dict:
    return {**m, "changed": True}


def s_side_deal(msgs, lang, _):
    out = redteam.m_side_deal(msgs, lang)
    i = redteam._ops(msgs)[0] + 1
    out[i] = _mark(out[i])
    return out


def s_insult(msgs, lang, _):
    out = redteam.m_insult(msgs, lang)
    i = redteam._ops(msgs)[0] + 1
    out[i] = _mark(out[i])
    return out


def s_leak(msgs, lang, _):
    out = redteam.m_leak_before_verification(msgs, lang)
    if out is None:
        return None
    i = redteam._ops(msgs)[0] + 1
    out[i] = _mark(out[i])
    return out


def s_drop_greeting(msgs, lang, _):
    out = redteam.m_drop_greeting(msgs, lang)
    i = redteam._ops(msgs)[0]
    out[i] = _mark(out[i])
    return out


def s_drop_farewell(msgs, lang, _):
    out = redteam.m_drop_farewell(msgs, lang)
    i = redteam._ops(msgs)[-1]
    out[i] = _mark(out[i])
    return out


def _shift_ts(raw, by: int = 300):
    """The same timestamp `by` seconds later, in its original format (MM:SS, HH:MM:SS or ISO 8601)."""
    s = str(raw or "").strip()
    if re.match(r"^\d+:\d{2}(:\d{2})?$", s):
        total = int(importer.parse_ts(s)) + by
        h, rest = divmod(total, 3600)
        return f"{h:02d}:{rest // 60:02d}:{rest % 60:02d}" if h or s.count(":") == 2 else f"{rest // 60:02d}:{rest % 60:02d}"
    try:
        return (datetime.fromisoformat(s.replace("Z", "+00:00")) + timedelta(seconds=by)).isoformat()
    except ValueError:
        return raw


def s_long_wait(msgs, lang, _):
    """The operator answers a client message 5 minutes later, without warning. Everything after shifts too."""
    ops = redteam._ops(msgs)
    for k in range(len(msgs) - 1):
        a, b = msgs[k], msgs[k + 1]
        if a["speaker"] != "client" or b["speaker"] != "operator" or not ops or k < ops[0] or a.get("t_sec") is None:
            continue
        prev = [msgs[j] for j in ops if j < k]
        if prev and any(w in prev[-1]["text"].lower() for w in WAIT_WORDS):
            continue
        out = list(msgs[:k + 1])
        for j, m in enumerate(msgs[k + 1:], start=k + 1):
            t = None if m.get("t_sec") is None else m["t_sec"] + 300
            m2 = {**m, "t_sec": t, "ts_raw": _shift_ts(m["ts_raw"])}
            out.append(_mark(m2) if j == k + 1 else m2)
        return out
    return None


def s_custom(msgs, lang, opts):
    text = (opts.get("text") or "").strip()
    if not text:
        raise ValueError("Напишите реплику оператора, которую нужно вставить")
    mids = [m["mid"] for m in msgs]
    after = opts.get("after_mid") or (msgs[redteam._ops(msgs)[0]]["mid"] if redteam._ops(msgs) else mids[-1])
    if after not in mids:
        raise ValueError(f"нет сообщения {after}")
    i = mids.index(after)
    return msgs[:i + 1] + [_mark({**msgs[i], "speaker": "operator", "text": text[:1000]})] + msgs[i + 1:]


# expect: {criterion_id: labels the panel must give}; None = no known answer, show before/after for `check`.
# ponytail: expectations name criterion ids of the organizers' checklist; ids missing from the active checklist are
# skipped, so a renamed criterion simply drops out of the stress test.
STRESS = {
    "side_deal": {"title": "Предложение «в обход компании»", "fn": s_side_deal, "expect": {"c08": {"yes"}}},
    "insult": {"title": "Оскорбление клиента", "fn": s_insult, "expect": {"c08": {"yes"}, "c05": {"no"}}},
    "leak": {"title": "Данные клиента без проверки личности", "fn": s_leak, "expect": {"c03": {"no"}}},
    "drop_greeting": {"title": "Нет приветствия и имени", "fn": s_drop_greeting, "expect": {"c01": {"no"}}},
    "drop_farewell": {"title": "Нет завершения", "fn": s_drop_farewell, "expect": {"c07": {"no", "na"}}},
    "long_wait": {"title": "5 минут молчания без предупреждения", "fn": s_long_wait,
                  "expect": {"c06": {"7", "4", "0"}}},
    "custom": {"title": "Своя реплика оператора", "fn": s_custom, "expect": None, "check": ("c03", "c05", "c06", "c08")},
}


def _labels(ev: dict | None) -> dict:
    if not ev:
        return {}
    return {i["criterion_id"]: pipeline.vote_label(i.get("final") or i.get("ai"))
            for i in json.loads(ev["result_json"])["criteria"]}


async def stress_test(conversation_id: str, mutation: str, opts: dict | None = None) -> dict:
    spec = STRESS.get(mutation)
    if not spec:
        raise ValueError(f"unknown stress test '{mutation}'")
    conv = db.row("SELECT * FROM conversations WHERE id = %s", conversation_id)
    if not conv:
        raise LookupError(f"conversation {conversation_id} not found")
    msgs = importer.messages(conversation_id)
    if not redteam._ops(msgs):
        raise ValueError("в диалоге нет реплик оператора: ломать нечего")
    lang = conv["language"] if conv["language"] in ("RU", "KZ", "EN") else "RU"
    mutated = spec["fn"](msgs, lang, opts or {})
    if mutated is None:
        raise ValueError("этот дефект не подходит к диалогу (например, личность уже подтверждена)")
    cl = checklist.get()
    by_id = {c["id"]: c for c in cl["criteria"]}
    targets = spec["expect"] or {cid: None for cid in spec["check"]}
    criteria = [by_id[c] for c in targets if c in by_id]
    if not criteria:
        raise ValueError("в активном чек-листе нет критериев для этой проверки")
    st_id = f"{conversation_id}~stress-{mutation}"
    new = redteam._renumber(st_id, mutated)
    out, usage = await pipeline.evaluate_criteria(criteria, new, signals.compute(new), st_id)
    before = _labels(db.row("SELECT result_json FROM evaluations WHERE conversation_id = %s ORDER BY id DESC LIMIT 1",
                            conversation_id))
    checks = []
    for c in criteria:
        r = out[c["id"]]
        after = pipeline.vote_label(r["ai"])
        expected = targets[c["id"]]
        checks.append({"criterion_id": c["id"], "title": c["title"], "type": c["type"], "expected": sorted(expected) if expected else None,
                       "before": before.get(c["id"]), "after": after,
                       "ok": (after in expected) if expected else None, "votes": r["votes"],
                       "confidence": r["confidence"], "rationale": r["rationale"], "evidence": r["evidence"],
                       "na_reason": r["na_reason"]})
    changed = [{"mid": m["mid"], "speaker": m["speaker"], "ts_raw": m["ts_raw"], "text": m["text_masked"]}
               for m in new if m.get("changed")]
    result = {"conversation_id": conversation_id, "mutation": mutation, "title": spec["title"],
              "checklist_version": cl["version"], "changed": changed, "checks": checks,
              "ok": all(x["ok"] for x in checks) if spec["expect"] else None,
              "tokens": sum(u["in"] + u["out"] for u in usage)}
    with db.tx() as con:
        result["id"] = con.execute(
            "INSERT INTO stress_tests (conversation_id, mutation, result_json, created_at) VALUES (%s,%s,%s,%s)"
            " RETURNING id", (conversation_id, mutation, db.dumps(result), db.now())).fetchone()["id"]
    return result


def stress_tests(conversation_id: str | None = None) -> list[dict]:
    rows = db.rows("SELECT id, result_json, created_at FROM stress_tests WHERE %s::text IS NULL OR conversation_id = %s"
                   " ORDER BY id DESC LIMIT 200", conversation_id, conversation_id)
    return [json.loads(r["result_json"]) | {"id": r["id"], "created_at": r["created_at"]} for r in rows]


def stress_summary() -> dict:
    """Pass rate of all stress tests with a known answer, per defect type."""
    by = {}
    for t in stress_tests():
        if t["ok"] is None:
            continue
        s = by.setdefault(t["mutation"], {"title": t["title"], "runs": 0, "passed": 0})
        s["runs"] += 1
        s["passed"] += bool(t["ok"])
    return {"by_mutation": by, "runs": sum(s["runs"] for s in by.values()),
            "passed": sum(s["passed"] for s in by.values())}


# ---------- 2. checklist health ----------

SPLIT_ALERT = 0.25  # roles disagree on >= 25% of dialogs -> the wording is probably ambiguous
MIN_DIALOGS = 5


def checklist_health() -> dict:
    cl = checklist.get()
    latest = db.rows("SELECT id, conversation_id, result_json FROM evaluations WHERE id IN"
                     " (SELECT max(id) FROM evaluations WHERE checklist_version = %s GROUP BY conversation_id)",
                     cl["version"])
    appeals = db.rows("SELECT a.criterion_id, a.state FROM appeals a JOIN evaluations e ON e.id = a.evaluation_id"
                      " WHERE e.checklist_version = %s", cl["version"])
    stats = {c["id"]: {"criterion_id": c["id"], "title": c["title"], "dialogs": 0, "split": 0, "needs_review": 0,
                       "na": 0, "human_changed": 0, "appeals": 0, "appeals_won": 0, "examples": []}
             for c in cl["criteria"]}
    for ev in latest:
        for item in json.loads(ev["result_json"])["criteria"]:
            s = stats.get(item["criterion_id"])
            if not s or not item.get("ai"):
                continue
            s["dialogs"] += 1
            votes = [v for v in (item.get("votes") or {}).values() if v]
            if len(set(votes)) > 1:
                s["split"] += 1
                if len(s["examples"]) < 5:
                    s["examples"].append({"conversation_id": ev["conversation_id"], "votes": item["votes"]})
            s["needs_review"] += item.get("status") == "needs_review"
            s["na"] += (item.get("ai") or {}).get("verdict") == "na"
            s["human_changed"] += bool(item.get("decision")) and item.get("final") != item.get("ai")
    for a in appeals:
        if a["criterion_id"] in stats:
            stats[a["criterion_id"]]["appeals"] += 1
            stats[a["criterion_id"]]["appeals_won"] += a["state"] == "changed"
    out = []
    for s in stats.values():
        n = s["dialogs"]
        s["split_rate"] = round(s["split"] / n, 3) if n else None
        s["na_rate"] = round(s["na"] / n, 3) if n else None
        s["alert"] = bool(n >= MIN_DIALOGS and s["split"] / n >= SPLIT_ALERT) or s["appeals_won"] >= 2
        out.append(s)
    return {"checklist_version": cl["version"], "dialogs": len(latest), "criteria": out,
            "split_alert": SPLIT_ALERT, "min_dialogs": MIN_DIALOGS}


# ---------- 3. test drive ----------

async def test_drive(criterion: dict, conversation_ids: list[str] | None = None, limit: int = 5) -> dict:
    """Evaluate one draft criterion on a few dialogs without saving anything (traces are kept)."""
    cl = checklist.get()
    draft = {**cl, "criteria": [criterion]}
    errors, warnings = checklist.lint(draft)
    warnings = [w for w in warnings if not w.startswith("scored weights")]  # one criterion alone never sums to 100
    if errors:
        raise ValueError("; ".join(errors))
    cid = criterion["id"]
    latest = db.rows("SELECT conversation_id, result_json FROM evaluations WHERE id IN"
                     " (SELECT max(id) FROM evaluations GROUP BY conversation_id) ORDER BY id DESC")
    before = {r["conversation_id"]: _labels(r).get(cid) for r in latest}
    if not conversation_ids:  # dialogs the models argued about for this criterion first, then the latest ones
        split = [r["conversation_id"] for r in latest
                 if any(i["criterion_id"] == cid and len({v for v in (i.get("votes") or {}).values() if v}) > 1
                        for i in json.loads(r["result_json"])["criteria"])]
        rest = [r["conversation_id"] for r in latest if r["conversation_id"] not in split]
        conversation_ids = (split + rest)[:limit] or [r["id"] for r in db.rows(
            "SELECT id FROM conversations ORDER BY id LIMIT %s", limit)]
    conversation_ids = conversation_ids[:10]

    async def one(conv_id):
        msgs = importer.messages(conv_id)
        if not msgs:
            return {"conversation_id": conv_id, "error": "диалог не найден"}
        out, _ = await pipeline.evaluate_criteria([criterion], msgs, signals.compute(msgs), f"testdrive:{conv_id}")
        r = out[cid]
        return {"conversation_id": conv_id, "before": before.get(conv_id), "after": pipeline.vote_label(r["ai"]),
                "status": r["status"], "votes": r["votes"], "confidence": r["confidence"],
                "rationale": r["rationale"], "evidence": r["evidence"], "na_reason": r["na_reason"]}

    rows = await asyncio.gather(*(one(c) for c in conversation_ids))
    done = [r for r in rows if "after" in r]
    return {"criterion_id": cid, "warnings": warnings, "rows": rows,
            "changed": sum(1 for r in done if r["before"] is not None and r["before"] != r["after"]),
            "unanimous": sum(1 for r in done if r["confidence"] >= 0.99), "dialogs": len(done)}


# ---------- 4. review impact ----------

def _final_from_label(label: str) -> dict:
    return {"verdict": label, "step": None} if label in ("yes", "no", "na") else {"verdict": "scored",
                                                                                  "step": float(label)}


def review_impact(ev: dict, criterion_id: str) -> float | None:
    """Largest change of the total if this verdict were any other answer the roles gave."""
    result = json.loads(ev["result_json"])
    cl = checklist.get(ev["checklist_version"])
    if not cl:
        return None
    finals = {i["criterion_id"]: i["final"] for i in result["criteria"] if i.get("final")}
    item = next((i for i in result["criteria"] if i["criterion_id"] == criterion_id), None)
    if not item:
        return None
    base = scoring.score(cl, finals)["total"]
    current = pipeline.vote_label(item.get("final"))
    best = 0.0
    for alt in {v for v in (item.get("votes") or {}).values() if v and v != current}:
        try:
            total = scoring.score(cl, finals | {criterion_id: _final_from_label(alt)})["total"]
        except (ValueError, KeyError):
            continue
        if base is not None and total is not None:
            best = max(best, abs(total - base))
    return round(best, 2)
