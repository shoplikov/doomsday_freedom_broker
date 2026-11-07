# Proving the three-model panel works

The claim: **two independent models (M1 strict, M2 lenient) plus a judge (M3) are more accurate than one
model, and their agreement tells us when to trust the AI.** Here is how we measure it, not assume it.

## 1. What is recorded for every criterion of every dialog

| Field | Meaning |
|---|---|
| `votes.inspector`, `votes.advocate` | M1 and M2 answers. They run **in parallel** and never see each other (tested). |
| `votes.judge` | M3 answer after reading both briefs and the transcript |
| `roles.*` | each role's full answer: atoms, evidence quotes, checked range, rationale |
| `source` | where the final came from: `judge` · `agreement` (judge skipped) · `inspector` (judge failed) |
| `confidence` | share of the roles agreeing with the final answer (1.0 / 0.67 / 0.33) |
| `errors.*` | validator complaints per role (invented quote, missing atom, illegal N/A…) |
| `trace_ids.*` | ids of every LLM call: the exact prompt, the raw response, latency, tokens, retry number |

You can see it all in the UI: dialog card → «Трасса» on any criterion → «вызов #id» shows the prompt and the raw answer.
In the terminal: `uv run python -m cqa.cli trace <conversation_id>`. Logs go to `data/logs/cqa.log`
(one line per call, validation failure and evaluation).

## 2. Gold set (human reference answers)

1. Pick 20 DEV dialogs: at least 5 KZ, 5 EN, the 5 calls, and dialogs with N/A cases and a gross error.
2. Open each card and set **«эталон»** for every criterion. Do this **before** reading the AI verdict if you can,
   and read the transcript, not the rationale.
3. Two people label the same 5 dialogs first. Where they disagree, the criterion wording is ambiguous: fix the
   checklist, not the labels.
4. A gold label is tied to the criterion's wording (fingerprint). If the wording changes, the old label stops counting.

## 3. The numbers (page «Аналитика», or `uv run python -m cqa.cli report`)

| Metric | What it tells you | Good sign |
|---|---|---|
| **Accuracy: M1 / M2 / M3 / panel** vs gold | Does the panel beat a single model? | panel > best single role |
| **M1 = M2 %** per criterion | How often two opposed readers agree | high on clear criteria (c01, c08), lower on c02/c04/c05 |
| **Judge sided both / M1 / M2 / neither** | Is the judge just copying one side? | spread between M1 and M2; "neither" rare |
| **Accuracy when M1 = M2** vs **when they split** | Is agreement a valid confidence signal? | agreed items ≥ 95% correct |
| **needs_review share** | Human workload | 10–30% |
| **validation issues / retries** | Prompt and format quality | near 0 after prompt iteration |
| **by language** | Kazakh quality | KZ accuracy close to RU |
| **$ per dialog** | Cost (the brief requires it) | write it in README |

## 4. Decision rules

- **Panel accuracy ≤ best single role** on ≥ 100 gold labels → simplify: `JUDGE_MODE=disputed`, or one model plus review.
- **Agreed items are not ≥ 95% correct** → agreement is not a trust signal yet: raise `TRUST_THRESHOLD` to 1.0.
- **One criterion is far below the others** → look at «Промахи относительно эталона», open the traces,
  fix the atoms or definition in the checklist editor (new version), re-run. Only that criterion re-runs.
- **The judge sides with one role > 90% of the time** → the other role adds nothing: change its prompt or drop it.

## 5. The loop with a real key

```bash
# .env: LLM_PROVIDER=openai, OPENAI_API_KEY=..., MODEL_INSPECTOR/ADVOCATE/JUDGE=...
uv run python -m cqa.cli import DEV
uv run python -m cqa.cli run DEV --limit 10     # first a small batch: look at cost and validation issues
uv run python -m cqa.cli report
uv run python -m cqa.cli trace p1-kz-001        # read what each model actually said
# fix prompts (bump PROMPT_VERSION) or criteria (new checklist version), re-run, compare reports
uv run python -m cqa.cli run DEV                # full DEV
```

