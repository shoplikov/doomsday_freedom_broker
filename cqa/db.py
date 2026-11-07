"""SQLite storage. JSON lives in TEXT columns. One short-lived connection per operation."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, group_id TEXT, channel TEXT, language TEXT, audio_url TEXT,
    transcript_hash TEXT, raw_json TEXT, imported_at TEXT);
CREATE TABLE IF NOT EXISTS messages (
    conversation_id TEXT, idx INTEGER, mid TEXT, speaker TEXT, ts_raw TEXT, t_sec REAL,
    text TEXT, text_masked TEXT, lang TEXT, PRIMARY KEY (conversation_id, idx));
CREATE TABLE IF NOT EXISTS checklists (
    version INTEGER PRIMARY KEY, json TEXT NOT NULL, note TEXT, created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, checklist_version INTEGER,
    status TEXT, total_ai REAL, total REAL, gross_error INTEGER, models TEXT, prompt_version TEXT,
    tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL, result_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id INTEGER, criterion_id TEXT, comment TEXT,
    filed_by TEXT, state TEXT, reason TEXT, decided_by TEXT, created_at TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, who TEXT, entity TEXT, entity_id TEXT,
    action TEXT, before TEXT, after TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, attempt INTEGER,
    provider TEXT, model TEXT, criteria TEXT, request TEXT, response TEXT, error TEXT,
    validation TEXT, tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS gold_labels (
    conversation_id TEXT, criterion_id TEXT, label TEXT, fingerprint TEXT, note TEXT, by TEXT, at TEXT,
    PRIMARY KEY (conversation_id, criterion_id));
CREATE INDEX IF NOT EXISTS ix_eval_conv ON evaluations (conversation_id);
CREATE INDEX IF NOT EXISTS ix_trace_conv ON traces (conversation_id);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def tx():
    """A transaction: commits on success, rolls back on error."""
    con = connect()
    try:
        with con:
            yield con
    finally:
        con.close()


def init() -> None:
    with tx() as con:
        con.executescript(SCHEMA)


def rows(sql: str, *args) -> list[dict]:
    with tx() as con:
        return [dict(r) for r in con.execute(sql, args)]


def row(sql: str, *args) -> dict | None:
    found = rows(sql, *args)
    return found[0] if found else None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def audit(con, who, entity, entity_id, action, before=None, after=None, reason=None) -> None:
    """Every change to appeals, verdicts and checklists writes one line here."""
    con.execute(
        "INSERT INTO audit (at, who, entity, entity_id, action, before, after, reason) VALUES (?,?,?,?,?,?,?,?)",
        (now(), who, entity, str(entity_id), action,
         None if before is None else dumps(before), None if after is None else dumps(after), reason),
    )
