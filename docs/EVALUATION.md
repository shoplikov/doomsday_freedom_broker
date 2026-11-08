# Evaluation: how we know a pipeline change helped

We have no human experts and the organizers give no reference answers. So we build reference answers
we can trust, and measure every approach, prompt and checklist change against them with one command.

```bash
uv run python -m cqa.cli bench --split tune --out results/bench/<name>.tune.json --note "what changed"
uv run python -m cqa.cli bench-compare results/bench/p1.tune.json results/bench/<name>.tune.json
uv run python -m cqa.cli bench-show results/bench/<name>.tune.json --fails --criterion c07
```

Bench runs call the real model (no cache) and write traces to whatever `DATABASE_URL` points at: use a
throwaway database, not prod.

## 1. Three sources of known answers

| Suite | What | Why we trust the answer |
|---|---|---|
| `gold` | All 65 DEV dialogs labelled **blind** (labellers saw only the transcript and the checklist, never an AI verdict) by 5 independent reviewers, 12 dialogs labelled twice | Two reviewers agreed on **95.8 %** of answers exactly and on **100 %** within accepted alternatives |
| `synthetic` | Realistic dialogs written to a scenario in RU / KZ / EN, each built around a hard-but-decidable trap (name only in the 2nd message, Kazakh without special letters, verification after disclosure, polite side deal on WhatsApp...) | The answer follows from how the dialog was written; each one re-read by a second reviewer |
| `mutations` | A gold or synthetic dialog broken on purpose: greeting removed, side deal or insult inserted, data leaked before verification, client disconnected, 5 silent minutes, harmless typos | The target criterion MUST flip; a harmless edit must change nothing |

Labels and scenarios live in `data/eval/` (`gold_dev.json`, `scenarios/*.json`). The labelling rules are
`data/eval/LABELING_GUIDE.md`: the organizers' checklist plus decisions on its ambiguous points. A label
can carry `accept` alternatives where a careful second reviewer could reasonably differ; a prediction is
correct if it is the label or an accepted alternative.

## 2. TUNE and HOLDOUT

Every item is in TUNE or HOLDOUT (alternating within each language and channel, so both halves are
balanced). Mutations inherit the split of the dialog they were made from.

- Develop on TUNE: read the misses (`bench-show --fails`), change one thing, re-run, compare.
- Run HOLDOUT only at milestones and look only at the numbers. A gain on TUNE that does not appear on
  HOLDOUT is overfitting: revert it.
- When TUNE is exhausted (no informative misses left), write NEW scenarios for the weak spots instead of
  re-tuning on the same ones.

## 3. Metrics

| Metric | Meaning | Gate / goal |
|---|---|---|
| macro accuracy | mean of per-criterion accuracy (rare criteria count equally) | main score |
| c08 recall | gross errors caught | **100 %, hard gate** |
| c08 false alarm | gross error flagged where there is none | low |
| N/A precision / recall (c02 c03 c04 c07) | the known weak spot | both high |
| total MAE | points off the reference total (what operators see) | low |
| auto-accept coverage / accuracy | share accepted without a human, and how often those are right | accuracy ≥ 95 % |
| harmless flip % | verdicts that change after harmless edits | ≈ 0 |
| repeat flip % (`--repeat 2`) | verdicts that change on an identical re-run | ≈ 0 |
| by language | KZ must not lag RU | small gap |
| role accuracy | inspector / advocate / judge alone | panel ≥ best role |
| p50 s/dialog, tokens, $ | cost and speed | full DEV ≤ 10 min |

`bench-compare` also counts cells **fixed** and **broken** between two runs and gives a sign test p-value.
With ~500 cells, a difference of a few cells is noise (p ≥ 0.05): do not keep a change on noise.

## 4. Picking an approach

1. Gate: c08 recall 100 % on TUNE and HOLDOUT, no crashed dialogs, within time and cost.
2. Rank by HOLDOUT macro accuracy, paired against the current main on the same cells.
3. Ties: lower total MAE, better auto-accept accuracy, lower cost.

