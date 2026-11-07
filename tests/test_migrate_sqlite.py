"""migrate-sqlite: an old SQLite file (pre-Postgres schema, gold_labels.by) is copied row for row into Postgres."""
import re
import sqlite3

import pytest

from cqa import cli, config, db, traces

# The SQLite schema as it was before the move to Postgres.
OLD_SCHEMA = """
CREATE TABLE conversations (id TEXT PRIMARY KEY, group_id TEXT, channel TEXT, language TEXT, audio_url TEXT,
    transcript_hash TEXT, raw_json TEXT, imported_at TEXT);
CREATE TABLE messages (conversation_id TEXT, idx INTEGER, mid TEXT, speaker TEXT, ts_raw TEXT, t_sec REAL,
    text TEXT, text_masked TEXT, lang TEXT, PRIMARY KEY (conversation_id, idx));
CREATE TABLE checklists (version INTEGER PRIMARY KEY, json TEXT NOT NULL, note TEXT, created_by TEXT, created_at TEXT);
CREATE TABLE evaluations (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, checklist_version INTEGER,
    status TEXT, total_ai REAL, total REAL, gross_error INTEGER, models TEXT, prompt_version TEXT,
    tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL, result_json TEXT, created_at TEXT);
CREATE TABLE appeals (id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id INTEGER, criterion_id TEXT, comment TEXT,
    filed_by TEXT, state TEXT, reason TEXT, decided_by TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, who TEXT, entity TEXT, entity_id TEXT,
    action TEXT, before TEXT, after TEXT, reason TEXT);
CREATE TABLE cache (key TEXT PRIMARY KEY, value TEXT, created_at TEXT);
CREATE TABLE traces (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, attempt INTEGER,
    provider TEXT, model TEXT, criteria TEXT, request TEXT, response TEXT, error TEXT,
    validation TEXT, tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, created_at TEXT);
CREATE TABLE gold_labels (conversation_id TEXT, criterion_id TEXT, label TEXT, fingerprint TEXT, note TEXT, by TEXT,
    at TEXT, PRIMARY KEY (conversation_id, criterion_id));
"""
T = "2026-11-01T10:00:00+00:00"


@pytest.fixture
def old_file(tmp_path):
    path = tmp_path / "cqa.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("INSERT INTO conversations VALUES ('c1', 'DEV', 'agent chat', 'RU', NULL, 'h', '{}', ?)", (T,))
    con.executemany("INSERT INTO messages VALUES ('c1', ?, ?, 'client', '00:01', ?, 'Привет', 'Привет', 'ru')",
                    [(0, "m1", 0.0), (1, "m2", 12.5)])
    con.execute("INSERT INTO checklists VALUES (1, '{\"criteria\": []}', 'seed', 'system', ?)", (T,))
    con.execute("INSERT INTO checklists VALUES (2, '{\"criteria\": []}', 'v2', 'reviewer', ?)", (T,))
    con.execute("INSERT INTO evaluations VALUES (5, 'c1', 1, 'auto_accepted', 87.35, 90.1, 0, '{}', 'p1', 10, 2,"
                " 0.0123, '{\"criteria\": []}', ?)", (T,))
    con.execute("INSERT INTO appeals VALUES (3, 5, 'c01', 'не согласен', 'operator', 'submitted', NULL, NULL, ?, NULL)",
                (T,))
    con.execute("INSERT INTO audit VALUES (9, ?, 'system', 'evaluation', '5', 'evaluate', NULL, '{\"total\": 87.35}',"
                " NULL)", (T,))
    con.execute("INSERT INTO cache VALUES ('k', '{}', ?)", (T,))
    con.execute("INSERT INTO traces VALUES (7, 'c1', 'judge', 1, 'mock', 'm', '[]', '{}', 'r', NULL, NULL, 1, 2, 30, ?)",
                (T,))
    con.execute("INSERT INTO gold_labels VALUES ('c1', 'c01', 'yes', 'fp', '', 'reviewer', ?)", (T,))
    con.commit()
    con.close()
    return path


@pytest.fixture
def empty_target(monkeypatch, fresh_database_url):
    monkeypatch.setattr(config, "DATABASE_URL", fresh_database_url())
    db.init()
    yield
    db.close()


def test_migrate_copies_everything_and_keeps_ids(old_file, empty_target, capsys):
    with db.tx() as con:  # what a freshly started app writes: the seed checklist
        con.execute("INSERT INTO checklists (version, json) VALUES (1, '{}')")
    cli.migrate_sqlite(old_file, replace=False)
    out = capsys.readouterr().out
    assert re.search(r"gold_labels\s+1\s+1\s+ok", out) and "MISMATCH" not in out

    for table in db.TABLES:
        assert db.row(f"SELECT count(*) AS n FROM {table}")["n"] > 0, table
    assert db.row("SELECT labelled_by FROM gold_labels")["labelled_by"] == "reviewer"
    ev = db.row("SELECT * FROM evaluations")
    assert ev["id"] == 5 and ev["total_ai"] == 87.35 and ev["cost_usd"] == 0.0123
    assert [m["t_sec"] for m in db.rows("SELECT t_sec FROM messages ORDER BY idx")] == [0.0, 12.5]
    assert db.row("SELECT json FROM checklists WHERE version = 1")["json"] == '{"criteria": []}'

    # new rows continue after the copied ids
    assert traces.record("c1", "judge", 1, "mock", "m", [], {}, "r", None, 1, 2, 3) == 8
    with db.tx() as con:
        new_eval = con.execute("INSERT INTO evaluations (conversation_id) VALUES ('c1') RETURNING id").fetchone()["id"]
        new_appeal = con.execute("INSERT INTO appeals (evaluation_id) VALUES (5) RETURNING id").fetchone()["id"]
        db.audit(con, "t", "x", 1, "y")
    assert (new_eval, new_appeal) == (6, 4)
    assert db.row("SELECT max(id) AS id FROM audit")["id"] == 10


def test_migrate_refuses_a_target_with_data_unless_replace(old_file, empty_target):
    cli.migrate_sqlite(old_file, replace=False)
    with pytest.raises(SystemExit, match="--replace"):
        cli.migrate_sqlite(old_file, replace=False)
    cli.migrate_sqlite(old_file, replace=True)
    assert db.row("SELECT count(*) AS n FROM conversations")["n"] == 1
    assert db.row("SELECT count(*) AS n FROM evaluations")["n"] == 1


def test_migrate_missing_file(tmp_path, empty_target):
    with pytest.raises(SystemExit, match="not found"):
        cli.migrate_sqlite(tmp_path / "nope.sqlite3", replace=False)
