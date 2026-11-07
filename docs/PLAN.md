# Team plan: 4 lanes in parallel, 24 hours

The skeleton already runs end to end on the mock LLM, so **no lane waits for another**.
Each lane replaces stubs or extends its own files. Lane C works against the mock from minute one,
and lane B works on real prompts in isolation.

## Lanes and ownership

| Lane | Person | Owns (files) | Mission |
|---|---|---|---|
| **A · Engine** | _name_ | `importer.py`, `signals.py`, `privacy.py`, `scoring.py`, `evaluations.py`, `db.py`, `main.py` | Real data in, runs fast and never loses work; math is exact |
| **B · Judge** | _name_ | `pipeline.py`, `llm.py`, `checklist.py`, `data/checklist_v1.json` | The three-model panel is accurate in RU/KZ/EN and cheap |
| **C · UI** | _name_ | `web/**` | The 5-minute demo looks clear and never breaks |
| **D · Data & pitch** | _name_ | `data/gold/`, `docs/DEMO.md`, `README.md`, tests for gold/red team | Proves quality with numbers; owns demo, control round, submission |

With 3 people, D's work splits between B (gold set and red team) and C (pitch and README).

## Hour 0–1: everyone together

- [ ] Clone, `uv sync`, `uv run uvicorn cqa.main:app --reload`, open the app, `uv run pytest -q`
- [ ] Read `CLAUDE.md` and `docs/CONTRACTS.md`, then freeze the contracts
- [ ] Put the real DEV link in `.env` (`DATASET_API_URL` with `{group_id}`), import DEV, check 65 records
- [ ] Put the OpenAI key in `.env`; B runs one real evaluation to prove the path
- [ ] Create the public GitHub repo, send the link to the organizers (brief: required before stop-code)
- [ ] Fill in names in the table above

## Timeline

| Hours | A · Engine | B · Judge | C · UI | D · Data & pitch |
|---|---|---|---|---|
| 1–4 | Import real DEV; verify timestamps of calls; mask check on DEV (grep for 12-digit numbers left) | **KZ bake-off**: 2–3 candidate models × 15 KZ chats, then pick models (DECISIONS D-03) | Polish list + card on mock data; evidence jump; audio player on a call | Label the **gold set** in the UI («эталон» on each criterion): 20 DEV dialogs (≥5 KZ, ≥5 EN, 5 calls, N/A cases, 1 gross error); protocol in VALIDATION.md |
| 4–8 | Signals tuning (KZ detection, wait warnings); runs: retry-failed button endpoint | Prompt iteration with `cli report` + `cli trace`; atoms wording; `PROMPT_VERSION` bumps | Appeal + decision UX polish; review queue | Read «Аналитика»: accuracy per role vs gold, gold misses → report to B |
| 8–12 | Load test: 65 dialogs at `CONCURRENCY` 8–10, measure minutes and $ | Validator false-reject rate; tune retry feedback; `JUDGE_MODE` choice | Checklist editor: replace criterion flow end to end, diff view | Red-team mutations (drop greeting, insert side-deal offer, late verification): verdicts must flip |
| **12** | **Checkpoint: real DEV end to end. If not, cut every "if ahead" item.** | | | |
| 12–16 | Cost meter totals per run; export audit | Calibrate `TRUST_THRESHOLD` on gold set | Score waterfall / sections; what-if (optional) | Calibration panel data (agreement per criterion + KZ filter) |
| 16–18 | Bug fixes only | "If ahead": Criterion Studio AI draft of a new criterion | Calibration badge on the card | README model + $ per evaluation; pitch narrative |
| **18** | **Feature freeze** | | | |
| 18–20 | **Control-round drill on DEV** with a stopwatch (see DEMO.md) | | | |
| 20–23 | Fix whatever was slow; pin demo dialogs; demo-mode backup (cached DB) | | Rehearse demo ×3 | Backup screen recording |
| 23–24 | **Control round**: import, run, spot-check, criterion swap through the UI, final commit before stop-code | | | |

## Definition of done (for any task)

- Works through the UI on DEV data, not only in a test
- `uv run pytest -q` is green; new logic has one small test
- Contract unchanged, or CONTRACTS.md updated and the team told
- No secrets, no debug prints, no dead code

## Git workflow

- `main` always runs. Short branches `a/…`, `b/…`, `c/…`, `d/…`, PR to main, one teammate glances at it.
- Rebase often, because lanes own different files and conflicts should be rare.
- Commit messages: imperative, English, no AI attribution.

## Cut rules

1. At 12h, if real DEV is not end to end: stop all "if ahead" work and everyone fixes the core path.
2. Anything that endangers the 5-minute demo or the control round is cut, no debate.
3. Roadmap items are pitched on a slide, not built (see DECISIONS.md, "Pitch only").

## Scope (Balanced)

**Build:** foundation (checklist as data, model judges / code counts, evidence-locked verdicts,
signals, appeals, control-round pipeline, Dynamics-ready UI), atoms, the three-model panel,
privacy masks, Criterion Studio editor + lint, gold set + calibration, red-team suite.

**If ahead (after 12h checkpoint):** AI-drafted criterion, what-if toggles, golden replies, risk-radar alert.

**Pitch only:** appeal-driven prompt evolution (GEPA), MCP server, audio model signals,
contact-reason insights, KZ AI law compliance framing, PCF control.
