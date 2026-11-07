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
| Background ideas (not binding) | `sol_approach_design/*.pdf` |
| Decisions | `docs/DECISIONS.md` |

## Commands

```bash
uv sync
uv run uvicorn cqa.main:app --reload     # http://localhost:8000, mock LLM by default
uv run pytest -q                         # must stay green on main
uv run --with playwright pytest tests/e2e -q   # browser demo flow; run before merging UI changes
uv run python -m cqa.cli report          # is the panel working? (see docs/VALIDATION.md)
```

## Code map

```
cqa/config.py       env settings (.env)                 cqa/main.py         FastAPI routes + static UI
cqa/importer.py     API fetch, normalize, store         cqa/evaluations.py  evaluate, cache, appeals, decisions, runs, diff
cqa/privacy.py      typed masks [IIN_1] ...             cqa/pipeline.py     3-role prompts, validator, confidence
cqa/signals.py      facts for the LLM                   cqa/llm.py          the only OpenAI caller
cqa/checklist.py    versions, lint, fingerprint         cqa/mock_llm.py     fake LLM for dev and tests
cqa/scoring.py      the math (no LLM)                   cqa/db.py           SQLite + audit helper
cqa/traces.py       every LLM call: prompt, answer, ms  cqa/analytics.py    panel metrics vs gold labels
cqa/cli.py          import / run / report / trace from the terminal
web/                vanilla JS Web Components, no build; web/api.js is the only fetch adapter
```

## Team conventions

- One lane owns each area (see `docs/PLAN.md`). Touching another lane's files: ping the owner first.
- Contracts in `docs/CONTRACTS.md` are frozen after hour 1. Changing one = update the doc in the same PR
  and tell the team.
- Small PRs into `main`, `pytest` green, no direct pushes of broken code. Branch names: `a/...`, `b/...`, `c/...`, `d/...`.
- No secrets in git (`.env` is ignored). No Claude/AI attribution lines in commits or PRs.
- Changing prompts? Bump `PROMPT_VERSION` in `cqa/config.py` so the cache does not serve old verdicts.
- Keep it simple: no new dependency, abstraction or config flag unless the demo needs it.
  Deliberate shortcuts carry a `ponytail:` comment naming the limit.
- UI text is Russian; code, docs and commit messages are English.
