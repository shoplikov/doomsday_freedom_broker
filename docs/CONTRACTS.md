# Contracts

The shapes every lane codes against. **Frozen after hour 1.** To change a contract, update this file
in the same PR and announce it in the team chat.

## 1. Organizers' dataset (input)

```json
{ "error": "", "isSuccess": true, "data": {
  "dataset_group_id": "DEV", "total_records": 65,
  "records": [{ "conversation_id": "p1-ru-001", "channel": "agent chat" | "voice call",
                "language": "RU" | "KZ" | "EN",
                "transcript": [{ "timestamp": "00:02" | "2026-05-03T10:00:05+05:00",
                                 "speaker": "client" | "operator" | "bot" | "system", "text": "…" }],
                "audio_url": null | "https://…" }] } }
```

`DATASET_API_URL` uses a `{group_id}` placeholder. Offline: `data/fixtures/<group>.json` (same shape).

## 2. Checklist (config)

```json
{ "name": "…", "sections": [{ "id": "start", "title": "…" }],
  "criteria": [{
    "id": "c01", "section": "start", "title": "Установление контакта",
    "type": "binary" | "scale" | "flag", "weight": 10,
    "na_allowed": false, "na_condition": "",
    "definition": "Да — …",
    "rule": "all" | "any",                              // binary/flag: atoms → verdict
    "atoms": [{ "id": "greeting", "q": "Поздоровался ли…?" }],   // binary/flag
    "scale": [{ "step": 20, "label": "…" }]               // scale
  }] }
```

`checklist.lint` errors block saving: missing id, type, atoms or steps; N/A allowed without
a condition; duplicate ids. Warnings are allowed: weights ≠ 100, vague words, unknown section.

## 3. LLM role output (inspector / advocate / judge, same schema)

```json
{ "criteria": [{
  "id": "c07",
  "atoms": { "summary_or_offer": false, "farewell": false },
  "verdict": "yes" | "no" | "na" | "scored",
  "step": null | 15,
  "evidence": [{ "msg": "m5", "quote": "exact substring" }],
  "checked_range": ["m5", "m7"] | null,
  "na_reason": null | "…",
  "rationale": "1–2 sentences (Russian)"
}] }
```

Validator rules (`pipeline.validate`): every quote must be in the cited message (case, whitespace and
ё/е are normalized; `…` splits a quote into parts); atoms must be complete; N/A only if allowed and
with a reason; the step must be one of the steps; a positive answer needs evidence; a binary "no"
needs evidence or a checked_range.

## 4. Stored criterion result (inside `evaluations.result_json.criteria[]`)

```json
{ "criterion_id": "c05", "title": "…", "section": "flow", "type": "binary", "weight": 15,
  "na_allowed": false, "steps": [],
  "ai":    { "verdict": "no", "step": null },        // AI original, never changes
  "final": { "verdict": "yes", "step": null },       // after human decision
  "decision": { "action": "change", "by": "reviewer", "reason": "…", "at": "…",
                "before": {…}, "after": {…}, "appeal_id": 7 } | null,
  "atoms": {…}, "evidence": [...], "checked_range": [...], "na_reason": null, "rationale": "…",
  "votes": { "inspector": "no", "advocate": "yes", "judge": "no" },   // labels: yes|no|na|step
  "source": "judge" | "agreement" | "inspector" | "advocate" | null,
  "roles": { "inspector": {…answer…}, "advocate": {…}, "judge": {…} },  // each role's validated answer
  "trace_ids": { "inspector": [31], "advocate": [32, 40], "judge": [146] },
  "briefs": { "inspector": "rationale…", "advocate": "rationale…" },
  "confidence": 0.67, "status": "auto_accepted" | "needs_review" | "reviewed" | "failed",
  "errors": { "judge": ["quote not found …"] }, "cached": false }
```

`result_json` also holds `score_ai` and `score` (the output of `scoring.score`: total, total_note,
applicable_weight, gross_error, criteria{points, max_points}, sections[]) and `facts`.

## 5. REST API

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | provider, models, judge_mode, threshold, dataset_api |
| POST | `/api/datasets/{group_id}/import` | | `{imported}` |
| GET | `/api/conversations` | `language, channel, group_id` | list + latest total/status |
| GET | `/api/conversations/{id}` | | messages, signals, evaluations[], latest (full) |
| POST | `/api/evaluations` | `{conversation_id, checklist_version?}` | evaluation (full) |
| GET | `/api/evaluations/{id}` | | evaluation (full, with appeals) |
| POST | `/api/runs` | `{group_id?, conversation_ids?, checklist_version?}` | run `{id, status, done, total, failed[]}` |
| GET | `/api/runs/{id}` | | run progress |
| POST | `/api/appeals` | `{evaluation_id, criterion_id, comment}` | appeal (state `submitted`) |
| GET | `/api/appeals` | `state` | appeals |
| POST | `/api/appeals/{id}/decision` | `{action: uphold\|change, verdict?, step?, reason}` | evaluation |
| POST | `/api/evaluations/{id}/review` | same + `criterion_id` | evaluation (low-confidence items) |
| GET | `/api/review-queue` | | `{appeals[], low_confidence[]}` |
| GET | `/api/checklists` · `/active` · `/{version}` | | versions / checklist |
| POST | `/api/checklists/lint` | `{checklist}` | `{errors, warnings}` |
| POST | `/api/checklists` | `{checklist, note}` | `{version, warnings}` |
| GET | `/api/checklists/diff` | `from_v, to_v` | per-dialog totals + delta |
| GET | `/api/audit` | `limit` | audit lines |
| GET | `/api/traces` | `conversation_id` | LLM calls of a dialog (no prompt bodies) |
| GET | `/api/traces/{id}` | | one call: request messages, raw response, error, validation, tokens, latency |
| GET | `/api/gold` | `conversation_id?` | gold labels |
| POST | `/api/gold` | `{conversation_id, criterion_id, label, note}` (empty label deletes) | label |
| GET | `/api/analytics` | `checklist_version?` | panel metrics (see VALIDATION.md) |

Header `X-Role: operator | reviewer` identifies the actor (there is no login, as the brief requires).
Errors: 404 not found, 409 already decided, 422 validation (`detail` = message or list).

## 6. UI components (`web/components/`)

| Tag | Owner lane | Does |
|---|---|---|
| `<cqa-conversation-list>` | C | import, batch run, filters, open card |
| `<cqa-dialog-view conversation-id>` | C | transcript + audio, evaluation card, evidence jump, appeal form, decision form |
| `<cqa-review-queue>` | C | open appeals + low-confidence items |
| `<cqa-checklist-editor>` | C (+B for lint UX) | edit/add/replace criteria, lint, publish, apply, diff |
| `<cqa-analytics>` | C + D | panel metrics, accuracy vs gold, disagreements, gold misses |