An approach is any function registered in `bench.APPROACHES` with the signature of
`pipeline.evaluate_criteria`. The current three-model panel is `panel`.

## 5. Results (Qwen `qwen3.8-27b`, non-thinking, 3-role panel)

### 5.1 What each step changed (TUNE, 112 items, 566 cells)

| Version | What changed | macro % | c02 | c03 | c06 | c07 | c08 recall | MAE | harmless flip | auto-accept acc. |
|---|---|---|---|---|---|---|---|---|---|---|
| p1 | baseline: server-default sampling, checklist v1 | 81.7 | 56.7 | 64.9 | 80.0 | 82.4 | 92.3 | 12.18 | 15.3 | 82.9 |
| p4 | + temperature 0, explicit N/A decision (`na_applies`), quote snapping, facts (first reaction counted from the transfer, waits, identity events), checklist v2 | 85.4 | 56.2 | 97.1 | 80.0 | 88.2 | 93.3 | 11.94 | 0.0 | 87.3 |
| p5 | + rationale written before decisions, "lowest matching step" rule for scales, checklist v3 | 91.4 | 85.9 | 94.3 | 82.9 | 90.8 | 90.0 | 6.60 | 0.0 | 92.8 |
| p6 | + safety flag when the model itself names a violation, empty-answer resampling, checklist v4 (c08 split into 5 concrete atoms) | 93.3 | 87.5 | 95.7 | 87.1 | 94.7 | 96.7 | 6.92 | 0.0 | 94.8 |
| p7 | + risk hints by kind (side deal, insult, another person's data, internal info; RU/KZ/EN), checklist v5 (c06 = three equal checks, c04 = every client question) | 95.1 | 89.1 | 98.6 | 88.6 | 98.7 | 100.0 | 7.95 | 0.0 | 95.4 |

What mattered most, in order: (1) **rationale before decisions** in the JSON (a non-thinking model decides
while it writes; asking for the verdict first made it decide before reading); (2) **N/A as an explicit,
normal answer** with its own field (c02 N/A recall 15 % → 82 %); (3) **temperature 0** (harmless flip 15 % → 0);
(4) **concrete atoms** instead of one abstract question (c08).

### 5.2 Does it hold on data we did not tune on?

HOLDOUT (109 items, 542 cells) was run only at milestones, numbers only. The fresh set (24 new dialogs
written after p6 was frozen: 10 RU chats, 2 RU calls, 6 KZ, 6 EN, verified by a blind second labeller)
was run once per final candidate.

| | HOLDOUT p1 | HOLDOUT p6 | HOLDOUT p7 | fresh p1 | fresh p6 | fresh p7 |
|---|---|---|---|---|---|---|
| macro % | 81.7 | 90.6 | **92.5** | 75.0 | 90.6 | **91.7** |
| c08 recall % | 100 | 100 | 100 | 50 | 50 | **100** |
| c08 false alarm % | 1.9 | 3.7 | 3.7 | 0 | 0 | 0 |
| total MAE | 9.83 | 8.72 | 7.68 | 11.02 | 6.64 | 4.28 |
| auto-accept accuracy % | 82.4 | 91.0 | 93.1 | 77.8 | 90.6 | 92.1 |
| harmless flip % | 16.7 | 6.2 | 2.1 | — | — | — |
| fixed / broken cells vs p1 (sign test) | — | +65 / −19 (p≈0) | +70 / −14 (p≈0) | — | +34 / −4 (p≈0) | +35 / −3 (p≈0) |

**Verdict: p7 + checklist v5 is the shipped configuration** (`PROMPT_VERSION = "p7"`,
`data/checklist_clarified.json` = `data/eval/checklist_v5.json`). Against p6 on HOLDOUT it fixes 16 cells and
breaks 6 (c04 75.4 → 82.0, c06 79.1 → 83.6, c07 91.8 → 97.3, harmless flip 6.2 → 2.1 %); on its own that
difference is borderline (p = 0.05), but it points the same way on TUNE (+21 / −10) and fresh (+5 / −3), and it
closes the only gross-error miss. The gain from p1 holds out of sample: TUNE +13.4, HOLDOUT +10.8, fresh +16.7
macro points, so it is not memorised from TUNE.

### 5.3 Honest notes

- **We peeked once.** p6 missed one gross error on the fresh set (`fresh-kz-002`: the operator tells the
  client about the withdrawal of the client's wife, in Kazakh). We read that miss and added "another person's data"
  risk hints in RU/KZ/EN (p7). The fresh c08 number for p7 is therefore not fully blind; the TUNE and
  HOLDOUT c08 numbers are.
- **A wording can overfit.** Checklist v4 described c06 mostly through timings; TUNE c06 went up but
  HOLDOUT c06 fell 86.6 → 79.1. v5 gives c06 three equal checks (clarity, no repeated questions, pace).
- **The inference server can return junk.** One prompt kept getting `{}` / cut-off JSON at temperature 0:
  a corrupted prefix cache on the shared SGLang server. Fix in `pipeline.call_role`: if the answer has no
  items, resample once with a fresh first line (`Request <nonce>.`) at temperature 0.7.
- **Still weak:**
  - c02 on the fresh set (66.7 %): the model still says "no" where nothing needed clarifying (N/A recall 56 %);
  - c04 (did the operator answer *every* question): 82 % on HOLDOUT;
  - c08 false alarms: 2 clean HOLDOUT dialogs flagged, both a curt or dismissive reply read as an insult (same
    2 as p6; the new risk hints added none).

  Every one of these goes to the review queue when the three roles disagree, so a human sees it before it counts.
- **Speed:** p7 takes ~150 s per dialog on the shared Qwen server. The
  10 control dialogs run in parallel well inside the hour.

### 5.4 Same pipeline, other models (OpenAI API)

The p7 prompts and checklist v5 unchanged, only the model behind all three roles swapped (11-08, HOLDOUT 109 items
and the fresh set 24 items, judge always called). Prices are OpenAI list prices per 1M tokens (input / output).

| Model (reasoning effort) | $/1M in / out | HOLDOUT macro | fresh macro | c08 recall | HOLDOUT MAE | p50 s/dialog | $ / dialog |
|---|---|---|---|---|---|---|---|
| Qwen `qwen3.8-27b` (p7, shared server) | own GPU | 92.5 | 91.7 | 100 / 100 | 7.68 | 148 | 0 |
| **gpt-6.1-sol (low)** | 2 / 10 | **94.6** | **96.4** | 100 / 100 | **6.14** | 17 | 0.04 |
| gpt-6.1-terra (low) | 1 / 5 | 93.2 | 95.3 | 100 / 100 | 7.61 | 23 | 0.02 |
| gpt-6.1-terra (none) | 1 / 5 | 92.8 | 93.8 | 100 / 100 | 7.28 | 25 | 0.02 |
| gpt-6-luna (none) | 0.10 / 0.50 | 91.4 | 92.2 | 100 / 100 | 9.32 | 14 | 0.002 |
| gpt-4.1-mini | 0.40 / 1.60 | 91.1 | 88.5 | **95.5** / 100 | 6.80 | 15 | 0.008 |
| gpt-5.4-mini (none) | 0.75 / 4.50 | 88.3 | 88.0 | 100 / 100 | 8.55 | 9 | 0.016 |

- gpt-6.1-sol vs Qwen p7, paired cells: HOLDOUT +28 / −17, fresh +12 / −3 (together +40 / −20, sign test p ≈ 0.01).
  Biggest gains: c04 82 → 93 (Qwen said "fully resolved" when part of the questions were left open), c06 84 → 88.
- With a strong model the panel adds little: sol inspector alone 95.0 on HOLDOUT, judge needed in 12 of 109
  dialogs when it is called only on a dispute (`JUDGE_MODE=disputed`, same accuracy, about a third fewer calls).
- The errors of gpt-6.1-terra are different from Qwen's (24 cells fixed, 22 broken on HOLDOUT): c04 better, c02 worse.
  Its c02 misses are one pattern (the operator re-asked a known detail, the model said N/A instead of "no"). A c02
  wording that says so was tried on TUNE and did not help (97.2 → 96.3), so it was dropped.
- gpt-4.1-mini missed a gross error on HOLDOUT; gpt-5.4-mini is fast but the weakest. Both are out.
- Reasoning models reject `temperature` unless `reasoning_effort` is `none`; with `low` the harmless-edit flip rate
  stayed 0 %. gpt-6.1-sol does not accept `none`.
- Data: personal data is masked before any call (`cqa/privacy.py`), but transcripts do leave our server.

### 5.5 Approach 2: precedents (`CQA_APPROACH=precedents`, `cqa/precedents.py`)

With a strong model the remaining misses are rarely a misread dialog; they are the house reading of a rule (re-asking a
known detail is "no", not N/A; where c06 step 7 ends). Rewording the checklist per miss overfits (§5.3, v4 c06; a c02
v6 wording did not help either). Approach 2 shows every role the 4 most similar cases already decided by a reviewer
(answer, reason, cited messages), with at least one case of each nearby answer so the model sees where the line is.
Similarity: `BAAI/bge-m3` / OpenAI embeddings, character trigrams without them. On the bench the pool is TUNE only.

Kimi-K2.6 (non-thinking, via an OpenAI-compatible proxy, judge on disputes) and Qwen p7:

| | HOLDOUT macro | fresh macro | c02 HOLDOUT / fresh | c08 recall / false alarm (HOLDOUT) |
|---|---|---|---|---|
| Kimi-K2.6 panel | 92.7 | 94.3 | 82 / 75 | 100 / 0 |
| **Kimi-K2.6 precedents** | **93.5** | **95.3** | **88 / 79** | 100 / 0 |
| Qwen p7 panel | 92.5 | 91.7 | 92 / 67 | 100 / 3.7 |
| Qwen p7 precedents (trigrams) | — | 92.2 | — / 75 | — |

Paired, Kimi precedents vs panel: HOLDOUT +12 / −8, fresh +6 / −4. Same direction on all three measurements but not
yet significant (p ≈ 0.4). It costs one embedding call and ~1.5k prompt tokens per call. A reviewer's decision can
become a new precedent (not wired yet).

### 5.6 Final choice: gpt-6.1-terra (low) + precedents, judge on disputes

Same p7 prompts and checklist v5, precedent pool = TUNE only, `JUDGE_MODE=disputed` (the production setting):

| Model + precedents | HOLDOUT macro | fresh macro | c02 fresh | c08 recall / false alarm (HOLDOUT) | total MAE (HOLDOUT / fresh) | p50 s | $ / dialog |
|---|---|---|---|---|---|---|---|
| gpt-6-luna (none) | 93.3 | 91.1 | 66.7 | 100 / 0 | 9.08 / 8.30 | 7.5–11 | 0.002 |
| **gpt-6.1-terra (low)** | **93.2** | **96.9** | **91.7** | 100 / 3.7 | **7.07 / 2.22** | 13–18 | **0.023** |
| gpt-6.1-sol (low, judge always) | — | 96.9 | 91.7 | — | — / 2.92 | 22 | 0.074 |

- terra vs luna, paired: HOLDOUT +17 / −18 (a tie), fresh +14 / −3 (p ≈ 0.013). luna's misses on fresh are mostly c02.
- terra matches sol on fresh at a third of the price, so terra is the production model.
- Production run on 11-08 (prod server, 65 DEV + 10 control dialogs): 75 dialogs, $1.69 ($0.023 per dialog), 0 failed.
