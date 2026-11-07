"""Red team on real dialogs: break a dialog on purpose so the correct answer is KNOWN, then check the panel.

DEV has no reference answers, so we create them: remove the greeting -> c01 must be "no", offer a side deal ->
c08 must be "yes", etc. A harmless edit must change nothing. Only the affected criteria are evaluated (cheap),
and we record which role caught the defect (M1 inspector, M2 advocate, M3 judge).
"""
import asyncio
import json
import logging

from . import checklist, config, db, importer, pipeline, privacy, signals

log = logging.getLogger(__name__)

PHRASES = {
    "neutral_reply": {"RU": "Сейчас проверю ваш вопрос.", "KZ": "Қазір сұрағыңызды тексеремін.", "EN": "Let me check your question."},
    "ok": {"RU": "Хорошо.", "KZ": "Жақсы.", "EN": "Okay."},
    "side_deal": {
        "RU": "Могу помочь в обход компании: переведите деньги на мою личную карту, я сам их вложу под хороший процент.",
        "KZ": "Компанияны айналып өтіп көмектесе аламын: ақшаны менің жеке картама аударыңыз, өзім жақсы пайызбен салып беремін.",
        "EN": "I can help outside the company: transfer the money to my personal card and I will invest it for you myself."},
    "insult": {
        "RU": "Вы что, совсем ничего не понимаете? Читать не умеете?",
        "KZ": "Сіз мүлдем ештеңе түсінбейсіз бе? Оқи алмайсыз ба?",
        "EN": "Are you completely clueless? Can you not even read?"},
    "leak": {
        "RU": "Вижу ваш счёт: баланс 1 250 000 тенге, последний вывод 3 мая на карту, привязанную к вашему номеру.",
        "KZ": "Шотыңызды көріп тұрмын: балансы 1 250 000 теңге, соңғы шығару 3 мамырда нөміріңізге байланған картаға.",
        "EN": "I can see your account: balance 1,250,000 tenge, last withdrawal on 3 May to the card linked to your number."},
}
VERIFIED_MARKERS = ("подтверждена", "расталды", "identity verified")


def _ops(msgs):
    return [i for i, m in enumerate(msgs) if m["speaker"] == "operator"]


def _with_text(m, text):
    return {**m, "text": text}


def _insert_after(msgs, i, text):
    m = msgs[i]
    return msgs[:i + 1] + [{**m, "speaker": "operator", "text": text}] + msgs[i + 1:]


def m_drop_greeting(msgs, lang):
    i = _ops(msgs)[0]
    out = list(msgs)
    out[i] = _with_text(msgs[i], PHRASES["neutral_reply"][lang])
    return out


def m_side_deal(msgs, lang):
    return _insert_after(msgs, _ops(msgs)[0], PHRASES["side_deal"][lang])


def m_insult(msgs, lang):
    return _insert_after(msgs, _ops(msgs)[0], PHRASES["insult"][lang])


def m_drop_farewell(msgs, lang):
    i = _ops(msgs)[-1]
    out = list(msgs)
    out[i] = _with_text(msgs[i], PHRASES["ok"][lang])
    return out


def m_leak_before_verification(msgs, lang):
    if any(m["speaker"] == "system" and any(k in m["text"].lower() for k in VERIFIED_MARKERS) for m in msgs):
        return None  # needs a dialog with no identity verification at all
    return _insert_after(msgs, _ops(msgs)[0], PHRASES["leak"][lang])


def m_harmless(msgs, lang):
    """Client typing sloppier (lowercase, no final punctuation). Must not change any verdict."""
    i = next((j for j, m in enumerate(msgs) if m["speaker"] == "client" and len(m["text"]) > 3), None)
    if i is None:
        return None
    out = list(msgs)
    out[i] = _with_text(msgs[i], msgs[i]["text"].lower().rstrip(".!?"))
    return out


# name -> (mutation, {criterion: allowed labels}); None = compare every criterion with the unmutated baseline
MUTATIONS = {
    "drop_greeting": (m_drop_greeting, {"c01": {"no"}}),
    "side_deal": (m_side_deal, {"c08": {"yes"}}),
    "insult": (m_insult, {"c08": {"yes"}, "c05": {"no"}}),
    "drop_farewell": (m_drop_farewell, {"c07": {"no", "na"}}),
    "leak_before_verification": (m_leak_before_verification, {"c03": {"no"}}),
    "harmless": (m_harmless, None),
}


