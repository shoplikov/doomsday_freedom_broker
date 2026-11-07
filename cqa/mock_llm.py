"""LLM_PROVIDER=mock: deterministic, schema-valid fake answers so the whole app runs without a key.
Not smart. Its only job is to exercise every code path: agreement, disagreement, review queue."""
import hashlib


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def respond(role: str, criteria: list[dict], msgs: list[dict], briefs: dict | None = None) -> dict:
    conv = msgs[0]["conversation_id"] if msgs else ""
    texts = [m for m in msgs if m["text_masked"].strip()]
    ops = [m for m in texts if m["speaker"] == "operator"] or texts
    out = {}
    for c in criteria:
        h = _h(role, c["id"], conv)
        if role == "judge" and briefs and briefs.get(c["id"]):
            b = briefs[c["id"]]
            pick = "inspector" if h % 2 else "advocate"
            chosen = b.get(pick) or b.get("inspector") or b.get("advocate")
            if chosen:
                out[c["id"]] = {**chosen, "rationale": f"[mock judge] согласен с {pick}"}
                continue
        m = ops[h % len(ops)] if ops else None
        evidence = [{"msg": m["mid"], "quote": m["text_masked"][:40]}] if m else []
        checked = [msgs[0]["mid"], msgs[-1]["mid"]] if msgs else None
        lenient = role == "advocate" or h % 4 != 0
        item = {"id": c["id"], "evidence": evidence, "checked_range": checked, "na_reason": None,
                "rationale": f"[mock {role}] детерминированный ответ без LLM"}
        if c["type"] == "scale":
            steps = sorted(s["step"] for s in c["scale"])
            item.update(verdict="scored", step=steps[-1] if lenient else steps[len(steps) // 2])
        else:
            value = lenient if c["type"] == "binary" else False  # flag: no violation
            item.update(verdict="yes" if value else "no", step=None,
                        atoms={a["id"]: value for a in c["atoms"]})
        out[c["id"]] = item
    return out
