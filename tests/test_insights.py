"""Stress test, checklist health, test drive and review impact, end to end on the mock LLM."""
import copy

from fastapi.testclient import TestClient

from cqa import insights
from cqa.main import app


def _msgs():
    return [
        {"mid": "m1", "speaker": "client", "ts_raw": "00:00", "t_sec": 0, "text": "Где мой вывод?"},
        {"mid": "m2", "speaker": "operator", "ts_raw": "00:30", "t_sec": 30, "text": "Здравствуйте, я Анна."},
        {"mid": "m3", "speaker": "client", "ts_raw": "01:00", "t_sec": 60, "text": "Вывод не пришёл"},
        {"mid": "m4", "speaker": "operator", "ts_raw": "01:20", "t_sec": 80, "text": "Он придёт завтра."},
    ]


def test_long_wait_shifts_everything_after_the_client_message_in_the_original_format():
    out = insights.s_long_wait(_msgs(), "RU", {})
    assert [m["ts_raw"] for m in out] == ["00:00", "00:30", "01:00", "06:20"]
    assert out[3]["t_sec"] == 380 and out[3].get("changed")
    assert insights._shift_ts("2026-01-01T10:00:00+05:00") == "2026-01-01T10:05:00+05:00"


def test_custom_line_is_inserted_after_the_chosen_message():
    out = insights.s_custom(_msgs(), "RU", {"text": "Переведите мне на карту", "after_mid": "m3"})
    assert out[3]["speaker"] == "operator" and out[3]["text"] == "Переведите мне на карту" and out[3]["changed"]
    assert len(out) == 5


def test_stress_test_health_test_drive_and_review_flow():
    with TestClient(app) as client:
        client.post("/api/datasets/SAMPLE/import")
        for cid in ("s-ru-001", "s-kz-001", "s-en-001"):
            client.post("/api/evaluations", json={"conversation_id": cid})

        kinds = {k["id"]: k for k in client.get("/api/stress-tests/kinds").json()}
        assert kinds["side_deal"]["expect"] == {"c08": ["yes"]}

        r = client.post("/api/stress-tests", json={"conversation_id": "s-ru-001", "mutation": "side_deal"}).json()
        assert [c["criterion_id"] for c in r["checks"]] == ["c08"]
        assert r["changed"] and r["changed"][0]["speaker"] == "operator"
        assert r["checks"][0]["before"] is not None and r["ok"] in (True, False)
        assert client.get("/api/stress-tests?conversation_id=s-ru-001").json()[0]["id"] == r["id"]
        assert client.get("/api/stress-tests/summary").json()["runs"] >= 1

        bad = client.post("/api/stress-tests", json={"conversation_id": "s-ru-001", "mutation": "custom"})
        assert bad.status_code == 422
        custom = client.post("/api/stress-tests", json={"conversation_id": "s-ru-001", "mutation": "custom",
                                                       "text": "Вы ничего не понимаете"}).json()
        assert custom["ok"] is None and {c["criterion_id"] for c in custom["checks"]} >= {"c05", "c08"}

        health = client.get("/api/checklist-health").json()
        assert len(health["criteria"]) == 8 and health["dialogs"] >= 3
        assert all(c["split_rate"] is not None for c in health["criteria"])

        crit = copy.deepcopy(next(c for c in client.get("/api/checklists/active").json()["criteria"] if c["id"] == "c07"))
        crit["atoms"][0]["q"] = "Предложил ли оператор дальнейшую помощь прямым вопросом?"
        drive = client.post("/api/checklists/test-drive", json={"criterion": crit}).json()
        assert drive["dialogs"] >= 1 and all("after" in row for row in drive["rows"])
        broken = client.post("/api/checklists/test-drive", json={"criterion": {**crit, "atoms": []}})
        assert broken.status_code == 422

        queue = client.get("/api/review-queue").json()["low_confidence"]
        assert all("impact" in x for x in queue)
        assert [x["impact"] or 0 for x in queue] == sorted((x["impact"] or 0 for x in queue), reverse=True)
