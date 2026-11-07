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
