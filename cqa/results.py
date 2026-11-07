"""Organisers' results file, format 1.0 (initial_docs/CQA_Student_Guide_Appendix_Results.pdf).

results/<dev|prod>/results.json: one object per dialog of a group, latest evaluation, human decisions included.
Values are the final layer (AI -> reviewer decision -> final); points come from cqa/scoring.py as everywhere else.
"""
import json
import re
from pathlib import Path

from . import config, db, evaluations, importer

SCHEMA_VERSION = "1.0"
# Our checklist ids -> the organisers' criterion codes (same order and weights as the checklist PDF).
CODES = {"c01": "contact", "c02": "discovery", "c03": "identification", "c04": "consultation",
         "c05": "empathy", "c06": "dialogue", "c07": "closure", "c08": "critical"}
_MID = re.compile(r"^m(\d+)$")


def _value(item: dict):
    f = item.get("final")
    if not f or f["verdict"] == "na":
        return None
    if f["verdict"] == "scored":
        return int(f["step"]) if float(f["step"]).is_integer() else f["step"]
    return f["verdict"] == "yes"


def _turns(item: dict, speakers: list[str]) -> list[int]:
    """1-based positions in the original transcript array (our mid m<N> is exactly that).
    Evidence quotes first, else the checked range. A criterion with neither (N/A, "no gross errors")
    gets the turns that were checked: all operator turns for `critical`, the client's request for N/A."""
    mids = [e.get("msg") for e in item.get("evidence") or []] or list(item.get("checked_range") or [])
    nums = {int(m.group(1)) for x in mids if isinstance(x, str) and (m := _MID.match(x))}
    if len(mids) == 2 and not item.get("evidence") and len(nums) == 2:  # checked_range: the whole span
        nums = set(range(min(nums), max(nums) + 1))
    if not nums:
        if item["criterion_id"] == "c08":
            nums = {i + 1 for i, sp in enumerate(speakers) if sp == "operator"}
        else:  # client turns before the operator's first reply
            first_op = next((i for i, sp in enumerate(speakers) if sp == "operator"), len(speakers))
            nums = {i + 1 for i, sp in enumerate(speakers[:first_op]) if sp == "client"}
    return sorted(nums)


def _rationale(item: dict) -> str:
    d = item.get("decision")
    if d and d.get("reason"):
        return f"Решение проверяющего: {d['reason']}"
    return (item.get("rationale") or item.get("na_reason") or "").strip()


def _error(cid: str, msg: str) -> dict:
    return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": msg}


def _record(cid: str, ev: dict | None, speakers: list[str] = ()) -> dict:
    if not ev:
        return _error(cid, "Диалог не оценён.")
    items = {i["criterion_id"]: i for i in ev["result"]["criteria"]}
    missing = [code for k, code in CODES.items() if k not in items or not items[k].get("final")]
    if missing:
        return _error(cid, "Нет итогового ответа модели по критериям: " + ", ".join(missing))
    score = ev["result"]["score"]
    if score.get("total") is None:
        return _error(cid, f"Итог не посчитан: {score.get('total_note')}")
    return {
        "conversation_id": cid,
        "status": "ok",
        "criteria": [{"criterion_id": code, "value": _value(items[k]), "rationale": _rationale(items[k]),
                      "evidence_turns": _turns(items[k], list(speakers))} for k, code in CODES.items()],
        "score": {"final_score": score["total"], "critical_flag": _value(items["c08"]) is True},
    }


def _model(evals: list[dict]) -> dict:
    names, modes = [], set()
    for ev in evals:
        for role in ("inspector", "advocate", "judge"):
            name = ev["models"].get(role)
            if name and name not in names:
                names.append(name)
                modes.add("external_api" if name.startswith(("gpt-", "o1", "o3", "o4")) else "local")
    mode = "hybrid" if len(modes) > 1 else (modes.pop() if modes else "local")
    return {"mode": mode, "name": ", ".join(names) or "none"}


def build(group_id: str, team_name: str) -> dict:
    ids = [r["id"] for r in db.rows("SELECT id FROM conversations WHERE group_id = %s ORDER BY id", group_id)]
    if not ids:
        raise LookupError(f"no conversations in group '{group_id}': import it first")
    evals = {}
    for cid in ids:
        last = db.row("SELECT max(id) AS id FROM evaluations WHERE conversation_id = %s", cid)
        evals[cid] = evaluations.load(last["id"]) if last and last["id"] else None
    return {"schema_version": SCHEMA_VERSION, "team_name": team_name,
            "model": _model([e for e in evals.values() if e]),
            "results": [_record(cid, evals[cid], [m["speaker"] for m in importer.messages(cid)]) for cid in ids]}


def write(group_id: str, out: Path, team_name: str | None = None) -> dict:
    doc = build(group_id, team_name or config.TEAM_NAME)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc
