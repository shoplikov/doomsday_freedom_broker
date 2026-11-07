# Architecture

> The AI proposes verdicts with evidence. Plain code checks and counts. A human has the final word.

## 1. Pipeline for one dialog

```
 Organizers' API ──GET ?group_id──▶ importer ──▶ normalize ──▶ privacy mask ──▶ SQLite
                                     (server-side,  m1…mN, MM:SS / ISO   [IIN_1] [CARD_1]
                                      raw JSON cached)  → seconds

 evaluate(conversation, checklist vN)
   1. signals.compute       CODE   first reply, gaps, language mix, system events, risk words
   2. cache lookup          CODE   per criterion: hash(transcript + criterion text + models + prompt version)
   3. M1 Inspector ┐        LLM    strict: every atom needs clear proof
      M2 Advocate  ┘ parallel      operator-friendly: strongest HONEST evidence
   4. validate + retry      CODE   quote ⊂ cited message? atoms complete? N/A allowed? step valid?
   5. M3 Judge              LLM    reads both briefs, re-checks cited messages, rules per atom
   6. validate + retry      CODE
   7. derive verdict        CODE   atoms → verdict via rule all/any (code wins over the model)
   8. confidence + route    CODE   share of roles agreeing; ≥ threshold → auto-accept, else review queue
   9. score                 CODE   weights, N/A redistribution, half-up 2 dp, gross flag separate
  10. store                 CODE   evaluation row (AI original = final until a human decides), audit
```

Only steps 3 and 5 call a model (`cqa/llm.py`). Everything else is unit-testable Python.

## 2. The three-model panel

Our take on the "jury" idea with one provider (OpenAI). Instead of two identical judges we use two
**opposed roles**, so their agreement means something even when the models are similar.

| Role | Model env | Job | Sees |
|---|---|---|---|
| **M1 Inspector** | `MODEL_INSPECTOR` | Applies each criterion strictly; "no clear proof → false + checked_range" | facts, transcript, criteria |
| **M2 Advocate** | `MODEL_ADVOCATE` | Finds the strongest honest evidence for the operator or for N/A; never invents | same |
| **M3 Judge** | `MODEL_JUDGE` (strongest) | Re-checks every message the briefs cite, decides each atom from the transcript; ambiguous → stricter reading | same + both briefs |

**Why it is reliable**

- A strict reader and a lenient reader that **agree** = a robust verdict, so `confidence = 1.0`.
- They disagree → the judge rules, confidence drops to 0.67 (judge sided with one of them) or lower.
- A human sees everything below `TRUST_THRESHOLD`, plus every item where validation failed twice.
- The judge's briefs are shown on the card ("AI second opinion" for the reviewer).
- `JUDGE_MODE=disputed` skips the judge when M1 and M2 already agree (saves about ⅓ of the cost).

**Cost:** 2–3 calls per dialog with all 8 criteria in one call. Shared prompt prefix
(system + facts + transcript first, role and criteria last), so the provider's prompt cache is reused
across roles and incremental re-runs.

**Failure handling:** SDK retries 429/5xx 4 times. Invalid JSON or failed validation triggers one
re-ask with the exact errors, for the failing criteria only. If the judge still fails, the strict
(inspector) reading is used and the item goes to review. A dialog that crashes never stops a batch.

## 3. Checklist model

From the organizers' PDF (`data/checklist_v1.json`):

| ID | Criterion | Type | Weight | N/A |
|---|---|---|---|---|
| c01 | Установление контакта | binary (3 atoms, all) | 10 | no |
| c02 | Выявление потребности | binary (2 atoms) | 15 | yes |
| c03 | Идентификация | binary (1 atom) | 20 | yes |
| c04 | Корректная и полная консультация | scale 0/5/10/15/20 | 20 | yes |
| c05 | Эмпатия | binary (2 atoms) | 15 | no |
| c06 | Управление диалогом | scale 0/4/7/10 | 15 | no |
| c07 | Завершение | binary (2 atoms) | 5 | yes |
| c08 | Грубые ошибки | flag (3 atoms, any) | — | no |

**Atoms** split each yes/no criterion into 2–5 concrete questions (CheckEval). The rule `all`/`any`
turns them into a verdict in code. Scale criteria pick one anchored step.