def _renumber(conv_id, msgs):
    mapping: dict = {}
    return [{**m, "conversation_id": conv_id, "idx": n, "mid": f"m{n + 1}",
             "text_masked": privacy.mask(m["text"], mapping), "lang": signals.detect_lang(m["text"])}
            for n, m in enumerate(msgs)]


def pick_dialogs(group: str, per_lang: int) -> list[dict]:
    rows = db.rows("SELECT id, language FROM conversations WHERE group_id = %s AND channel = 'agent chat' ORDER BY id", group)
    chosen, count = [], {}
    for r in rows:
        msgs = importer.messages(r["id"])
        if len(_ops(msgs)) >= 2 and count.get(r["language"], 0) < per_lang:
            count[r["language"]] = count.get(r["language"], 0) + 1
            chosen.append(r)
    return chosen


def baseline(conv_id: str) -> dict | None:
    ev = db.row("SELECT result_json FROM evaluations WHERE conversation_id = %s AND checklist_version = 1 ORDER BY id DESC LIMIT 1", conv_id)
    if not ev:
        return None
    return {i["criterion_id"]: pipeline.vote_label(i["ai"]) for i in json.loads(ev["result_json"])["criteria"]}


def plan(group: str, per_lang: int, only: list[str] | None = None) -> list[dict]:
    cases = []
    for d in pick_dialogs(group, per_lang):
        for name, (fn, expect) in MUTATIONS.items():
            if only and name not in only:
                continue
            if name == "harmless" and not baseline(d["id"]):
                continue  # needs an existing evaluation to compare with
            cases.append({"conversation_id": d["id"], "language": d["language"], "mutation": name})
    return cases


async def run_case(case: dict, cl: dict) -> dict:
    fn, expect = MUTATIONS[case["mutation"]]
    lang = case["language"] if case["language"] in ("RU", "KZ", "EN") else "RU"
    mutated = fn(importer.messages(case["conversation_id"]), lang)
    if mutated is None:
        return case | {"skipped": "mutation not applicable to this dialog"}
    rt_id = f"{case['conversation_id']}~{case['mutation']}"
    msgs = _renumber(rt_id, mutated)
    base = baseline(case["conversation_id"]) if expect is None else None
    targets = {c: {base[c]} for c in base} if expect is None else expect
    criteria = [c for c in cl["criteria"] if c["id"] in targets]
    out, usage = await pipeline.evaluate_criteria(criteria, msgs, signals.compute(msgs), rt_id)
    checks = []
    for cid, allowed in targets.items():
        r = out[cid]
        final = pipeline.vote_label(r["ai"])
        checks.append({"criterion": cid, "expected": sorted(allowed), "final": final, "votes": r["votes"],
                       "ok": final in allowed, "status": r["status"], "rationale": r["rationale"]})
    return case | {"checks": checks, "ok": all(c["ok"] for c in checks), "usage": usage}


async def run(cases: list[dict]) -> list[dict]:
    cl = checklist.get(1)
    return await asyncio.gather(*(run_case(c, cl) for c in cases))


def summarize(results: list[dict]) -> dict:
    done = [r for r in results if "checks" in r]
    by_mut, roles = {}, {"inspector": [0, 0], "advocate": [0, 0], "judge": [0, 0]}
    for r in done:
        s = by_mut.setdefault(r["mutation"], {"cases": 0, "passed": 0})
        s["cases"] += 1
        s["passed"] += r["ok"]
        if r["mutation"] == "harmless":
            continue
        for c in r["checks"]:
            for role, label in c["votes"].items():
                roles[role][0] += label in c["expected"]
                roles[role][1] += 1
    from .llm import cost_usd
    return {
        "cases": len(done), "passed": sum(r["ok"] for r in done), "skipped": len(results) - len(done),
        "by_mutation": by_mut,
        "role_catch_rate_pct": {k: round(100 * a / b, 1) if b else None for k, (a, b) in roles.items()},
        "cost_usd": cost_usd([u for r in done for u in r["usage"]]),
        "provider": config.LLM_PROVIDER, "models": config.MODELS,
    }
