"""HTTP API + static UI. Run: uv run uvicorn cqa.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analytics, checklist, config, db, evaluations, importer, insights, results, signals, traces

config.setup_logging()


@asynccontextmanager
async def lifespan(_app):
    db.init()
    checklist.ensure_seed()
    yield
    db.close()


app = FastAPI(title="CQA · Conversation Quality Analytics", lifespan=lifespan)


def who(request: Request) -> str:
    # No login (the brief says so): the UI's Operator / Reviewer switch sends X-Role.
    return request.headers.get("x-role", "anonymous")[:40]


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(422, exc.args[0] if exc.args else str(exc))


class EvaluateIn(BaseModel):
    conversation_id: str
    checklist_version: int | None = None


class RunIn(BaseModel):
    group_id: str | None = None
    conversation_ids: list[str] | None = None
    checklist_version: int | None = None


class AppealIn(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str


class DecisionIn(BaseModel):
    action: str  # uphold | change
    verdict: str | None = None  # yes | no | na | scored
    step: float | None = None
    reason: str


class ReviewIn(DecisionIn):
    criterion_id: str


class GoldIn(BaseModel):
    conversation_id: str
    criterion_id: str
    label: str | None = None  # yes | no | na | step ("15"); empty removes
    note: str = ""


class StressIn(BaseModel):
    conversation_id: str
    mutation: str
    text: str | None = None  # custom: the operator line to insert
    after_mid: str | None = None


class TestDriveIn(BaseModel):
    criterion: dict
    conversation_ids: list[str] | None = None


class ChecklistIn(BaseModel):
    checklist: dict
    note: str = ""


# ---------- health / data ----------

@app.get("/api/health")
def health():
    return {"ok": True, "db": "postgres", "provider": config.LLM_PROVIDER, "models": config.MODELS, "judge_mode": config.JUDGE_MODE,
            "trust_threshold": config.TRUST_THRESHOLD, "dataset_api": bool(config.DATASET_API_URL)}


@app.post("/api/datasets/{group_id}/import")
async def import_dataset(group_id: str):
    try:
        return {"group_id": group_id, "imported": await importer.import_group(group_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/conversations")
def list_conversations(language: str | None = None, channel: str | None = None, group_id: str | None = None):
    sql = ("SELECT c.id, c.group_id, c.channel, c.language, c.audio_url, e.id AS evaluation_id, e.total, e.status,"
           " e.gross_error, e.checklist_version FROM conversations c"
           " LEFT JOIN evaluations e ON e.id = (SELECT max(id) FROM evaluations WHERE conversation_id = c.id)"
           " WHERE (%s::text IS NULL OR c.language = %s) AND (%s::text IS NULL OR c.channel = %s) AND (%s::text IS NULL OR c.group_id = %s)"
           " ORDER BY c.id")
    return db.rows(sql, language, language, channel, channel, group_id, group_id)


@app.get("/api/results/{group_id}")
def results_file(group_id: str):
    """The organisers' results.json (format 1.0) for a group; the UI offers it as a download."""
    return _guard(results.build, group_id, config.TEAM_NAME)


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = db.row("SELECT id, group_id, channel, language, audio_url, imported_at FROM conversations WHERE id = %s",
                  conversation_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    msgs = importer.messages(conversation_id)
    evals = db.rows("SELECT id, checklist_version, total, total_ai, status, created_at FROM evaluations"
                    " WHERE conversation_id = %s ORDER BY id DESC", conversation_id)
    return {**conv, "messages": msgs, "signals": signals.compute(msgs), "evaluations": evals,
            "latest": evaluations.load(evals[0]["id"]) if evals else None}


# ---------- evaluation ----------

@app.post("/api/evaluations")
async def evaluate_one(body: EvaluateIn):
    try:
        eval_id = await evaluations.evaluate(body.conversation_id, body.checklist_version)
    except LookupError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    return evaluations.load(eval_id)


@app.get("/api/evaluations/{eval_id}")
def get_evaluation(eval_id: int):
    ev = evaluations.load(eval_id)
    if not ev:
        raise HTTPException(404, "evaluation not found")
    return ev


@app.post("/api/runs")
async def start_run(body: RunIn):  # async on purpose: the batch task must start on the event loop
    ids = body.conversation_ids or [r["id"] for r in db.rows(
        "SELECT id FROM conversations WHERE (%s::text IS NULL OR group_id = %s) ORDER BY id", body.group_id, body.group_id)]
    if not ids:
        raise HTTPException(404, "no conversations to evaluate: import a dataset first")
    if body.checklist_version and not checklist.get(body.checklist_version):
        raise HTTPException(404, "checklist version not found")
    return evaluations.start_run(ids, body.checklist_version)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = evaluations.public_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


# ---------- appeals and review ----------

@app.post("/api/appeals")
def file_appeal(body: AppealIn, request: Request):
    return _guard(evaluations.file_appeal, body.evaluation_id, body.criterion_id, body.comment, who(request))


@app.get("/api/appeals")
def list_appeals(state: str | None = None):
    return db.rows("SELECT a.*, e.conversation_id FROM appeals a JOIN evaluations e ON e.id = a.evaluation_id"
                   " WHERE (%s::text IS NULL OR a.state = %s) ORDER BY a.id DESC", state, state)


@app.post("/api/appeals/{appeal_id}/decision")
def decide_appeal(appeal_id: int, body: DecisionIn, request: Request):
    appeal = db.row("SELECT * FROM appeals WHERE id = %s", appeal_id)
    if not appeal:
        raise HTTPException(404, "appeal not found")
    if appeal["state"] != "submitted":
        raise HTTPException(409, f"appeal already {appeal['state']}")
    return _guard(evaluations.decide, appeal["evaluation_id"], appeal["criterion_id"], body.action, body.verdict,
                  body.step, body.reason, who(request), appeal_id)


@app.post("/api/evaluations/{eval_id}/review")
def review_criterion(eval_id: int, body: ReviewIn, request: Request):
    """Reviewer decision on a low-confidence item (no appeal involved)."""
    return _guard(evaluations.decide, eval_id, body.criterion_id, body.action, body.verdict, body.step,
                  body.reason, who(request))


@app.get("/api/review-queue")
def review_queue():
    return evaluations.review_queue()


# ---------- checklists ----------

@app.get("/api/checklists")
def list_checklists():
    return checklist.versions()


@app.get("/api/checklists/active")
def active_checklist():
    return checklist.get()


@app.get("/api/checklists/diff")
def checklist_diff(from_v: int, to_v: int):
    return evaluations.diff(from_v, to_v)


@app.get("/api/checklists/{version}")
def get_checklist(version: int):
    cl = checklist.get(version)
    if not cl:
        raise HTTPException(404, "checklist version not found")
    return cl


@app.post("/api/checklists/lint")
def lint_checklist(body: ChecklistIn):
    errors, warnings = checklist.lint(body.checklist)
    return {"errors": errors, "warnings": warnings}


@app.post("/api/checklists")
def publish_checklist(body: ChecklistIn, request: Request):
    version = _guard(checklist.save, body.checklist, who(request), body.note)
    _, warnings = checklist.lint(body.checklist)
    return {"version": version, "warnings": warnings}


@app.post("/api/checklists/test-drive")
async def test_drive(body: TestDriveIn):
    """Run a draft criterion on a few dialogs before publishing it. Nothing is saved."""
    try:
        return await insights.test_drive(body.criterion, body.conversation_ids)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/checklist-health")
def checklist_health():
    return insights.checklist_health()


# ---------- stress test ----------

@app.get("/api/stress-tests/kinds")
def stress_kinds():
    return [{"id": k, "title": v["title"], "expect": {c: sorted(x) for c, x in (v["expect"] or {}).items()}}
            for k, v in insights.STRESS.items()]


@app.post("/api/stress-tests")
async def run_stress_test(body: StressIn):
    try:
        return await insights.stress_test(body.conversation_id, body.mutation,
                                          {"text": body.text, "after_mid": body.after_mid})
    except LookupError as exc:
        raise HTTPException(404, str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/stress-tests")
def list_stress_tests(conversation_id: str | None = None):
    return insights.stress_tests(conversation_id)


@app.get("/api/stress-tests/summary")
def stress_summary():
    return insights.stress_summary()


# ---------- traces, gold labels, analytics ----------

@app.get("/api/traces")
def list_traces(conversation_id: str):
    return traces.list_for(conversation_id)


@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: int):
    t = traces.get(trace_id)
    if not t:
        raise HTTPException(404, "trace not found")
    return t


@app.get("/api/gold")
def list_gold(conversation_id: str | None = None):
    return db.rows("SELECT * FROM gold_labels WHERE (%s::text IS NULL OR conversation_id = %s) ORDER BY conversation_id, criterion_id",
                   conversation_id, conversation_id)


@app.post("/api/gold")
def set_gold(body: GoldIn, request: Request):
    return _guard(evaluations.set_gold, body.conversation_id, body.criterion_id, body.label, who(request), body.note)


@app.get("/api/analytics")
def get_analytics(checklist_version: int | None = None):
    if checklist_version and not checklist.get(checklist_version):
        raise HTTPException(404, "checklist version not found")
    return analytics.compute(checklist_version)


@app.get("/api/audit")
def audit(limit: int = 200):
    return db.rows("SELECT * FROM audit ORDER BY id DESC LIMIT %s", limit)


# The UI is plain static files: the same files can ship as a Dynamics 365 HTML web resource.
app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
