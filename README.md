# CQA · Conversation Quality Analytics

AI scoring of operator chats and calls (RU / KZ / EN) against an editable checklist.
Every verdict has message-level evidence, plain code does the math, humans decide appeals.
Built for the Freedom Broker CQA hackathon (brief: `initial_docs/`).

## Run

```bash
uv sync                      # Python 3.12 is installed automatically (.python-version)
cp .env.example .env         # optional: works without it (mock LLM, offline SAMPLE data)
uv run uvicorn cqa.main:app --reload
# open http://localhost:8000
```

1. **Диалоги** → group `SAMPLE` (5 demo dialogs) or `DEV` (65 real) → «Загрузить набор» → «Оценить все».
2. Open a dialog → click evidence chips → switch role to **Оператор** → «Оспорить».
3. Switch to **Проверяющий** → «Решение» → the total is recalculated.
4. **Чек-лист** → edit or replace a criterion → «Сохранить как vN+1» → «Применить ко всем диалогам» → diff.
5. On any criterion: «Трасса» (what M1, M2, M3 said, plus raw calls) and «эталон» (gold label). **Аналитика** shows whether the panel works.

Real data and models: set `DATASET_API_URL`, `LLM_PROVIDER=openai`, `OPENAI_API_KEY` and the
`MODEL_*` variables in `.env` (see `.env.example`), then import group `DEV`.

```bash
uv run pytest -q                                   # math, validator, 3-model panel on the OpenAI path (fake model), API flow
uv run --with playwright pytest tests/e2e -q       # the whole demo in a real browser (uses /usr/bin/chromium)

uv run python -m cqa.cli import DEV                # terminal loop: import, evaluate, analyse
uv run python -m cqa.cli run DEV --limit 10
uv run python -m cqa.cli report                    # A/B agreement, judge sides, gold accuracy per role, cost
uv run python -m cqa.cli trace p1-kz-001           # every LLM call of one dialog
```

With `DATASET_API_URL` set, `DEV` is fetched from the organizers' API and cached in `data/raw/` (offline fallback).
Real DEV data is never committed (it has real calls): keep a local copy in `data/fixtures/DEV.json` if needed.
Logs: `data/logs/cqa.log`. Every LLM call (prompt, raw answer, latency, tokens, validation) is in the `traces` table
and behind the «Трасса» button on each criterion.

## Model and cost (required by the brief)

| Role | Model (set in `.env`) | Calls per dialog |
|---|---|---|
| M1 Inspector | `MODEL_INSPECTOR` | 1 (+1 retry if invalid) |
| M2 Advocate | `MODEL_ADVOCATE` | 1 (+1 retry if invalid) |
| M3 Judge | `MODEL_JUDGE` | 1 (+1 retry if invalid); 0 for agreed criteria when `JUDGE_MODE=disputed` |

Cost per evaluation is computed from real token usage × `LLM_PRICES` and shown on every card.

**Measured on the full DEV set (65 dialogs, 2026-11-07):** M1/M2 `gpt-4.1-mini`, M3 `gpt-4.1`, `JUDGE_MODE=always`
→ **≈ $0.023 per dialog** (all calls incl. retries), $1.50 for DEV, 2 min 50 s at `CONCURRENCY=8`, 0 failed,
9 invented/missing quotes caught by the validator and fixed on retry, 7 dialogs sent to human review.
Alternative `gpt-5-mini` ×2 + `gpt-5`: ≈ $0.058 and ~90 s per dialog, more unanimous answers.
Re-running is free for unchanged criteria (cache). `cli run` with real models asks for `--yes` and shows an estimate.

## Docs

- [CLAUDE.md](CLAUDE.md): rules for everyone (people and AI agents) working in this repo
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): pipeline, three-model panel, scoring, data model
- [docs/CONTRACTS.md](docs/CONTRACTS.md): JSON shapes, REST API, DB tables
- [docs/PLAN.md](docs/PLAN.md): 4 lanes, ownership, hour-by-hour plan, cut rules
- [docs/DEMO.md](docs/DEMO.md): 5-minute demo script, control-round runbook, jury Q&A
- [docs/DECISIONS.md](docs/DECISIONS.md): decisions made and still open
- [docs/VALIDATION.md](docs/VALIDATION.md): how we prove the three-model panel works (gold set, metrics, decision rules)
- [docs/DEPLOY.md](docs/DEPLOY.md): environments (`main` → prod, `dev` → :8080), CI/CD, releases and rollback, server and DB backups
