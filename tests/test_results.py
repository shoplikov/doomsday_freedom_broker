"""The organisers' results.json (format 1.0): shape, value types, error records."""
from fastapi.testclient import TestClient

from cqa import results
from cqa.main import app

CODES = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
ALLOWED = {"contact": {True, False}, "discovery": {True, False, None}, "identification": {True, False, None},
           "consultation": {0, 5, 10, 15, 20, None}, "empathy": {True, False}, "dialogue": {0, 4, 7, 10},
           "closure": {True, False, None}, "critical": {True, False}}


def test_results_file():
    with TestClient(app) as client:
        client.post("/api/datasets/SAMPLE/import")
        client.post("/api/evaluations", json={"conversation_id": "s-ru-001"})
        doc = client.get("/api/results/SAMPLE").json()

    assert doc["schema_version"] == "1.0" and doc["team_name"] and set(doc["model"]) == {"mode", "name"}
    assert doc["model"]["mode"] in ("local", "external_api", "hybrid")
    ids = [r["conversation_id"] for r in doc["results"]]
    assert len(ids) == len(set(ids)) == 5  # every dialog of the group, once, errors included

    ok = next(r for r in doc["results"] if r["conversation_id"] == "s-ru-001")
    assert ok["status"] == "ok" and [c["criterion_id"] for c in ok["criteria"]] == CODES
    for c in ok["criteria"]:
        assert c["value"] in ALLOWED[c["criterion_id"]] and type(c["value"]) in (bool, int, type(None))
        assert isinstance(c["rationale"], str) and all(isinstance(t, int) and t >= 1 for t in c["evidence_turns"])
    critical = next(c["value"] for c in ok["criteria"] if c["criterion_id"] == "critical")
    assert ok["score"]["critical_flag"] is critical and 0 <= ok["score"]["final_score"] <= 100

    for r in doc["results"]:  # other dialogs may be unevaluated (shared test DB): then a proper error record
        if r["status"] != "ok":
            assert set(r) == {"conversation_id", "status", "criteria", "score", "error"}
            assert r["status"] == "error" and r["criteria"] == [] and r["score"] is None and r["error"]
    assert results._record("x", None)["status"] == "error"
