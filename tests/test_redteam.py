"""Red-team mutations produce the intended defect and the runner reports per-role votes (mock LLM, free)."""
import asyncio

from cqa import checklist, db, importer, redteam

MSGS = [
    {"conversation_id": "x", "idx": 0, "mid": "m1", "speaker": "client", "ts_raw": "00:01", "t_sec": 0, "text": "Здравствуйте. Где мой вывод?"},
    {"conversation_id": "x", "idx": 1, "mid": "m2", "speaker": "operator", "ts_raw": "00:20", "t_sec": 19, "text": "Здравствуйте! Меня зовут Алия."},
    {"conversation_id": "x", "idx": 2, "mid": "m3", "speaker": "operator", "ts_raw": "00:50", "t_sec": 49, "text": "Всего доброго, до свидания!"},
]


def test_mutations_change_what_they_claim():
    assert redteam.m_drop_greeting(MSGS, "RU")[1]["text"] == redteam.PHRASES["neutral_reply"]["RU"]
    deal = redteam.m_side_deal(MSGS, "KZ")
    assert len(deal) == 4 and deal[2]["speaker"] == "operator" and "жеке картама" in deal[2]["text"]
    assert redteam.m_drop_farewell(MSGS, "EN")[2]["text"] == "Okay."
    assert redteam.m_harmless(MSGS, "RU")[0]["text"] == "здравствуйте. где мой вывод"
    verified = MSGS + [{**MSGS[0], "speaker": "system", "text": "Личность клиента подтверждена."}]
    assert redteam.m_leak_before_verification(verified, "RU") is None  # not applicable when identity was verified
    renum = redteam._renumber("x~t", deal)
    assert [m["mid"] for m in renum] == ["m1", "m2", "m3", "m4"] and all(m["conversation_id"] == "x~t" for m in renum)


def test_runner_on_mock_reports_votes_and_summary():
    db.init()
    checklist.ensure_seed()
    importer.save("RT", [{"conversation_id": "rt-1", "channel": "agent chat", "language": "RU", "audio_url": None,
                          "transcript": [{"timestamp": m["ts_raw"], "speaker": m["speaker"], "text": m["text"]} for m in MSGS]}])
    cases = redteam.plan("RT", 1, ["drop_greeting", "insult"])
    assert [c["mutation"] for c in cases] == ["drop_greeting", "insult"]
    results = asyncio.run(redteam.run(cases))
    insult = next(r for r in results if r["mutation"] == "insult")
    assert {c["criterion"] for c in insult["checks"]} == {"c08", "c05"}
    assert all(set(c["votes"]) == {"inspector", "advocate", "judge"} for c in insult["checks"])
    s = redteam.summarize(results)
    assert s["cases"] == 2 and set(s["role_catch_rate_pct"]) == {"inspector", "advocate", "judge"}
