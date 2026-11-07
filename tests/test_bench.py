"""Bench: mutations set the right expectations, metrics count what they claim, a mock run end to end."""
import asyncio

from cqa import bench, db

TURNS = [
    {"timestamp": "00:00", "speaker": "client", "text": "Как вывести деньги на карту?"},
    {"timestamp": "00:05", "speaker": "system", "text": "Чат передан оператору."},
    {"timestamp": "01:00", "speaker": "operator", "text": "Здравствуйте, меня зовут Аня. Сейчас подскажу."},
    {"timestamp": "01:30", "speaker": "client", "text": "Спасибо, жду ответа по выводу."},
    {"timestamp": "02:00", "speaker": "operator", "text": "Откройте «Вывести» в приложении. Чем-то ещё помочь? До свидания!"},
]
EXPECT = bench._expect({"c01": {"label": "yes"}, "c02": {"label": "na"}, "c03": {"label": "na"},
                        "c04": {"label": "20"}, "c05": {"label": "yes"}, "c06": {"label": "10", "accept": ["7"]},
                        "c07": {"label": "yes"}, "c08": {"label": "no"}})
ITEM = {"id": "t-ru-001", "source": "synthetic", "split": "tune", "language": "RU", "channel": "agent chat",
        "record": {"conversation_id": "t-ru-001", "channel": "agent chat", "language": "RU", "transcript": TURNS},
        "expect": EXPECT}


def test_labels_normalized():
    assert EXPECT["c04"] == {"label": "20", "accept": ["20"]}
    assert EXPECT["c06"]["accept"] == ["10", "7"]
    assert bench.norm_label(15) == "15" and bench.norm_label("N/A") == "na"


def test_mutations_change_the_text_and_set_targets():
    turns, t = bench.mut_side_deal(TURNS, "RU", EXPECT)
    assert t == {"c08": {"yes"}} and len(turns) == len(TURNS) + 1 and turns[3]["speaker"] == "operator"
    turns, t = bench.mut_drop_greeting(TURNS, "RU", EXPECT)
    assert t == {"c01": {"no"}} and "Здравствуйте" not in turns[2]["text"]
    turns, t = bench.mut_wait(TURNS, "RU", EXPECT)
    assert t == {"c06": {"7", "4"}} and turns[4]["timestamp"] == "07:00"
    turns, t = bench.mut_client_left(TURNS, "RU", EXPECT)
    assert turns[-1]["speaker"] == "system" and t == {"c07": {"na"}}
    turns, t = bench.mut_harmless(TURNS, "RU", EXPECT)
    assert turns[0]["text"] != TURNS[0]["text"] and t["c04"] == {"20"}


def test_mock_run_and_metrics():
    db.init()
    items = [ITEM] + bench.mutation_items([ITEM], 1)
    assert {i["mutation"] for i in items[1:]} >= {"side_deal", "drop_greeting", "harmless"}
    runs = [asyncio.run(bench.run(items, bench.load_checklist()))]
    m = bench.metrics(runs)
    assert m["items"] == len(items) and m["ops"]["failed_cells"] == 0
    assert set(m["by_criterion"]) == {f"c0{i}" for i in range(1, 9)}
    assert m["c08_n"][0] >= 2  # side_deal + side_deal_soft are positives
    assert runs[0][0]["total"]["expected"] == 100.0
    doc = {"runs": runs}
    assert bench.compare(doc, doc)["fixed"] == [] and bench.mcnemar_p(0, 0) == 1.0
    assert bench.mcnemar_p(10, 0) < 0.01


def test_checkpoint_resumes(tmp_path):
    db.init()
    cp = tmp_path / "run.0.partial.jsonl"
    first = asyncio.run(bench.run([ITEM], bench.load_checklist(), checkpoint=cp))
    assert len(cp.read_text().splitlines()) == 1
    cp.write_text(cp.read_text() + '{"id": "cut')  # a line cut by a kill is ignored
    again = asyncio.run(bench.run([ITEM], bench.load_checklist(), checkpoint=cp))
    assert again[0]["criteria"] == first[0]["criteria"] and len(cp.read_text().splitlines()) == 2
