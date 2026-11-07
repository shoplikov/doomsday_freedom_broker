# Decisions

Short log. New decision → add a row; change one → strike through and add the new row, never delete.

## Made

| ID | Date | Decision | Why |
|---|---|---|---|
| D-01 | 2026-11-07 | Stack: Python 3.12 + FastAPI + SQLite (stdlib), vanilla JS Web Components, no build step | Matches the brief (HTML/CSS/JS, D365-ready); fastest for 4 people; nothing to install beyond `uv sync` |
| D-02 | 2026-11-07 | LLM provider: **OpenAI only** (team has a key). Three roles: Inspector, Advocate, Judge | One provider, but agreement still means something because the roles are opposed |
| D-04 | 2026-11-07 | Scope: **Balanced** (see PLAN.md) | Safe for hidden tests; leaves room for gold set + red team, which prove quality |
| D-05 | 2026-11-07 | Docs: `CLAUDE.md` + `docs/`, in English. UI text in Russian | Shared by people and AI agents; jury-facing UI in the checklist's language |
| D-06 | 2026-11-07 | Checklist v1 = organizers' PDF, transcribed into `data/checklist_v1.json` with atoms | The brief says to use it as the initial configuration |
| D-07 | 2026-11-07 | All criteria in one call per role, not one call per criterion | 3 calls per dialog instead of 24; shared prompt cache; enough context for 8 criteria |
| D-08 | 2026-11-07 | Confidence = share of the roles agreeing with the final verdict; threshold 0.67 until calibrated | Simple, explainable, tunable on the gold set |
| D-09 | 2026-11-07 | Mock LLM is the default | Everyone can run and build without a key; tests are deterministic |
| D-15 | 2026-11-07 | Trace every LLM call (mock too) in SQLite; per-criterion results keep trace ids and each role's answer | Any verdict can be replayed and explained; this is how we debug prompts and prove the panel |
| D-16 | 2026-11-07 | Gold labels are tied to the criterion fingerprint | A reworded criterion must not be scored against labels made for the old wording |
| D-17 | 2026-11-07 | Only the judge voted (M1 and M2 both invalid) → needs_review, even if the judge's answer is valid | One voice is not a panel |
| D-10 | 2026-11-07 | Rationale language: Russian (`RATIONALE_LANGUAGE`); quotes always in the original language | The jury and checklist are Russian; quotes must match the transcript |

| D-18 | 2026-11-07 | Real DEV data (`invoke.json`, `data/fixtures/DEV.json`) is NOT committed: it contains 5 real calls. Teammates load it via `DATASET_API_URL` (auto-cached in `data/raw/`) | The repo must be public; customer speech should not be published |
| D-19 | 2026-11-07 | Real-model batch runs from the CLI need `--yes` and print a cost estimate | No accidental spending; the cache makes re-runs free |

| D-20 | 2026-11-07 | Docker Compose: one `app` service (API + UI), named volume for SQLite/logs/cache, `test` profile runs pytest in the image | Same run on every laptop and the jury's machine; state survives restarts |

| D-21 | 2026-11-07 | Red team on real DEV chats is the cheap correctness check until gold labels exist; run it after every prompt or checklist change | 15/15 passed for $0.095; it showed the judge catching what both M1 and M2 missed |
| D-23 | 2026-11-07 | ~~SQLite~~ → **PostgreSQL 16** is the only runtime DB (psycopg 3 + small pool, `DATABASE_URL`; `db` service in Compose). JSON and timestamps stay TEXT. Old SQLite files move over once with `cli migrate-sqlite`. Supersedes the SQLite part of D-01/D-15/D-20 | Prod and dev keep real review work (appeals, gold labels, decisions): a server database with `pg_dump` backups and concurrent writers is safer than a file in a volume; TEXT columns kept the code change small |

## Open (owner decides by the given hour)

| ID | Question | Owner | By | Notes |
|---|---|---|---|---|
| D-03 | Which OpenAI models for M1/M2/M3? **Measured so far:** 4.1-mini×2 + 4.1 = $0.023/dialog, ~15 s, M1=M2 82.5% on DEV, 0 failures; 5-mini×2 + 5 = $0.058, ~90 s (1 dialog), more unanimous. Default stays on 4.1 until gold accuracy decides. | B | 4h | Bake-off on the 15 KZ chats + 5 RU + 5 EN against the gold labels. Check what the key has access to. Cheaper model for M1/M2, strongest for M3 is the starting point. Record the models + $/dialog here and in README. |
| D-11 | `JUDGE_MODE` always vs disputed | B | 12h | Compare agreement with gold vs cost. **Red team says keep `always`:** in 2/15 cases M1 and M2 agreed on the wrong answer (c03 N/A) and only the judge was right; `disputed` would have skipped the judge there. |
| D-22 | Sharpen c03 N/A: "N/A only if the operator disclosed NO client-specific data (balance, operations, account state) and performed no protected action" | B | 8h | M1/M2 over-apply N/A (red team + 56.9% agreement on DEV). Change via the checklist editor (v2), re-run only c03, compare. |
| D-12 | `TRUST_THRESHOLD` final value | D | 16h | Pick the value that keeps agreement ≥ 90% on auto-accepted items; report the auto-accept share |
| D-13 | Audio offset for calls: does the recording start at the first transcript timestamp? | A | 4h | Listen to 1 DEV call; if not, add an offset |
| ~~D-14~~ | ~~Do identity-verification system messages exist in DEV?~~ **Yes**, in 3 languages: «Личность клиента подтверждена.», «Клиенттің жеке басы расталды.», «Customer identity verified.» (only 4 dialogs). Other events: escalation to operator, operator/agent closed chat, client left / connection lost, hold on/off, recording paused. These feed c03, c07 N/A and c06 waits. | A | done | Found 2026-11-07 in `data/fixtures/DEV.json` (copy of `invoke.json`). DEV has 0 messages with IIN/card/phone patterns; masking still runs for the control set. |

## Pitch only (not building)

Appeal-driven prompt evolution (GEPA), CQA as an MCP server, audio-model signals (±15 s windows),
contact-reason taxonomy and heatmap, cost cascade beyond `JUDGE_MODE`, PCF control.