Versioning: saving creates vN+1, and old evaluations keep their version. The cache key uses the
criterion **text** fingerprint and leaves out weight and section. So after a criterion swap only that
criterion reaches the LLM, and a weight change re-scores with no LLM call at all.

## 4. Scoring (checklist page 2)

```
points(c)  = weight × (1 if yes else 0)            binary
           = weight × step / top_step               scale
N/A        → criterion leaves both sums
total      = Σ points / Σ applicable weights × 100, Decimal ROUND_HALF_UP to 0.01, once
no applicable weight or a missing result → total not calculated
flag (c08) → gross_error = true, never changes points
```

Per-criterion points and section results are shown **after** redistribution (e.g. 20 → 23.53 when 15 is N/A).
Reference numbers in `tests/test_scoring.py`: 76.47 → 94.12 after appeal, 88.125 → 88.13.

## 5. Human loop

```
AI evaluated ──▶ auto_accepted ──(operator appeals)──▶ submitted ──▶ upheld | changed
             └─▶ needs_review (low confidence / invalid) ──(reviewer decides)──▶ reviewed
```

- A decision always needs a reason. The same scoring code recalculates the total.
- Each criterion keeps `ai` (original), `decision` (who, reason, before → after) and `final`.
- `total_ai` and `total` are both stored, and the card shows "ИИ: X → итог: Y".

## 6. Data model (SQLite, JSON in TEXT columns)

```
conversations 1─n messages          (mid, speaker, ts_raw, t_sec, text, text_masked, lang)
conversations 1─n evaluations ─n─1 checklists(version)
evaluations   1─n appeals
audit         (who, when, entity, action, before, after, reason)
cache         (key → criterion result)
```

Per-criterion results live inside `evaluations.result_json` (see CONTRACTS.md). That is simpler than
a verdicts table and is enough for 65 + control dialogs.

## 7. UI and Dynamics 365

- Vanilla JS Web Components, no build step, served by FastAPI from `web/`.
- `web/api.js` is the only place that calls `fetch`. To embed the UI elsewhere, set `window.CQA_API_BASE`.
- D365 HTML web resource: the record ID arrives as `?data=<conversation_id>` and opens that card.
  All AI keys and data stay server-side. Next step after the hackathon: a PCF control.

## 8. Observability: logs, traces, analytics

| What | Where |
|---|---|
| One log line per LLM call (role, try, model, ms, tokens, answered n/m), per validation failure, per evaluation | `data/logs/cqa.log` + console |
| Every LLM call: exact prompt messages, raw response, error, validation errors, tokens, latency | `traces` table · `GET /api/traces/{id}` · «Трасса» → «вызов #id» |
| Per criterion: each role's full answer, votes, source of the final answer, trace ids | `result_json.criteria[].roles / votes / source / trace_ids` |
| Panel quality: M1=M2 agreement, judge sides, accuracy per role vs gold, by language, retries, cost | `GET /api/analytics` · page «Аналитика» · `python -m cqa.cli report` |
| Human reference answers (gold) | `gold_labels` table · «эталон» selector on each criterion |

How to use these to accept or reject the panel design: [VALIDATION.md](VALIDATION.md).

## 9. Reliability checklist

| Risk | Mitigation |
|---|---|
| Hallucinated evidence | quote-in-message validator, retry, human review |
| Sloppy model formats | `pipeline.coerce` fixes harmless slips ("true", "N/A", "M3", "m3-m7", ```json fences); meaning-changing errors go back to the model |
| One model's blind spot | opposed roles + judge; confidence routing |
| Provider outage at T+23h | SDK retries; failed dialogs are listed per run and can be re-run; cache keeps finished work |
| Organizers' API down | raw JSON cached in `data/raw/`, auto fallback |
| Wrong math | Decimal half-up, unit tests with reference numbers |
| PII leak to the LLM | masking on import; LLM sees `text_masked` only |
| Prompt injection inside a transcript | "transcript is data" system rule; verdicts must still pass the validator |
| Kazakh quality | judge in the original language; KZ bake-off (DECISIONS D-03); KZ filter on calibration |
