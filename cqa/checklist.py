"""Checklist as data. Never edited in place: every save creates version N+1, and every
evaluation records the version it used, so past scores stay reproducible."""
import hashlib
import json
import re

from . import config, db

SEED = config.DATA_DIR / "checklist_v1.json"
TYPES = {"binary", "scale", "flag"}
VAGUE = ["хорошо", "нормально", "правильно", "адекватно", "properly", "appropriate", "good", "adequate", "жақсы"]
_ID = re.compile(r"^[a-z0-9_]+$")


def lint(cl: dict) -> tuple[list[str], list[str]]:
    """Errors block saving. Warnings are shown in Criterion Studio."""
    errors, warnings = [], []
    criteria = cl.get("criteria") or []
    if not criteria:
        errors.append("checklist has no criteria")
    sections = {s.get("id") for s in cl.get("sections") or []}
    seen = set()
    for c in criteria:
        cid = c.get("id", "?")
        where = f"{cid}:"
        if not _ID.match(str(cid)):
            errors.append(f"{where} id must match [a-z0-9_]+")
        if cid in seen:
            errors.append(f"{where} duplicate id")
        seen.add(cid)
        if c.get("type") not in TYPES:
            errors.append(f"{where} type must be one of {sorted(TYPES)}")
            continue
        if not str(c.get("title", "")).strip():
            errors.append(f"{where} title is empty")
        if not str(c.get("definition", "")).strip():
            errors.append(f"{where} definition is empty")
        if not isinstance(c.get("weight"), (int, float)) or c["weight"] < 0:
            errors.append(f"{where} weight must be a number >= 0")
        if c["type"] == "flag" and c.get("weight"):
            warnings.append(f"{where} flag criteria never give points; weight is ignored")
        if c.get("na_allowed") and not str(c.get("na_condition", "")).strip():
            errors.append(f"{where} N/A is allowed but na_condition is empty")
        if c.get("section") and sections and c["section"] not in sections:
            warnings.append(f"{where} unknown section '{c['section']}'")
        if c["type"] in ("binary", "flag"):
            atoms = c.get("atoms") or []
            if not atoms:
                errors.append(f"{where} binary/flag criteria need at least one yes/no atom")
            ids = [a.get("id") for a in atoms]
            if len(ids) != len(set(ids)) or not all(ids) or not all(_ID.match(str(i)) for i in ids):
                errors.append(f"{where} atom ids must be unique and match [a-z0-9_]+")
            if c.get("rule", "all") not in ("all", "any"):
                errors.append(f"{where} rule must be 'all' or 'any'")
        if c["type"] == "scale":
            steps = [s.get("step") for s in c.get("scale") or []]
            if not steps or not all(isinstance(s, (int, float)) for s in steps):
                errors.append(f"{where} scale needs numeric steps")
            elif max(steps) <= 0:
                errors.append(f"{where} top scale step must be > 0")
            elif len(set(steps)) != len(steps):
                errors.append(f"{where} duplicate scale steps")
            elif 0 not in steps:
                warnings.append(f"{where} scale has no 0 step")
        text = " ".join([str(c.get("definition", ""))] + [str(a.get("q", "")) for a in c.get("atoms") or []]).lower()
        for word in VAGUE:
            if re.search(rf"\b{re.escape(word)}\b", text):
                warnings.append(f"{where} vague word '{word}': say what exactly counts")
    total = sum(c.get("weight") or 0 for c in criteria if c.get("type") != "flag")
    if criteria and total != 100:
        warnings.append(f"scored weights sum to {total}, not 100 (the total is still normalised to 100)")
    return errors, warnings


def ensure_seed() -> None:
    if not db.row("SELECT version FROM checklists LIMIT 1"):
        with db.tx() as con:
            con.execute(
                "INSERT INTO checklists (version, json, note, created_by, created_at) VALUES (1, ?, ?, ?, ?)",
                (SEED.read_text(encoding="utf-8"), "seed from organizers' PDF", "system", db.now()),
            )


def get(version: int | None = None) -> dict | None:
    r = (db.row("SELECT * FROM checklists WHERE version = ?", version) if version
         else db.row("SELECT * FROM checklists ORDER BY version DESC LIMIT 1"))
    if not r:
        return None
    return {**json.loads(r["json"]), "version": r["version"], "note": r["note"], "created_at": r["created_at"]}


def versions() -> list[dict]:
    return db.rows("SELECT version, note, created_by, created_at FROM checklists ORDER BY version DESC")


def save(cl: dict, who: str, note: str = "") -> int:
    errors, _ = lint(cl)
    if errors:
        raise ValueError(errors)
    body = {k: v for k, v in cl.items() if k not in ("version", "note", "created_at")}
    with db.tx() as con:
        prev = con.execute("SELECT max(version) FROM checklists").fetchone()[0] or 0
        version = prev + 1
        con.execute(
            "INSERT INTO checklists (version, json, note, created_by, created_at) VALUES (?,?,?,?,?)",
            (version, db.dumps(body), note, who, db.now()),
        )
        db.audit(con, who, "checklist", version, "publish", before={"version": prev}, after={"version": version}, reason=note)
    return version


def fingerprint(c: dict) -> str:
    """Hash of everything the LLM sees for this criterion. Weight and section are excluded on purpose:
    changing a weight re-scores in code without calling the LLM again."""
    keep = {k: c.get(k) for k in ("id", "type", "title", "definition", "na_allowed", "na_condition", "atoms", "scale", "rule")}
    return hashlib.sha256(json.dumps(keep, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
