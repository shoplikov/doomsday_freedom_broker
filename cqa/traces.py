"""Trace of every LLM call (mock included): exact prompt, raw response, timing, tokens, validation.
Each criterion result keeps the ids of the traces it came from, so any verdict can be replayed."""
import json

from . import db


def record(conversation_id, role, attempt, provider, model, criteria_ids, request, response, error,
           tokens_in, tokens_out, latency_ms) -> int:
    with db.tx() as con:
        cur = con.execute(
            "INSERT INTO traces (conversation_id, role, attempt, provider, model, criteria, request, response, error,"
            " tokens_in, tokens_out, latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (conversation_id, role, attempt, provider, model, db.dumps(criteria_ids), db.dumps(request), response,
             error, tokens_in, tokens_out, latency_ms, db.now()))
        return cur.lastrowid


def set_validation(trace_id: int, errors: dict) -> None:
    with db.tx() as con:
        con.execute("UPDATE traces SET validation = ? WHERE id = ?", (db.dumps(errors), trace_id))


def get(trace_id: int) -> dict | None:
    t = db.row("SELECT * FROM traces WHERE id = ?", trace_id)
    if t:
        for k in ("criteria", "request", "validation"):
            t[k] = json.loads(t[k]) if t[k] else None
    return t


def list_for(conversation_id: str) -> list[dict]:
    return db.rows("SELECT id, role, attempt, provider, model, criteria, error, validation, tokens_in, tokens_out,"
                   " latency_ms, created_at FROM traces WHERE conversation_id = ? ORDER BY id", conversation_id)
