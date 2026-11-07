"""Scoring engine. Plain code, no LLM. Rules from the organizers' checklist, page 2:

1. N/A only where allowed.          2. Yes = full weight, No = 0; scale = one of the steps.
3. Scale points = weight * step / top step.
4. N/A criteria drop out; their weight is redistributed proportionally (max stays 100).
5. Round the total once, half-up, to 2 decimals. No applicable weight -> no total.
   Gross errors (flag criteria) are reported separately and never change points.
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def _q(x: Decimal) -> float:
    # Trap: Python round(88.125, 2) == 88.12 (banker's rounding). Decimal half-up gives 88.13.
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


def fraction(criterion: dict, final: dict) -> Decimal:
    if criterion["type"] == "scale":
        top = max(Decimal(str(s["step"])) for s in criterion["scale"])
        return Decimal(str(final["step"])) / top
    return Decimal(1) if final["verdict"] == "yes" else Decimal(0)


def score(checklist: dict, finals: dict[str, dict]) -> dict:
    """finals: {criterion_id: {"verdict": "yes"|"no"|"na"|"scored", "step": number|None}}"""
    raw: dict[str, Decimal] = {}
    weights: dict[str, Decimal] = {}
    missing, gross = [], False
    for c in checklist["criteria"]:
        f = finals.get(c["id"])
        if c["type"] == "flag":
            gross = gross or bool(f and f["verdict"] == "yes")
            continue
        if f is None:
            missing.append(c["id"])
            continue
        if f["verdict"] == "na":
            continue
        w = Decimal(str(c["weight"]))
        weights[c["id"]] = w
        raw[c["id"]] = w * fraction(c, f)

    pool = sum(weights.values(), Decimal(0))
    ok = pool > 0 and not missing

    def scaled(x: Decimal) -> float | None:
        return _q(x * 100 / pool) if pool > 0 else None

    per_criterion = {
        c["id"]: {"points": scaled(raw[c["id"]]), "max_points": scaled(weights[c["id"]])} if c["id"] in raw
        else {"points": None, "max_points": None}
        for c in checklist["criteria"]
    }
    sections = []
    for s in checklist.get("sections") or []:
        ids = [c["id"] for c in checklist["criteria"] if c.get("section") == s["id"] and c["id"] in raw]
        sections.append({
            "id": s["id"], "title": s["title"],
            "points": scaled(sum((raw[i] for i in ids), Decimal(0))) if ids else None,
            "max_points": scaled(sum((weights[i] for i in ids), Decimal(0))) if ids else None,
        })
    return {
        "total": _q(sum(raw.values(), Decimal(0)) / pool * 100) if ok else None,
        "total_note": None if ok else ("missing results: " + ", ".join(missing) if missing else "no applicable criteria"),
        "applicable_weight": float(pool),
        "gross_error": gross,
        "criteria": per_criterion,
        "sections": sections,
    }
