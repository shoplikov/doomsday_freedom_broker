"""The three-model panel on the REAL OpenAI code path (LLM_PROVIDER=openai) with a scripted fake model.
Every scenario a real model can produce: agreement, split, invented quotes, broken JSON, outages, sloppy formats."""
import asyncio
import json

import pytest

from cqa import checklist, config, db, llm, pipeline, traces

CL = json.loads(checklist.SEED.read_text(encoding="utf-8"))
C = {c["id"]: c for c in CL["criteria"]}
CONV = "t-panel"
MSGS = [
    {"conversation_id": CONV, "mid": "m1", "speaker": "client", "ts_raw": "00:01", "t_sec": 0, "text_masked": "Здравствуйте, где мой вывод?"},
    {"conversation_id": CONV, "mid": "m2", "speaker": "operator", "ts_raw": "00:20", "t_sec": 19, "text_masked": "Здравствуйте! Меня зовут Айгерим."},
    {"conversation_id": CONV, "mid": "m3", "speaker": "operator", "ts_raw": "00:50", "t_sec": 49, "text_masked": "Вывод поступит до 5 мая."},
    {"conversation_id": CONV, "mid": "m4", "speaker": "system", "ts_raw": "01:00", "t_sec": 59, "text_masked": "Чат закрыт"},
]
GOOD_EV = [{"msg": "m2", "quote": "Здравствуйте! Меня зовут Айгерим."}]


def c01(value: bool, evidence=GOOD_EV, **kw):
    return {"id": "c01", "atoms": {"greeting": value, "client_language": value, "introduced": value},
            "verdict": "yes" if value else "no", "step": None, "evidence": evidence,
            "checked_range": ["m2", "m3"], "na_reason": None, "rationale": "r"} | kw


def c06(step, **kw):
    return {"id": "c06", "verdict": "scored", "step": step, "evidence": [{"msg": "m3", "quote": "до 5 мая"}],
            "checked_range": None, "na_reason": None, "rationale": "r"} | kw


