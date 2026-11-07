import json

from cqa import scoring
from cqa.config import DATA_DIR

CL = json.loads((DATA_DIR / "checklist_v1.json").read_text(encoding="utf-8"))


def f(verdict, step=None):
    return {"verdict": verdict, "step": step}


def test_na_redistribution_and_appeal_numbers():
    # Playbook page 3: 02 N/A, 05 No, 07 No -> 65 / 85 * 100 = 76.47
    finals = {"c01": f("yes"), "c02": f("na"), "c03": f("yes"), "c04": f("scored", 20),
              "c05": f("no"), "c06": f("scored", 10), "c07": f("no"), "c08": f("no")}
    s = scoring.score(CL, finals)
    assert s["total"] == 76.47
    assert s["applicable_weight"] == 85
    assert s["criteria"]["c03"]["points"] == 23.53
    assert s["gross_error"] is False
    finals["c05"] = f("yes")  # reviewer changes 05 -> 80 / 85
    assert scoring.score(CL, finals)["total"] == 94.12


def test_half_up_rounding():
    # 70.5 / 80 * 100 = 88.125 -> 88.13 (Python round() would give 88.12)
    finals = {"c01": f("yes"), "c02": f("na"), "c03": f("yes"), "c04": f("scored", 15),
              "c05": f("yes"), "c06": f("scored", 7), "c07": f("na"), "c08": f("no")}
    assert scoring.score(CL, finals)["total"] == 88.13


def test_no_applicable_weight_and_gross_flag():
    finals = {c["id"]: f("na") for c in CL["criteria"]}
    finals["c05"] = f("na")  # not allowed in real life, but the engine must not divide by zero
    finals["c06"] = f("na")
    finals["c01"] = f("na")
    finals["c08"] = f("yes")
    s = scoring.score(CL, finals)
    assert s["total"] is None and s["gross_error"] is True


def test_missing_result_gives_no_total():
    s = scoring.score(CL, {"c01": f("yes")})
    assert s["total"] is None and "c02" in s["total_note"]
