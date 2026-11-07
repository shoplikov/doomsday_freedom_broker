"""Import a dataset by group_id from the organizers' API (server-side, so no CORS),
normalize it and store it. Raw JSON is cached in data/raw/ as a backup for demo day."""
import hashlib
import json
import logging
import re
from datetime import datetime

import httpx

from . import config, db, privacy, signals

log = logging.getLogger(__name__)
SPEAKERS = {"client", "operator", "bot", "system"}
# Other spellings of the four roles, so an unseen dataset with "agent" / "customer" is still read right.
SPEAKER_ALIASES = {"agent": "operator", "support": "operator", "manager": "operator", "оператор": "operator",
                   "customer": "client", "user": "client", "клиент": "client", "assistant": "bot", "ai": "bot",
                   "бот": "bot", "event": "system", "система": "system"}
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?$")


def parse_ts(raw) -> float | None:
    """Chats: MM:SS (or HH:MM:SS) -> seconds. Calls: ISO 8601 with timezone -> epoch seconds."""
    if raw is None:
        return None
    s = str(raw).strip()
    m = _CLOCK.match(s)
    if m:
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi) * 60 + int(se)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize(record: dict) -> tuple[dict, list[dict]]:
    cid = str(record["conversation_id"])
    turns = record.get("transcript") or []
    if isinstance(turns, str):  # defensive: the brief says it is already an array
        turns = json.loads(turns)
    stamps = [parse_ts(t.get("timestamp")) for t in turns]
    base = next((s for s in stamps if s is not None), 0)
    mapping: dict = {}
    msgs = []
    for i, turn in enumerate(turns):
        speaker = str(turn.get("speaker") or turn.get("role") or "").strip().lower()
        speaker = SPEAKER_ALIASES.get(speaker, speaker)
        text = str(turn.get("text") or turn.get("message") or "")
        msgs.append({
            "conversation_id": cid,
            "idx": i,
            "mid": f"m{i + 1}",
            "speaker": speaker if speaker in SPEAKERS else "unknown",
            "ts_raw": turn.get("timestamp"),
            "t_sec": None if stamps[i] is None else round(stamps[i] - base, 3),  # seconds from first message
            "text": text,
            "text_masked": privacy.mask(text, mapping),
            "lang": signals.detect_lang(text),
        })
    digest = hashlib.sha256(db.dumps([[m["speaker"], m["text_masked"]] for m in msgs]).encode()).hexdigest()
    conv = {
        "id": cid,
        "channel": record.get("channel"),
        "language": record.get("language"),
        "audio_url": record.get("audio_url"),
        "transcript_hash": digest,
        "raw_json": db.dumps(record),
    }
    return conv, msgs


async def fetch_records(group_id: str) -> list[dict]:
    raw_path = config.RAW_DIR / f"{group_id}.json"
    fixture = config.DATA_DIR / "fixtures" / f"{group_id}.json"
    body = None
    if config.DATASET_API_URL:
        url = config.DATASET_API_URL
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                if "{group_id}" in url:
                    resp = await client.get(url.replace("{group_id}", group_id))
                else:
                    resp = await client.get(url, params={"group_id": group_id})
                resp.raise_for_status()
                body = resp.json()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("API fetch failed for %s (%s); trying the cached copy", group_id, exc)
    if body is None:
        source = fixture if fixture.exists() else raw_path
        if not source.exists():
            raise LookupError(f"No data for group '{group_id}': set DATASET_API_URL or use group SAMPLE")
        body = json.loads(source.read_text(encoding="utf-8"))
    if not body.get("isSuccess", True):
        raise RuntimeError(f"Organizers' API error: {body.get('error')}")
    return body["data"]["records"]


def save(group_id: str, records: list[dict]) -> int:
    with db.tx() as con:
        for record in records:
            conv, msgs = normalize(record)
            con.execute(
                "INSERT INTO conversations (id, group_id, channel, language, audio_url, transcript_hash, raw_json,"
                " imported_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET"
                " group_id = EXCLUDED.group_id, channel = EXCLUDED.channel, language = EXCLUDED.language,"
                " audio_url = EXCLUDED.audio_url, transcript_hash = EXCLUDED.transcript_hash,"
                " raw_json = EXCLUDED.raw_json, imported_at = EXCLUDED.imported_at",
                (conv["id"], group_id, conv["channel"], conv["language"], conv["audio_url"],
                 conv["transcript_hash"], conv["raw_json"], db.now()),
            )
            con.execute("DELETE FROM messages WHERE conversation_id = %s", (conv["id"],))
            con.cursor().executemany(
                "INSERT INTO messages (conversation_id, idx, mid, speaker, ts_raw, t_sec, text, text_masked, lang)"
                " VALUES (%(conversation_id)s, %(idx)s, %(mid)s, %(speaker)s, %(ts_raw)s, %(t_sec)s, %(text)s,"
                " %(text_masked)s, %(lang)s)",
                msgs,
            )
    return len(records)


async def import_group(group_id: str) -> int:
    return save(group_id, await fetch_records(group_id))


def messages(conversation_id: str) -> list[dict]:
    return db.rows("SELECT * FROM messages WHERE conversation_id = %s ORDER BY idx", conversation_id)
