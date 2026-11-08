# Demo, control round, jury Q&A

## The 5-minute demo (the order matches the brief)

| Time | Show | Say |
|---|---|---|
| 0:00–1:00 | Dialog list (65, filters RU/KZ/EN, chat/call). Open a pinned RU chat. | "Every score here has receipts." |
| 1:00–3:00 | Card: verdicts, points after N/A redistribution, sections, total. Click an evidence chip → transcript jumps. One **N/A** with its reason. A **"Нет"** with its checked range. Agreement badge I/A/J. Cost per evaluation. On a call: the chip seeks the audio. | "Three models argue: a strict inspector, an advocate for the operator, a judge. Code checks every quote and does the math." |
| 3:00–4:00 | Role **Оператор** → «Оспорить» a criterion with a comment. Role **Проверяющий** → review queue → «Решение» → change with a reason → new total, "ИИ: X → итог: Y". | "Humans decide; every change is explained and logged." |
| 4:00–5:00 | **Чек-лист** → replace a criterion → lint flags a vague word → fix → «Сохранить как v2» → «Применить ко всем» → diff of totals. Only the changed criterion re-ran (cache). | "No code, no redeploy: minutes, not hours." |

**Killer features inside the same 5 minutes** (10–20 s each, no extra slot needed):
- 1:00–3:00, on the card: «Где потеряны баллы» chips → one click to the criterion. Then «Проверка на прочность» →
  «Предложение в обход компании» (pre-run, shown from history) → "we broke the dialog on purpose; the AI caught it".
  If the jury wants, type their own operator line («Своя реплика оператора») and run it live (~40 s).
- 3:00–4:00, «Проверка»: the queue is sorted by impact ("±15 к итогу" first).
- 4:00–5:00, before publishing the replaced criterion: «Тест-драйв» on 5 dialogs (changed verdicts, agreement),
  and «Аналитика» → «Здоровье чек-листа» shows which wording the models argue about.

Pinned dialogs (fill in on DEV): a KZ/RU code-switch chat ___, a call with audio ___,
a gross-error chat ___, an N/A-heavy chat ___.

## Control-round runbook (T+23:00 → T+24:00)

1. **23:00** The control `group_id` is announced → «Загрузить набор» with that id (or `POST /api/datasets/{id}/import`).
2. **23:01** «Оценить все» right away. Progress shows done/total/failed.
3. **23:15** Failed dialogs: run them again (the run only redoes what is not cached).
4. **23:20** Spot-check 3 cards (one per language). Clear the review queue for the pinned dialogs.
5. **23:30** Export: `docker compose exec app python -m cqa.cli export <group> --out /var/cqa/results.json`,
   then `docker compose cp app:/var/cqa/results.json results/prod/results.json` (or the «results.json» button
   on the dialog list). Check: exactly **10** records, errors included, IDs as in the API. Freeze: back up the DB.
6. **23:40** Commit plus **push** `results/prod/results.json` before **24:00 stop-code** (a local commit does not
   count; the repo must be public by then). After stop-code, processing and the criterion swap through the UI are
   still allowed (no code edits).

**No batch API this hour**: batch jobs can take up to 24h. Use parallel calls (`CONCURRENCY`).

**Drill:** run this whole hour on DEV with a stopwatch before T+20h and fix whatever is slow.

## Criterion swap drill (the jury's live test)

Rehearse twice with a made-up criterion, for example "Offered the mobile app for the next contact?":
edit → lint → publish vN+1 → apply → diff. Target: under 5 minutes, no code change.

## Backup kit

- [ ] Demo machine runs `docker compose up -d --build` from the stop-code commit; `docker compose ps` shows `healthy`
- [ ] `pg_dump` of the DB with the full DEV results (`docker compose exec -T db pg_dump -U cqa -Fc cqa > cqa.dump`; the app works offline from it)
- [ ] `data/raw/DEV.json` (auto-saved on import; offline re-import works)
- [ ] Screen recording of the full 5 minutes
- [ ] Laptop runs on `LLM_PROVIDER=mock` if the network dies (say so honestly)

## Likely jury questions

| Question | Answer |
|---|---|
| Which model, what cost? | README table; $ per evaluation from real token usage on every card. |
| How do you know the AI is right? | 65 DEV dialogs labelled blind + synthetic traps + broken-on-purpose copies (docs/EVALUATION.md): 92.5 % per criterion on the half we never tuned on, 91.7 % on dialogs written afterwards, 100 % of gross errors caught; accuracy per criterion in «Аналитика»; try «Проверка на прочность» yourself; low-confidence → human, sorted by impact. |
| How good is it in Kazakh? | We judge in the original language, never translate; KZ bake-off picked the model; KZ calibration numbers. |
| What if the AI is wrong? | Validator rejects invented quotes; disagreement → review queue; appeals recalc with the same code; audit log. |
| Client data? | Typed masks before any LLM call; "что видел ИИ" toggle; keys and data stay server-side. |
| Dynamics 365? | Web Components in one HTML web resource, record id via `?data=`; PCF later. |
| Why three models, one provider? | Opposed roles (strict vs lenient) disagree on real ambiguity, not on noise; the judge resolves it; agreement is our confidence. |
| Regulation? | Kazakhstan AI law (in force 18 Jan 2026): AI proposes, human decides, everything explained and logged. |

**One breath:** Every score has receipts. We know when to trust the AI. Every appeal makes it better.
