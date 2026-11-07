# CQA: working rules

Read this first. People and AI agents both follow it. Details live in `docs/`.

## What we are building (one line)

Transcript → code prepares facts → three LLM roles (Inspector, Advocate, Judge) answer yes/no atoms
with quotes → code validates quotes and counts points → humans decide disputes → checklist is versioned.

## The five rules (never break)

1. **The checklist is data, never code.** Criteria live in versioned JSON (`data/checklist_v1.json`
   is the seed; new versions are rows in `checklists`). A criterion change = UI save, no deploy.
2. **The model judges, code counts.** The LLM never computes points, totals or rounding.
   All math is in `cqa/scoring.py` (Decimal, half-up, 2 dp, N/A redistribution).
3. **No verdict without a real message ID.** Every quote is checked against the cited message by
   `pipeline.validate`. Missing actions cite a `checked_range`. Invalid → retry once → human review.
4. **Humans decide disputes; everything is logged.** AI original, decision and final are all kept.
   Every change writes to the `audit` table.
5. **Original language in, original quotes out.** Never translate before judging.
   Personal data is masked (`cqa/privacy.py`) before any LLM call.

## Source of truth

| Topic | Where |
|---|---|
| Task, demo plan, API format | `initial_docs/CQA_Hackathon_Student_Guide_v3 1.pdf` |
| Criteria, weights, scales, N/A, rounding | `initial_docs/CQA_Chat_Quality_Checklist 1.pdf` → `data/checklist_v1.json` |
| Results file to submit (`results/{dev,prod}/results.json`) | `initial_docs/CQA_Student_Guide_Appendix_Results.pdf` → `cqa/results.py`, `docs/CONTRACTS.md` §7 |
| Background ideas (not binding) | `sol_approach_design/*.pdf` |
| Decisions | `docs/DECISIONS.md` |

## Commands

```bash
docker compose up -d --build            # app + Postgres in containers (port CQA_PORT, default 8005)
docker compose --profile test run --rm test
uv sync                                  # local dev without Docker needs a Postgres (DATABASE_URL), e.g.:
docker run -d --name cqa-pg -e POSTGRES_USER=cqa -e POSTGRES_PASSWORD=cqa -e POSTGRES_DB=cqa -p 5432:5432 postgres:16-alpine
uv run uvicorn cqa.main:app --reload     # http://localhost:8000, mock LLM by default
uv run pytest -q                         # must stay green on main; own throwaway schema on TEST_DATABASE_URL
uv run --with playwright pytest tests/e2e -q   # browser demo flow; run before merging UI changes
uv run python -m cqa.cli report          # is the panel working? (see docs/VALIDATION.md)
uv run python -m cqa.cli bench --split tune --out results/bench/x.json   # before/after any prompt or checklist change (docs/EVALUATION.md)
```

## Code map

```
cqa/config.py       env settings (.env)                 cqa/main.py         FastAPI routes + static UI
cqa/importer.py     API fetch, normalize, store         cqa/evaluations.py  evaluate, cache, appeals, decisions, runs, diff
cqa/privacy.py      typed masks [IIN_1] ...             cqa/pipeline.py     3-role prompts, validator, confidence
cqa/signals.py      facts for the LLM                   cqa/llm.py          the only OpenAI caller
cqa/checklist.py    versions, lint, fingerprint         cqa/mock_llm.py     fake LLM for dev and tests
cqa/scoring.py      the math (no LLM)                   cqa/db.py           PostgreSQL schema, pool, audit helper
cqa/traces.py       every LLM call: prompt, answer, ms  cqa/analytics.py    panel metrics vs gold labels
cqa/cli.py          import / run / report / trace / redteam / export   cqa/redteam.py  known-answer mutations of real chats
cqa/results.py      organisers' results.json (format 1.0)
cqa/bench.py        eval bench: known answers (data/eval), metrics, compare    cqa/insights.py  stress test, checklist health, test drive, review impact
data/eval/          blind gold labels, synthetic scenarios, labelling guide, candidate checklists (docs/EVALUATION.md)
cqa/sqlite_migrate.py   one-shot `cli migrate-sqlite`: old SQLite file -> Postgres
web/                vanilla JS Web Components, no build; web/api.js is the only fetch adapter
```

## Team conventions

- One lane owns each area (see `docs/PLAN.md`). Touching another lane's files: ping the owner first.
- Contracts in `docs/CONTRACTS.md` are frozen after hour 1. Changing one = update the doc in the same PR
  and tell the team.
- Small PRs into `main`, `pytest` green, no direct pushes of broken code. Branch names: `a/...`, `b/...`, `c/...`, `d/...`.
- No secrets in git (`.env` is ignored). No Claude/AI attribution lines in commits or PRs.
- Changing prompts? Bump `PROMPT_VERSION` in `cqa/config.py` so the cache does not serve old verdicts.
- SQL is PostgreSQL with `%s` placeholders (psycopg 3). A NULL-able filter param needs a type: `%s::text IS NULL`.
- Keep it simple: no new dependency, abstraction or config flag unless the demo needs it.
  Deliberate shortcuts carry a `ponytail:` comment naming the limit.
- UI text is Russian; code, docs and commit messages are English.