class FakeLLM:
    """script[role] = list of replies per attempt; a reply is a list of items, a raw string, or an Exception."""

    def __init__(self, script):
        self.script, self.calls = script, []

    async def __call__(self, model, messages, temperature=None):
        self.temps = getattr(self, "temps", []) + [temperature]
        task = messages[2]["content"]
        role = next(r for r in ("inspector", "advocate", "judge") if f"ROLE: {r.upper()}" in task)
        attempt = sum(1 for c in self.calls if c[0] == role)
        self.calls.append((role, messages))
        replies = self.script[role]
        reply = replies[min(attempt, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps({"criteria": reply}, ensure_ascii=False)
        return llm.parse_json(text), {"model": model, "in": 1000, "out": 200}, text

    def roles(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def fake(monkeypatch):
    db.init()
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "JUDGE_MODE", "always")
    monkeypatch.setattr(config, "TRUST_THRESHOLD", 0.67)
    monkeypatch.setattr(pipeline, "_sem", None)

    def install(script):
        f = FakeLLM(script)
        monkeypatch.setattr(llm, "chat_json", f)
        return f
    return install


def run(criteria):
    return asyncio.run(pipeline.evaluate_criteria([C[i] for i in criteria], MSGS, {}, CONV))


def test_unanimous_is_auto_accepted(fake):
    f = fake({"inspector": [[c01(True)]], "advocate": [[c01(True)]], "judge": [[c01(True)]]})
    out, usage = run(["c01"])
    r = out["c01"]
    assert r["ai"] == {"verdict": "yes", "step": None} and r["confidence"] == 1.0 and r["status"] == "auto_accepted"
    assert r["votes"] == {"inspector": "yes", "advocate": "yes", "judge": "yes"} and r["source"] == "judge"
    assert sorted(f.roles()) == ["advocate", "inspector", "judge"] and len(usage) == 3


def test_inspector_and_advocate_are_independent_and_judge_sees_both(fake):
    f = fake({"inspector": [[c01(False, evidence=[], rationale="нет имени")]], "advocate": [[c01(True)]],
              "judge": [[c01(True)]]})
    out, _ = run(["c01"])
    by_role = {role: msgs for role, msgs in f.calls}
    assert "BRIEFS" not in by_role["inspector"][2]["content"] and "BRIEFS" not in by_role["advocate"][2]["content"]
    assert "нет имени" in by_role["judge"][2]["content"]  # judge reads both briefs
    assert f.roles()[-1] == "judge"
    r = out["c01"]
    assert r["votes"] == {"inspector": "no", "advocate": "yes", "judge": "yes"}
    assert r["confidence"] == 0.67 and r["status"] == "auto_accepted"


def test_judge_disagreeing_with_both_goes_to_review(fake):
    fake({"inspector": [[c06(10)]], "advocate": [[c06(10)]], "judge": [[c06(4)]]})
    r = run(["c06"])[0]["c06"]
    assert r["ai"]["step"] == 4 and r["confidence"] == 0.33 and r["status"] == "needs_review"


def test_invented_quote_is_retried_with_feedback(fake):
    bad = c01(True, evidence=[{"msg": "m2", "quote": "Добрый вечер, я Иван"}])
    f = fake({"inspector": [[c01(True)]], "advocate": [[c01(True)]], "judge": [[bad], [c01(True)]]})
    r = run(["c01"])[0]["c01"]
    assert r["status"] == "auto_accepted" and r["confidence"] == 1.0
    retry_prompt = [m for role, m in f.calls if role == "judge"][1]
    assert "quote not found" in retry_prompt[-1]["content"]
    first, second = (traces.get(t) for t in r["trace_ids"]["judge"])
    assert first["attempt"] == 1 and "c01" in first["validation"] and second["validation"] == {}


def test_judge_broken_twice_falls_back_to_strict_reading_and_review(fake):
    fake({"inspector": [[c01(False, evidence=[])]], "advocate": [[c01(True)]], "judge": ["not json at all"]})
    r = run(["c01"])[0]["c01"]
    assert r["ai"]["verdict"] == "no" and r["source"] == "inspector" and r["status"] == "needs_review"
    assert r["errors"]["judge"] == ["missing from the answer"]


def test_provider_outage_for_one_role(fake):
    fake({"inspector": [[c01(True)]], "advocate": [RuntimeError("503")], "judge": [[c01(True)]]})
    r = run(["c01"])[0]["c01"]
    assert r["votes"] == {"inspector": "yes", "judge": "yes"} and r["confidence"] == 0.67
    tr = [traces.get(t) for t in r["trace_ids"]["advocate"]]
    assert all("503" in t["error"] for t in tr) and len(tr) == 2


def test_everything_down_marks_failed_not_crash(fake):
    fake({"inspector": [RuntimeError("down")], "advocate": [RuntimeError("down")], "judge": [RuntimeError("down")]})
    r = run(["c01"])[0]["c01"]
    assert r["ai"] is None and r["status"] == "failed"


def test_sloppy_but_meaningful_format_is_accepted(fake):
    sloppy = {"id": "1", "atoms": {"greeting": "true", "client_language": "Yes", "introduced": "да"},
              "verdict": "YES", "evidence": {"message": "M2", "quote": "«Меня зовут Айгерим»"},
              "checked_range": "m2-m3", "rationale": "ok"}
    fake({"inspector": [f"```json\n{json.dumps({'criteria': [sloppy]}, ensure_ascii=False)}\n```"],
          "advocate": [[sloppy]], "judge": [[sloppy]]})
    r = run(["c01"])[0]["c01"]
    assert r["status"] == "auto_accepted" and r["ai"]["verdict"] == "yes" and r["checked_range"] == ["m2", "m3"]


def test_rule_violations_are_rejected(fake):
    na_forbidden = {"id": "c06", "verdict": "N/A", "na_reason": "x", "evidence": [], "rationale": "r"}
    wrong_step = c06(12)
    fake({"inspector": [[na_forbidden]], "advocate": [[wrong_step]], "judge": [[c06(7)]]})
    r = run(["c06"])[0]["c06"]
    assert "N/A is not allowed for this criterion" in r["errors"]["inspector"]
    assert any("step must be one of" in e for e in r["errors"]["advocate"])
    # Judge is valid but it is the only vote: one voice is not enough, a human must confirm.
    assert r["ai"]["step"] == 7 and r["confidence"] == 0.33 and r["status"] == "needs_review"


def test_atoms_beat_the_models_verdict(fake):
    lying = c01(True) | {"atoms": {"greeting": True, "client_language": True, "introduced": False}}
    fake({"inspector": [[lying]], "advocate": [[lying]], "judge": [[lying]]})
    assert run(["c01"])[0]["c01"]["ai"]["verdict"] == "no"  # rule "all" in code wins over "verdict": "yes"


def test_flag_any_atom_is_gross_error(fake):
    flag = {"id": "c08", "atoms": {"insult": False, "side_deal": True, "confidential_leak": False}, "verdict": "no",
            "evidence": [{"msg": "m3", "quote": "Вывод поступит"}], "rationale": "r"}
    fake({"inspector": [[flag]], "advocate": [[flag]], "judge": [[flag]]})
    assert run(["c08"])[0]["c08"]["ai"]["verdict"] == "yes"


def test_disputed_mode_skips_judge_on_agreement(fake, monkeypatch):
    monkeypatch.setattr(config, "JUDGE_MODE", "disputed")
    f = fake({"inspector": [[c01(True), c06(10)]], "advocate": [[c01(True), c06(7)]], "judge": [[c06(7)]]})
    out, _ = run(["c01", "c06"])
    assert out["c01"]["source"] == "agreement" and out["c01"]["confidence"] == 1.0
    judge_prompt = [m for role, m in f.calls if role == "judge"][0][2]["content"]
    assert '"id": "c06"' in judge_prompt and '"id": "c01"' not in judge_prompt
    assert out["c06"]["confidence"] == 0.67


def test_prompt_keeps_transcript_as_shared_prefix_and_quotes_original_language(fake):
    f = fake({"inspector": [[c01(True)]], "advocate": [[c01(True)]], "judge": [[c01(True)]]})
    run(["c01"])
    prefixes = {json.dumps(m[:2], ensure_ascii=False) for _, m in f.calls}
    assert len(prefixes) == 1  # system + facts + transcript identical for all roles -> provider prompt cache
    assert "[m2 00:20 operator] Здравствуйте! Меня зовут Айгерим." in f.calls[0][1][1]["content"]
    assert "Never translate" in f.calls[0][1][2]["content"]


def test_full_evaluation_cost_tokens_and_cache_on_openai_path(fake, monkeypatch):
    """evaluate() end to end through the OpenAI code path: cost from token usage, traces, cache hit on re-run."""
    import re

    from cqa import analytics, evaluations, importer, mock_llm

    checklist.ensure_seed()
    importer.save("COST", [{"conversation_id": "cost-1", "channel": "agent chat", "language": "RU", "audio_url": None,
                            "transcript": [{"timestamp": "00:01", "speaker": "client", "text": "Здравствуйте"},
                                           {"timestamp": "00:10", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алия."}]}])
    monkeypatch.setattr(config, "MODELS", {"inspector": "small", "advocate": "small", "judge": "big"})
    monkeypatch.setattr(config, "LLM_PRICES", {"small": [1.0, 2.0], "big": [10.0, 20.0]})
    calls = []

    async def realistic(model, messages, temperature=None):  # answers like a model would, from what is in the prompt
        calls.append(model)
        line = re.compile(r"^\[(m\d+) (\S+) (\w+)\] (.*)$")
        msgs = [{"conversation_id": "cost-1", "mid": m[1], "speaker": m[3], "text_masked": m[4]}
                for m in map(line.match, messages[1]["content"].splitlines()) if m]
        ids = re.findall(r'"id": "(c\d+)"', messages[2]["content"].split("OUTPUT")[0])
        role = next(r for r in ("inspector", "advocate", "judge") if f"ROLE: {r.upper()}" in messages[2]["content"])
        crit = [C[i] for i in dict.fromkeys(ids)]
        data = {"criteria": list(mock_llm.respond(role, crit, msgs).values())}
        return data, {"model": model, "in": 1_000_000, "out": 100_000}, json.dumps(data, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat_json", realistic)
    ev = evaluations.load(asyncio.run(evaluations.evaluate("cost-1")))
    assert calls == ["small", "small", "big"]  # one call per role, all 8 criteria at once
    assert ev["cost_usd"] == (1.0 + 0.2) * 2 + (10.0 + 2.0)  # 2 x small + 1 x big
    assert ev["tokens_in"] == 3_000_000 and all(i["status"] != "failed" for i in ev["result"]["criteria"])
    ev2 = evaluations.load(asyncio.run(evaluations.evaluate("cost-1")))
    assert len(calls) == 3 and ev2["cost_usd"] == 0 and all(i["cached"] for i in ev2["result"]["criteria"])
    roles = {r["role"]: r for r in analytics.compute()["calls"]}
    assert roles["judge"]["tokens_in"] >= 1_000_000


def test_near_verbatim_quote_is_snapped_to_the_real_text_without_retry(fake):
    # a dropped letter / suffix (common in Kazakh) is repaired from the cited message, not re-asked
    slip = [{"msg": "m2", "quote": "Здравствуйте! Меня зовут Айгерм."}]
    f = fake({"inspector": [[c01(True, evidence=slip)]], "advocate": [[c01(True)]], "judge": [[c01(True)]]})
    res = run(["c01"])[0]["c01"]
    assert f.roles().count("inspector") == 1
    assert res["roles"]["inspector"]["evidence"][0]["quote"] == "Здравствуйте! Меня зовут Айгерим."


def test_invented_quote_is_not_snapped():
    r = pipeline.snap_quotes({"evidence": [{"msg": "m2", "quote": "Добрый день, чем помочь?"}]},
                             {"m2": "Здравствуйте! Меня зовут Айгерим."})
    assert r["evidence"][0]["quote"] == "Добрый день, чем помочь?"


def test_empty_greedy_answer_is_resampled_at_once(fake):
    # temperature 0 sometimes yields just "{}": sample again in the same attempt instead of failing the dialog
    f = fake({"inspector": ["{}", [c01(True)]], "advocate": [[c01(True)]], "judge": [[c01(True)]]})
    res = run(["c01"])[0]["c01"]
    assert res["status"] == "auto_accepted" and f.roles().count("inspector") == 2
    assert pipeline.RESAMPLE_TEMPERATURE in f.temps


def test_flag_named_by_the_verdict_is_raised_even_without_an_atom():
    c08 = C["c08"]
    r = {"atoms": {a["id"]: False for a in c08["atoms"]}, "verdict": "yes"}
    assert pipeline.derive(c08, r)["verdict"] == "yes"
    assert pipeline.derive(c08, r | {"verdict": "no"})["verdict"] == "no"
