"""Is the three-model panel worth it? Numbers over the latest evaluation of every dialog for one checklist version.

Per criterion:
  ab_agreement   M1 Inspector and M2 Advocate (independent) gave the same answer
  judge_sided    when the judge ruled: with both / inspector only / advocate only / neither
  accuracy       vs human gold labels, for each role alone and for the panel's final AI answer.
                 Panel accuracy > best single role = the idea pays off.
  human_changed  reviewer decisions that changed the AI answer
"""
import json
from collections import Counter, defaultdict

from . import checklist, db, pipeline

ROLES = ("inspector", "advocate", "judge")


def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def compute(version: int | None = None) -> dict:
    cl = checklist.get(version)
    evals = db.rows(
        "SELECT e.*, c.language, c.channel FROM evaluations e JOIN conversations c ON c.id = e.conversation_id"
        " WHERE e.id IN (SELECT max(id) FROM evaluations WHERE checklist_version = ? GROUP BY conversation_id)",
        cl["version"])
    gold = {(g["conversation_id"], g["criterion_id"]): g for g in db.rows("SELECT * FROM gold_labels")}
    fingerprints = {c["id"]: checklist.fingerprint(c) for c in cl["criteria"]}

    per = {c["id"]: defaultdict(int) | {"criterion_id": c["id"], "title": c["title"], "type": c["type"],
                                        "final_dist": Counter(), "confidence_sum": 0.0}
           for c in cl["criteria"]}
    lang = defaultdict(lambda: defaultdict(int))
    disagreements, gold_misses = [], []
    split = defaultdict(int)

    for ev in evals:
        for item in json.loads(ev["result_json"])["criteria"]:
            p = per.get(item["criterion_id"])
            if p is None:
                continue
            votes = item.get("votes") or {}
            final = pipeline.vote_label(item.get("ai"))
            L = lang[ev["language"] or "?"]
            p["n"] += 1
            L["n"] += 1
            p["confidence_sum"] += item.get("confidence") or 0
            p["final_dist"][final or "none"] += 1
            p[item["status"]] += 1
            if item.get("errors"):
                p["validation_issues"] += 1
            if item.get("decision") and item["decision"]["before"] != item["decision"]["after"]:
                p["human_changed"] += 1
            a, b, j = votes.get("inspector"), votes.get("advocate"), votes.get("judge")
            if a is not None and b is not None:
                p["ab_pairs"] += 1
                L["ab_pairs"] += 1
                if a == b:
                    p["ab_agree"] += 1
                    L["ab_agree"] += 1
                else:
                    disagreements.append({"conversation_id": ev["conversation_id"], "language": ev["language"],
                                          "criterion_id": item["criterion_id"], "votes": votes, "final": final,
                                          "confidence": item.get("confidence"), "status": item["status"]})
            if j is not None:
                p["judged"] += 1
                side = ("both" if j == a == b else "inspector" if j == a else "advocate" if j == b else "neither")
                p[f"judge_{side}"] += 1
            g = gold.get((ev["conversation_id"], item["criterion_id"]))
            if g and g["fingerprint"] == fingerprints[item["criterion_id"]]:
                p["gold_n"] += 1
                L["gold_n"] += 1
                for role in ROLES:
                    if votes.get(role) is not None:
                        p[f"gold_{role}_n"] += 1
                        p[f"gold_{role}_ok"] += votes[role] == g["label"]
                p["gold_final_ok"] += final == g["label"]
                L["gold_final_ok"] += final == g["label"]
                if a is not None and b is not None:  # is M1 = M2 a valid trust signal?
                    bucket = "agreed" if a == b else "split"
                    split[f"{bucket}_n"] += 1
                    split[f"{bucket}_ok"] += final == g["label"]
                if final != g["label"]:
                    gold_misses.append({"conversation_id": ev["conversation_id"], "criterion_id": item["criterion_id"],
                                        "gold": g["label"], "final": final, "votes": votes})

    criteria = []
    for p in per.values():
        n = p["n"]
        criteria.append({
            "criterion_id": p["criterion_id"], "title": p["title"], "type": p["type"], "n": n,
            "ab_agreement_pct": _pct(p["ab_agree"], p["ab_pairs"]),
            "judged": p["judged"],
            "judge_sided": {k: p[f"judge_{k}"] for k in ("both", "inspector", "advocate", "neither")},
            "avg_confidence": round(p["confidence_sum"] / n, 2) if n else None,
            "auto_accepted": p["auto_accepted"], "needs_review": p["needs_review"],
            "failed": p["failed"], "reviewed": p["reviewed"],
            "validation_issues": p["validation_issues"], "human_changed": p["human_changed"],
            "final_dist": dict(p["final_dist"]),
            "gold_n": p["gold_n"],
            "accuracy_pct": {**{role: _pct(p[f"gold_{role}_ok"], p[f"gold_{role}_n"]) for role in ROLES},
                             "panel": _pct(p["gold_final_ok"], p["gold_n"])},
        })

    conv_ids = [ev["conversation_id"] for ev in evals]
    calls = db.rows(
        "SELECT role, count(*) AS calls, sum(attempt > 1) AS retries, sum(error IS NOT NULL) AS errors,"
        " avg(latency_ms) AS avg_ms, max(latency_ms) AS max_ms, sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out"
        f" FROM traces WHERE conversation_id IN ({','.join('?' * len(conv_ids)) or 'NULL'}) GROUP BY role",
        *conv_ids)
    total_gold = sum(c["gold_n"] for c in criteria)
    panel_ok = sum(per[c["criterion_id"]]["gold_final_ok"] for c in criteria)
    role_acc = {role: _pct(sum(per[c["criterion_id"]][f"gold_{role}_ok"] for c in criteria),
                           sum(per[c["criterion_id"]][f"gold_{role}_n"] for c in criteria)) for role in ROLES}
    return {
        "checklist_version": cl["version"],
        "dialogs": len(evals),
        "status": dict(Counter(ev["status"] for ev in evals)),
        "cost_usd": round(sum(ev["cost_usd"] or 0 for ev in evals), 4),
        "avg_cost_usd": round(sum(ev["cost_usd"] or 0 for ev in evals) / len(evals), 5) if evals else None,
        "avg_total": round(sum(ev["total"] for ev in evals if ev["total"] is not None)
                           / max(1, sum(ev["total"] is not None for ev in evals)), 2) if evals else None,
        "gross_errors": sum(ev["gross_error"] or 0 for ev in evals),
        "ab_agreement_pct": _pct(sum(per[c["criterion_id"]]["ab_agree"] for c in criteria),
                                 sum(per[c["criterion_id"]]["ab_pairs"] for c in criteria)),
        "gold": {"labels": total_gold, "accuracy_pct": {**role_acc, "panel": _pct(panel_ok, total_gold)},
                 "accuracy_when_m1_eq_m2_pct": _pct(split["agreed_ok"], split["agreed_n"]), "agreed_n": split["agreed_n"],
                 "accuracy_when_split_pct": _pct(split["split_ok"], split["split_n"]), "split_n": split["split_n"]},
        "calls": calls,
        "by_language": {k: {"n": v["n"], "ab_agreement_pct": _pct(v["ab_agree"], v["ab_pairs"]),
                            "gold_n": v["gold_n"], "panel_accuracy_pct": _pct(v["gold_final_ok"], v["gold_n"])}
                        for k, v in sorted(lang.items())},
        "criteria": criteria,
        "disagreements": disagreements[:300],
        "gold_misses": gold_misses[:300],
    }