## 5a. First real run on DEV (2026-11-07, gpt-4.1-mini ×2 + gpt-4.1)

65/65 evaluated, 0 failed, $1.50. M1=M2 agreement 82.5% overall (EN 83.3 · KZ 82.5 · RU 82.1): c05 and c08 100%,
c01 96.9%, c04 87.7%, c06 86.2%, c07 76.9%, **c02 55.4% and c03 56.9%**. The disagreement sits where the checklist
itself is ambiguous: N/A vs "Нет" for "needs clarification" and "identification required". These two criteria are
the first candidates for sharper atoms/N/A wording, and the first to label in the gold set.
All 9 validator rejections were real model errors (a quote with altered words or not in the cited message), not false alarms.
5 gross errors flagged (RU ×3, EN, KZ, e.g. a paid private consultation offered in Kazakh).

## 5b. Red team on real DEV dialogs (known answers without labelling)

DEV has no reference answers, so `cqa/redteam.py` creates them: it breaks a real chat on purpose and checks that
the verdict flips. Only the affected criteria are evaluated, so it is cheap.

| Mutation | Expected |
|---|---|
| `drop_greeting`: first operator reply becomes a neutral sentence | c01 = no |
| `side_deal`: operator offers to invest the client's money via a personal card | c08 = yes |
| `insult`: operator insults the client | c08 = yes, c05 = no |
| `drop_farewell`: last operator message becomes "Хорошо." | c07 = no (or na) |
| `leak_before_verification`: operator discloses balance with no identity check (only dialogs without verification) | c03 = no |
| `harmless`: client message lowercased, final punctuation removed | every verdict unchanged vs the baseline evaluation |

```bash
uv run python -m cqa.cli redteam DEV                                 # prints the cost estimate
uv run python -m cqa.cli redteam DEV --per-lang 1 --yes              # 1 chat per language, all mutations (~$0.26)
uv run python -m cqa.cli redteam DEV --only side_deal insult --yes   # a subset
```

**First run (2026-11-07, gpt-4.1-mini ×2 + gpt-4.1, p1-ru-001 / p1-kz-001 / p1-en-001, 5 targeted mutations): 15/15 passed, $0.095.**

| Role | Caught the defect |
|---|---|
| M1 Inspector | 88.9% |
| M2 Advocate | 66.7% |
| **M3 Judge = panel** | **100%** |

The panel beat both single models on known answers. In the KZ and RU `leak_before_verification` cases **both M1 and M2
said N/A** and only the judge answered "no"; with 1 of 3 votes these items went to human review, which is the safe outcome.
This matches the low M1=M2 agreement on c03 in the full DEV run: **c03's N/A condition is too easy to over-apply**
(see DECISIONS D-21). `harmless` (LLM noise) has not been run yet (+$0.08).

## 6. What the automated tests already guarantee (no key needed)

`uv run pytest -q` runs the OpenAI code path with a scripted fake model that does what real models do:

- unanimous answer → auto-accepted · split → judge decides, confidence 0.67 · judge against both → review
- M1 and M2 never see each other; the judge sees both briefs; all roles share one cached prompt prefix
- invented quote → re-asked with the exact error → fixed · broken JSON twice → strict reading + human review
- API outage of one role or all roles → no crash, `failed` / review, traces keep the error
- sloppy formats (`"true"`, `"N/A"`, `"M2"`, `"m2-m3"`, code fences) accepted; meaning-changing errors rejected
- atoms beat the model's own verdict; a flag atom = gross error; illegal N/A or step rejected
- cost = token usage × prices; re-run hits the cache (0 calls, $0)
- `uv run --with playwright pytest tests/e2e -q`: the whole demo in a real browser, no JS errors

They do **not** prove that real models judge well. Only the gold set does (sections 2–4).
