"""End-to-end on the mock LLM: import -> evaluate -> appeal -> decision -> new checklist -> re-run -> diff."""
import copy
import time

from fastapi.testclient import TestClient

from cqa import pipeline, privacy
from cqa.main import app


def test_full_flow():
    with TestClient(app) as client:
        assert client.post("/api/datasets/SAMPLE/import").json()["imported"] == 5

        conv = client.get("/api/conversations/s-ru-001").json()
        assert "[IIN_1]" in conv["messages"][1]["text_masked"]  # masked before any LLM call
        call = client.get("/api/conversations/s-call-001").json()
        assert call["messages"][2]["t_sec"] == 15  # ISO 8601 -> seconds from the first message

        ev = client.post("/api/evaluations", json={"conversation_id": "s-ru-001"}).json()
        items = ev["result"]["criteria"]
        assert len(items) == 8 and all(i["final"] for i in items)
        assert ev["checklist_version"] == 1

        # operator appeals, reviewer changes
        target = next(i for i in items if i["type"] == "binary")
        new = "no" if target["final"]["verdict"] == "yes" else "yes"
        appeal = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": target["criterion_id"],
                                                   "comment": "не согласен"}, headers={"X-Role": "operator"}).json()
        assert appeal["state"] == "submitted"
        assert client.post(f"/api/appeals/{appeal['id']}/decision",
                           json={"action": "change", "verdict": new, "reason": ""}).status_code == 422
        decided = client.post(f"/api/appeals/{appeal['id']}/decision",
                              json={"action": "change", "verdict": new, "reason": "см. m3"},
                              headers={"X-Role": "reviewer"}).json()
        item = next(i for i in decided["result"]["criteria"] if i["criterion_id"] == target["criterion_id"])
        assert item["ai"] != item["final"] and item["decision"]["by"] == "reviewer"
        assert decided["appeals"][0]["state"] == "changed"

        # replace a criterion through the API (what the UI does), publish v2, re-run, diff
        cl = client.get("/api/checklists/active").json()
        cl2 = copy.deepcopy(cl)
        c07 = next(c for c in cl2["criteria"] if c["id"] == "c07")
        c07["definition"] = "Да — оператор предложил мобильное приложение для следующего обращения и попрощался."
        c07["atoms"] = [{"id": "offered_app", "q": "Предложил ли оператор мобильное приложение?"},
                        {"id": "farewell", "q": "Попрощался ли оператор?"}]
        pub = client.post("/api/checklists", json={"checklist": cl2, "note": "replace c07"}).json()
        assert pub["version"] == 2
        run = client.post("/api/runs", json={"checklist_version": 2}).json()
        for _ in range(100):
            run = client.get(f"/api/runs/{run['id']}").json()
            if run["status"] == "finished":
                break
            time.sleep(0.05)
        assert run["done"] == 5 and not run["failed"]
        latest = client.get("/api/conversations/s-ru-001").json()["latest"]
        cached = {i["criterion_id"]: i["cached"] for i in latest["result"]["criteria"]}
        assert cached["c07"] is False and cached["c01"] is True  # only the changed criterion re-ran
        diff = client.get("/api/checklists/diff", params={"from_v": 1, "to_v": 2}).json()
        assert any(r["conversation_id"] == "s-ru-001" and r["to"] is not None for r in diff)

        assert client.get("/api/review-queue").status_code == 200
        assert len(client.get("/api/audit").json()) > 0


def test_validator_rejects_invented_quotes():
    c = {"id": "c01", "type": "binary", "na_allowed": False, "rule": "all",
         "atoms": [{"id": "greeting", "q": "?"}]}
    by_mid = {"m1": "Здравствуйте! Меня зовут Айгерим."}
    good = {"verdict": "yes", "atoms": {"greeting": True}, "evidence": [{"msg": "m1", "quote": "«здравствуйте!»"}]}
    bad = {"verdict": "yes", "atoms": {"greeting": True}, "evidence": [{"msg": "m1", "quote": "Добрый день"}]}
    na = {"verdict": "na", "na_reason": "x"}
    assert pipeline.validate(c, good, by_mid) == []
    assert pipeline.validate(c, bad, by_mid)
    assert pipeline.validate(c, na, by_mid)


def test_privacy_masks_are_stable():
    mapping = {}
    text = privacy.mask("ИИН 900101300123, ещё раз 900101300123, карта 4111 1111 1111 1111, тел +7 701 234 56 78", mapping)
    assert text == "ИИН [IIN_1], ещё раз [IIN_1], карта [CARD_1], тел [PHONE_1]"


def test_traces_gold_and_analytics():
    with TestClient(app) as client:
        client.post("/api/datasets/SAMPLE/import")
        ev = client.post("/api/evaluations", json={"conversation_id": "s-kz-001", "checklist_version": 1}).json()
        item = next(i for i in ev["result"]["criteria"] if i["criterion_id"] == "c01")
        assert set(item["roles"]) == {"inspector", "advocate", "judge"}
        trace = client.get(f"/api/traces/{item['trace_ids']['judge'][0]}").json()
        assert trace["role"] == "judge" and "BRIEFS" in trace["request"][2]["content"] and trace["response"]
        assert len(client.get("/api/traces", params={"conversation_id": "s-kz-001"}).json()) >= 3

        # gold label = the panel's answer -> panel accuracy 100% on that label
        label = item["votes"]["judge"]
        assert client.post("/api/gold", json={"conversation_id": "s-kz-001", "criterion_id": "c01", "label": "maybe"}).status_code == 422
        assert client.post("/api/gold", json={"conversation_id": "s-kz-001", "criterion_id": "c01", "label": label}).json()["label"] == label
        a = client.get("/api/analytics", params={"checklist_version": 1}).json()
        c01 = next(c for c in a["criteria"] if c["criterion_id"] == "c01")
        assert c01["gold_n"] == 1 and c01["accuracy_pct"]["panel"] == 100.0
        assert a["gold"]["agreed_n"] + a["gold"]["split_n"] == 1
        assert a["dialogs"] >= 1 and a["calls"] and "KZ" in a["by_language"]
        assert c01["ab_agreement_pct"] is not None and sum(c01["judge_sided"].values()) == c01["judged"]
        # a gold label stops counting once the criterion wording changes (fingerprint)
        cl = client.get("/api/checklists/1").json()
        next(c for c in cl["criteria"] if c["id"] == "c01")["definition"] += " Без сленга."
        v = client.post("/api/checklists", json={"checklist": cl, "note": "reword c01"}).json()["version"]
        client.post("/api/evaluations", json={"conversation_id": "s-kz-001", "checklist_version": v})
        a2 = client.get("/api/analytics", params={"checklist_version": v}).json()
        assert next(c for c in a2["criteria"] if c["criterion_id"] == "c01")["gold_n"] == 0
        assert client.post("/api/gold", json={"conversation_id": "s-kz-001", "criterion_id": "c01", "label": ""}).json()["label"] is None


def test_fresh_install_seeds_organizers_v1_and_clarified_active_v2(fresh_database_url, monkeypatch):
    from cqa import checklist, config, db
    monkeypatch.setattr(config, "DATABASE_URL", fresh_database_url())
    monkeypatch.setattr(config, "SEED_CLARIFIED", True)
    db.init()
    checklist.ensure_seed()
    assert [v["version"] for v in checklist.versions()] == [2, 1]
    active = checklist.get()
    assert active["version"] == 2 and {c["id"] for c in active["criteria"]} == {c["id"] for c in checklist.get(1)["criteria"]}
    assert checklist.lint(active)[0] == []
