"""Unseen data must never crash the system: odd speaker names, empty or operator-less dialogs, odd timestamps."""
import asyncio

from cqa import bench, importer, pipeline, signals


def _msgs(turns):
    return importer.normalize({"conversation_id": "u-1", "channel": "agent chat", "language": "RU",
                               "transcript": turns})[1]


def test_speaker_aliases_and_alternative_keys():
    msgs = _msgs([{"timestamp": "00:01", "role": "Customer", "message": "Привет"},
                  {"timestamp": "00:05", "speaker": "Agent", "text": "Здравствуйте, я Анна"},
                  {"timestamp": "00:06", "speaker": "assistant", "text": "..."},
                  {"timestamp": "00:07", "speaker": "weird", "text": "?"}])
    assert [m["speaker"] for m in msgs] == ["client", "operator", "bot", "unknown"]
    assert msgs[0]["text"] == "Привет"


def test_signals_survive_degenerate_dialogs():
    for turns in ([], [{"timestamp": None, "speaker": "client", "text": "алло"}],
                  [{"timestamp": "bad", "speaker": "bot", "text": "hi"}, {"timestamp": "", "speaker": "system", "text": "x"}],
                  [{"timestamp": "2026-11-08T10:00:00+05:00", "speaker": "operator", "text": "Здравствуйте"},
                   {"timestamp": "00:10", "speaker": "client", "text": "мixed clocks"}]):
        facts = signals.compute(_msgs(turns))
        assert "first_operator_reply" in facts


def test_mock_panel_on_operatorless_dialog_returns_every_criterion():
    cl = bench.load_checklist()
    msgs = _msgs([{"timestamp": "00:01", "speaker": "client", "text": "Здравствуйте"},
                  {"timestamp": "00:02", "speaker": "bot", "text": "Соединяю с оператором"},
                  {"timestamp": "00:30", "speaker": "system", "text": "Клиент покинул чат."}])
    out, _ = asyncio.run(pipeline.evaluate_criteria(cl["criteria"], msgs, signals.compute(msgs), "u-1"))
    assert set(out) == {c["id"] for c in cl["criteria"]}
