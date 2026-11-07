"""Evaluations, appeals and human decisions. One card keeps three layers per criterion:
AI original (`ai`) -> reviewer decision (`decision`) -> final (`final`)."""
import asyncio
import hashlib
import json
import logging
import uuid

from . import checklist, config, db, importer, llm, pipeline, scoring, signals

log = logging.getLogger(__name__)
RUNS: dict[str, dict] = {}  # ponytail: in-memory batch progress; lost on restart, results are in the DB


def _cache_key(conv: dict, c: dict) -> str:
    parts = [conv["transcript_hash"], checklist.fingerprint(c), config.LLM_PROVIDER, config.MODELS,
             config.JUDGE_MODE, config.TRUST_THRESHOLD, config.PROMPT_VERSION]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def _criterion_meta(c: dict) -> dict:
    return {"criterion_id": c["id"], "title": c["title"], "section": c.get("section"), "type": c["type"],
            "weight": c["weight"], "na_allowed": bool(c.get("na_allowed")),
            "steps": [s["step"] for s in c.get("scale") or []]}


def _status(items: list[dict]) -> str:
    if any(i["status"] == "failed" for i in items):
        return "failed"
    if any(i["status"] == "needs_review" for i in items):
        return "needs_review"
    return "reviewed" if any(i.get("decision") for i in items) else "auto_accepted"


async def evaluate(conversation_id: str, version: int | None = None) -> int:
    conv = db.row("SELECT * FROM conversations WHERE id = %s", conversation_id)
    if not conv:
        raise LookupError(f"conversation {conversation_id} not found")
    cl = checklist.get(version)
    if not cl:
        raise LookupError(f"checklist version {version} not found")
    msgs = importer.messages(conversation_id)
    facts = signals.compute(msgs)

    results, todo = {}, []
    for c in cl["criteria"]:
        hit = db.row("SELECT value FROM cache WHERE key = %s", _cache_key(conv, c))
        if hit:
            results[c["id"]] = json.loads(hit["value"]) | {"cached": True}
        else:
            todo.append(c)
    usage = []
    if todo:  # incremental: only criteria whose wording changed (or new dialogs) reach the LLM
        fresh, usage = await pipeline.evaluate_criteria(todo, msgs, facts, conversation_id)
        with db.tx() as con:
            for c in todo:
                results[c["id"]] = fresh[c["id"]] | {"cached": False}
                if fresh[c["id"]]["status"] != "failed":
                    con.execute("INSERT INTO cache (key, value, created_at) VALUES (%s,%s,%s) ON CONFLICT (key)"
                                " DO UPDATE SET value = EXCLUDED.value, created_at = EXCLUDED.created_at",
                                (_cache_key(conv, c), db.dumps(fresh[c["id"]]), db.now()))

    items = [_criterion_meta(c) | results[c["id"]] | {"final": results[c["id"]]["ai"], "decision": None}
             for c in cl["criteria"]]
    finals = {i["criterion_id"]: i["final"] for i in items if i["final"]}
    score = scoring.score(cl, finals)
    result = {"criteria": items, "score_ai": score, "score": score, "facts": facts}
    with db.tx() as con:
        eval_id = con.execute(
            "INSERT INTO evaluations (conversation_id, checklist_version, status, total_ai, total, gross_error,"
            " models, prompt_version, tokens_in, tokens_out, cost_usd, result_json, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (conversation_id, cl["version"], _status(items), score["total"], score["total"], int(score["gross_error"]),
             db.dumps({"provider": config.LLM_PROVIDER, **config.MODELS, "judge_mode": config.JUDGE_MODE}),
             config.PROMPT_VERSION, sum(u["in"] for u in usage), sum(u["out"] for u in usage),
             llm.cost_usd(usage), db.dumps(result), db.now()),
        ).fetchone()["id"]
        db.audit(con, "system", "evaluation", eval_id, "evaluate",
                 after={"total": score["total"], "checklist_version": cl["version"]})
    log.info("evaluated conv=%s eval=%d v%d total=%s status=%s llm_criteria=%d cached=%d cost=$%.5f",
             conversation_id, eval_id, cl["version"], score["total"], _status(items), len(todo),
             len(items) - len(todo), llm.cost_usd(usage))
    return eval_id


