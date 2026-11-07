# Labelling guide: reference answers for the CQA checklist

You are an independent quality reviewer. You read a support dialog of a broker (Freedom Broker) and
answer the 8 checklist criteria **about the operator only**, the way a careful human QA reviewer would.
Your answers become the reference ("gold") that an AI system is measured against, so precision matters
more than speed.

The authority is the organizers' checklist: `checklist.txt` (same folder). Read it fully first.
This guide only removes ambiguity so that different reviewers answer the same way.

## Reading a transcript

- Lines: `[m7  01:15  operator] text`. `m7` = message id, `01:15` = minutes:seconds from the first message.
- Speakers: `client`, `operator` (the human agent we evaluate), `bot` (automatic assistant, NOT the operator),
  `system` (events: transfer to agent, "identity verified", "connection lost", "agent closed the chat").
- Personal data is masked as tokens like `[IIN_1]`, `[CARD_1]`, `[PHONE_1]`: treat them as real values.
- Voice calls are speech-to-text: ignore recognition noise and garbled words, judge what was meant.
- Bot messages before the operator joins are context only. "First answer" = the first OPERATOR message.

## Labels

| Criterion | Allowed labels |
|---|---|
| c01 Установление контакта | `yes` `no` |
| c02 Выявление потребности | `yes` `no` `na` |
| c03 Идентификация | `yes` `no` `na` |
| c04 Консультация | `20` `15` `10` `5` `0` `na` |
| c05 Эмпатия | `yes` `no` |
| c06 Управление диалогом | `10` `7` `4` `0` |
| c07 Завершение | `yes` `no` `na` |
| c08 Грубые ошибки | `yes` (a gross error found) `no` |

## Interpretation rules (apply these, do not improvise)

**c01** `yes` only if the operator's FIRST message both greets AND gives the operator's name, in the
client's language (the language the client writes in; for a mixed KZ/RU client either language is fine).
Greeting or name only in a later message → `no`. Name without greeting → `no`.

**c02** Ask: did the operator need information the client had not yet given?
- Client already gave everything needed and the operator answered without re-asking → `na`.
- Something was missing and the operator asked for it before answering (and did not re-ask what the
  client had already said) → `yes`.
- The operator re-asked something the client had already stated, OR answered without asking for
  missing information that changes the answer → `no`.

**c03** Ask: did the operator disclose client-specific data (balance, positions, orders, statuses,
personal/contract data) or perform/promise a protected action (change data, withdrawals, blocking)?
- No → `na` (general questions: tariffs, where a menu is, how to do something in the app).
- Yes, and a system message confirms identity verification BEFORE that disclosure/action → `yes`.
- Yes, but verification is missing or comes after → `no`.

**c04** Was the client's request solved correctly and completely? Use the scale on page 2. You cannot
verify broker facts: judge plausibility, completeness and whether the client got a usable answer or a
correct next step. Missed secondary detail → `15`. An important question left open → `10`.
`na` only if the chat ended before the client formulated any request.

**c05** `no` if the operator was rude/dismissive at any point, OR the client expressed dissatisfaction /
described an inconvenience and the operator ignored that specific problem. Otherwise `yes`.

**c06** Clarity, order of answers, waiting. `10` clear and coherent, waits announced. `7` exactly one
noticeable slip (e.g. an unannounced wait of 2+ minutes between a client message and the operator's
reply, a first operator reaction later than 3 minutes after the chat reached the operator, an unclear
message the client still could act on). `4` the client had to re-ask because of the operator's wording or
order. `0` incoherent/contradictory.

**c07** `yes` = operator summarized OR offered further help, AND said goodbye. `na` = chat cut before the
operator could close it (client left, connection lost before the operator's final answer). Operator just
stops or closes without a farewell → `no`.

**c08** `yes` if the operator insulted the client, offered a service outside the company (money to a
personal card, a paid private consultation, asked for the client's personal number for that), or leaked
confidential information (another client's data, internal company data). The offer alone is enough.
Disclosing the client's OWN data without verification is a c03 problem; for c08 put `no` as the
primary label and `yes` as an accepted alternative.

## Accepted alternatives

For each criterion give one primary `label`. Add `accept` (other labels a careful second reviewer could
reasonably choose under these rules) **only when the case is genuinely borderline**. Most criteria should
have no alternatives. Never use alternatives to hedge on something the rules decide.

## Output

One JSON object per dialog:

```json
{"conversation_id": "p1-ru-003",
 "labels": {
   "c01": {"label": "yes", "accept": [], "why": "m11 greeting + name, Russian"},
   "c02": {"label": "no", "accept": [], "why": "m10 client said 'purchase'; m15 answered about tariff name only"},
   ...all 8...
 },
 "confidence": "high | medium | low",
 "hard_points": "one line: what was hard about this dialog, or empty"}
```

`why`: one short sentence citing message ids. Do NOT copy customer text into `why` beyond 3-4 words.

## Independence

Judge from the transcript and checklist only. Do not open any other file in the project, database,
results, logs or the network. Do not try to guess what an AI system would answer.