def set_gold(conversation_id: str, criterion_id: str, label: str | None, who: str, note: str = "") -> dict:
    """Human reference answer for one criterion. Empty label removes it. The criterion fingerprint is stored,
    so a label stops counting once that criterion's wording changes."""
    cl = checklist.get()
    c = next((x for x in cl["criteria"] if x["id"] == criterion_id), None)
    if not c:
        raise LookupError("criterion not in the active checklist")
    if not db.row("SELECT id FROM conversations WHERE id = %s", conversation_id):
        raise LookupError("conversation not found")
    with db.tx() as con:
        if not label:
            con.execute("DELETE FROM gold_labels WHERE conversation_id = %s AND criterion_id = %s", (conversation_id, criterion_id))
            db.audit(con, who, "gold", f"{conversation_id}/{criterion_id}", "delete")
            return {"conversation_id": conversation_id, "criterion_id": criterion_id, "label": None}
        allowed = (["na"] if c.get("na_allowed") else []) + (
            [f"{float(s['step']):g}" for s in c["scale"]] if c["type"] == "scale" else ["yes", "no"])
        if label not in allowed:
            raise ValueError(f"label must be one of {allowed}")
        con.execute("INSERT INTO gold_labels (conversation_id, criterion_id, label, fingerprint, note, labelled_by, at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (conversation_id, criterion_id) DO UPDATE SET"
                    " label = EXCLUDED.label, fingerprint = EXCLUDED.fingerprint, note = EXCLUDED.note,"
                    " labelled_by = EXCLUDED.labelled_by, at = EXCLUDED.at",
                    (conversation_id, criterion_id, label, checklist.fingerprint(c), note, who, db.now()))
        db.audit(con, who, "gold", f"{conversation_id}/{criterion_id}", "set", after=label, reason=note)
    return {"conversation_id": conversation_id, "criterion_id": criterion_id, "label": label}


def load(eval_id: int) -> dict | None:
    ev = db.row("SELECT * FROM evaluations WHERE id = %s", eval_id)
    if not ev:
        return None
    ev["result"] = json.loads(ev.pop("result_json"))
    ev["models"] = json.loads(ev["models"])
    ev["appeals"] = db.rows("SELECT * FROM appeals WHERE evaluation_id = %s ORDER BY id", eval_id)
    return ev


def _check_final(item: dict, verdict: str, step) -> dict:
    if verdict == "na":
        if not item["na_allowed"]:
            raise ValueError("N/A is not allowed for this criterion")
        return {"verdict": "na", "step": None}
    if item["type"] == "scale":
        if verdict != "scored" or step is None or float(step) not in [float(s) for s in item["steps"]]:
            raise ValueError(f"choose one of the steps {item['steps']}")
        return {"verdict": "scored", "step": float(step)}
    if verdict not in ("yes", "no"):
        raise ValueError("verdict must be yes or no")
    return {"verdict": verdict, "step": None}


def decide(eval_id: int, criterion_id: str, action: str, verdict: str | None, step, reason: str,
           who: str, appeal_id: int | None = None) -> dict:
    """Reviewer upholds or changes one criterion. The same scoring code recalculates the total."""
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    if action not in ("uphold", "change"):
        raise ValueError("action must be uphold or change")
    ev = load(eval_id)
    if not ev:
        raise LookupError("evaluation not found")
    item = next((i for i in ev["result"]["criteria"] if i["criterion_id"] == criterion_id), None)
    if not item:
        raise LookupError("criterion not in this evaluation")
    before = item["final"]
    if action == "change":
        item["final"] = _check_final(item, verdict, step)
    elif before is None:
        raise ValueError("nothing to uphold: the AI produced no verdict, use change")
    item["decision"] = {"action": action, "by": who, "reason": reason, "at": db.now(),
                        "before": before, "after": item["final"], "appeal_id": appeal_id}
    if item["status"] in ("needs_review", "failed"):
        item["status"] = "reviewed"
    cl = checklist.get(ev["checklist_version"])
    finals = {i["criterion_id"]: i["final"] for i in ev["result"]["criteria"] if i["final"]}
    ev["result"]["score"] = scoring.score(cl, finals)
    score = ev["result"]["score"]
    with db.tx() as con:
        con.execute("UPDATE evaluations SET result_json = %s, total = %s, gross_error = %s, status = %s WHERE id = %s",
                    (db.dumps(ev["result"]), score["total"], int(score["gross_error"]),
                     _status(ev["result"]["criteria"]), eval_id))
        if appeal_id:
            con.execute("UPDATE appeals SET state = %s, reason = %s, decided_by = %s, decided_at = %s WHERE id = %s",
                        ("changed" if action == "change" else "upheld", reason, who, db.now(), appeal_id))
        db.audit(con, who, "evaluation", eval_id, f"decision:{criterion_id}:{action}",
                 before=before, after=item["final"], reason=reason)
    return load(eval_id)


def file_appeal(eval_id: int, criterion_id: str, comment: str, who: str) -> dict:
    ev = load(eval_id)
    if not ev:
        raise LookupError("evaluation not found")
    if not any(i["criterion_id"] == criterion_id for i in ev["result"]["criteria"]):
        raise LookupError("criterion not in this evaluation")
    if not (comment or "").strip():
        raise ValueError("a comment is required")
    if any(a["criterion_id"] == criterion_id and a["state"] == "submitted" for a in ev["appeals"]):
        raise ValueError("an appeal for this criterion is already open")
    with db.tx() as con:
        appeal_id = con.execute(
            "INSERT INTO appeals (evaluation_id, criterion_id, comment, filed_by, state, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (eval_id, criterion_id, comment, who, "submitted", db.now())).fetchone()["id"]
        db.audit(con, who, "appeal", appeal_id, "submit", after={"criterion": criterion_id}, reason=comment)
    return db.row("SELECT * FROM appeals WHERE id = %s", appeal_id)


def review_queue() -> dict:
    appeals = db.rows(
        "SELECT a.*, e.conversation_id FROM appeals a JOIN evaluations e ON e.id = a.evaluation_id"
        " WHERE a.state = 'submitted' ORDER BY a.id")
    low = []
    latest = db.rows("SELECT * FROM evaluations WHERE id IN (SELECT max(id) FROM evaluations GROUP BY conversation_id)"
                     " AND status IN ('needs_review', 'failed') ORDER BY id")
    for ev in latest:
        for item in json.loads(ev["result_json"])["criteria"]:
            if item["status"] in ("needs_review", "failed") and not item.get("decision"):
                low.append({"evaluation_id": ev["id"], "conversation_id": ev["conversation_id"],
                            "criterion_id": item["criterion_id"], "title": item["title"],
                            "confidence": item["confidence"], "status": item["status"], "votes": item["votes"]})
    return {"appeals": appeals, "low_confidence": low}


async def _run(run_id: str, conversation_ids: list[str], version: int | None) -> None:
    state = RUNS[run_id]

    async def one(cid):
        try:
            await evaluate(cid, version)
            state["done"] += 1
        except Exception as exc:  # one bad dialog must not stop the batch
            log.exception("evaluation of %s failed", cid)
            state["failed"].append({"conversation_id": cid, "error": str(exc)})

    await asyncio.gather(*(one(c) for c in conversation_ids))  # parallelism is capped in pipeline.call_role
    state["status"] = "finished"
    state["finished_at"] = db.now()


def start_run(conversation_ids: list[str], version: int | None) -> dict:
    run_id = uuid.uuid4().hex[:8]
    RUNS[run_id] = {"id": run_id, "status": "running", "total": len(conversation_ids), "done": 0, "failed": [],
                    "checklist_version": version or checklist.get()["version"], "started_at": db.now()}
    RUNS[run_id]["_task"] = asyncio.create_task(_run(run_id, conversation_ids, version))
    return public_run(run_id)


def public_run(run_id: str) -> dict | None:
    run = RUNS.get(run_id)
    return {k: v for k, v in run.items() if not k.startswith("_")} if run else None


def diff(v_from: int, v_to: int) -> list[dict]:
    """Totals per dialog under two checklist versions (latest evaluation of each)."""
    rows = db.rows(
        "SELECT conversation_id, checklist_version, total FROM evaluations WHERE id IN ("
        " SELECT max(id) FROM evaluations WHERE checklist_version IN (%s, %s) GROUP BY conversation_id, checklist_version)",
        v_from, v_to)
    table: dict[str, dict] = {}
    for r in rows:
        key = "from" if r["checklist_version"] == v_from else "to"
        table.setdefault(r["conversation_id"], {"conversation_id": r["conversation_id"]})[key] = r["total"]
    out = sorted(table.values(), key=lambda x: x["conversation_id"])
    for r in out:
        a, b = r.get("from"), r.get("to")
        r["delta"] = round(b - a, 2) if a is not None and b is not None else None
    return out
