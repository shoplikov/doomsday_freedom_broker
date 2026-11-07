"""Terminal loop for the team:  uv run python -m cqa.cli {import,run,report,trace,export,migrate-sqlite} ...

  import DEV                 fetch + normalize + store a group
  run DEV [--limit 10]       evaluate the group with the active checklist (parallel, uses the cache)
  report [--version N]       panel analytics: A/B agreement, judge sides, gold accuracy per role, cost
  trace CONVERSATION_ID      every LLM call of one dialog: role, try, latency, tokens, validation errors
  export DEV [--prod]        organisers' results/{dev,prod}/results.json (format 1.0)
  migrate-sqlite [PATH]      one-shot copy of the old SQLite file into Postgres (--replace wipes the target)
  bench [--split tune]       score the pipeline on known answers (data/eval): accuracy, c08 recall, N/A, stability
  bench-compare A B          what changed between two bench runs: metrics, fixed/broken cells, sign test
  bench-show RUN [--fails]   the cells of a bench run with rationale, to see WHY it missed
"""
import argparse
import asyncio
import json
from pathlib import Path

from . import analytics, bench, checklist, config, db, evaluations, importer, redteam, results, sqlite_migrate, traces


def _table(rows: list[list], header: list[str]) -> str:
    rows = [[("—" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    line = lambda r: "  ".join(v.ljust(w) for v, w in zip(r, widths))  # noqa: E731
    return "\n".join([line(header), line(["-" * w for w in widths])] + [line(r) for r in rows])


def report(version: int | None) -> None:
    a = analytics.compute(version)
    print(f"checklist v{a['checklist_version']} · dialogs {a['dialogs']} · status {a['status']} · "
          f"avg total {a['avg_total']} · gross errors {a['gross_errors']} · cost ${a['cost_usd']} "
          f"(avg ${a['avg_cost_usd']}/dialog)")
    print(f"M1/M2 independent agreement: {a['ab_agreement_pct']}% · gold labels: {a['gold']['labels']} · "
          f"accuracy % {a['gold']['accuracy_pct']}")
    g = a["gold"]
    print(f"panel accuracy when M1=M2: {g['accuracy_when_m1_eq_m2_pct']}% (n={g['agreed_n']}) · "
          f"when they split: {g['accuracy_when_split_pct']}% (n={g['split_n']})\n")
    print(_table([[c["criterion_id"], c["title"][:28], c["n"], c["ab_agreement_pct"],
                   "/".join(str(c["judge_sided"][k]) for k in ("both", "inspector", "advocate", "neither")),
                   c["avg_confidence"], c["needs_review"], c["failed"], c["validation_issues"], c["gold_n"],
                   c["accuracy_pct"]["inspector"], c["accuracy_pct"]["advocate"], c["accuracy_pct"]["panel"],
                   json.dumps(c["final_dist"], ensure_ascii=False)] for c in a["criteria"]],
                 ["id", "title", "n", "A=B%", "judge both/A/B/none", "conf", "review", "fail", "valid!",
                  "gold", "accA%", "accB%", "panel%", "final answers"]))
    print("\nLLM calls by role:")
    print(_table([[r["role"], r["calls"], r["retries"], r["errors"], round(r["avg_ms"] or 0), r["max_ms"],
                   r["tokens_in"], r["tokens_out"]] for r in a["calls"]],
                 ["role", "calls", "retries", "errors", "avg ms", "max ms", "tokens in", "tokens out"]))
    print("\nBy language:", json.dumps(a["by_language"], ensure_ascii=False))
    if a["gold_misses"]:
        print(f"\nGold misses ({len(a['gold_misses'])}):")
        for m in a["gold_misses"][:30]:
            print(f"  {m['conversation_id']} {m['criterion_id']} gold={m['gold']} final={m['final']} votes={m['votes']}")


def trace(conversation_id: str) -> None:
    rows = traces.list_for(conversation_id)
    print(_table([[t["id"], t["role"], t["attempt"], t["model"], t["latency_ms"], t["tokens_in"], t["tokens_out"],
                   t["error"] or "", t["validation"] or ""] for t in rows],
                 ["trace", "role", "try", "model", "ms", "in", "out", "error", "validation errors"]))
    print("\nFull prompt/response: GET /api/traces/<id>  or the «Трасса» button on the dialog card.")


EST_USD_PER_DIALOG = 0.025  # measured on DEV: gpt-4.1-mini x2 + gpt-4.1, 8 criteria, ~$0.023


async def run(group: str, limit: int | None, version: int | None, yes: bool = False) -> None:
    ids = [r["id"] for r in db.rows("SELECT id FROM conversations WHERE group_id = %s ORDER BY id", group)][:limit]
    if not ids:
        raise SystemExit(f"no dialogs in group {group}: run `import {group}` first")
    if config.LLM_PROVIDER != "mock" and not yes:
        # ponytail: estimate ignores the cache, so it is an upper bound; already-evaluated criteria cost $0.
        raise SystemExit(f"{len(ids)} dialogs with real models ({config.MODELS}): up to ~${len(ids) * EST_USD_PER_DIALOG:.2f}. "
                         "Cached criteria are free. Add --yes to run, or --limit N to try a few first.")
    results = await asyncio.gather(*(evaluations.evaluate(i, version) for i in ids), return_exceptions=True)
    failed = [(i, r) for i, r in zip(ids, results) if isinstance(r, Exception)]
    print(f"evaluated {len(ids) - len(failed)}/{len(ids)}" + (f", failed: {failed}" if failed else ""))


def run_redteam(group: str, per_lang: int, yes: bool, only: list[str] | None = None) -> None:
    cases = redteam.plan(group, per_lang, only)
    if not cases:
        raise SystemExit(f"no suitable chats in group {group}: import it first")
    if config.LLM_PROVIDER != "mock" and not yes:
        est = sum(0.025 if c["mutation"] == "harmless" else 0.012 for c in cases)
        raise SystemExit(f"{len(cases)} red-team cases with real models: ~${est:.2f}. Add --yes to run.")
    results = asyncio.run(redteam.run(cases))
    for r in results:
        if "checks" not in r:
            continue
        for c in r["checks"]:
            if r["mutation"] != "harmless" or not c["ok"]:
                print(f"{'OK ' if c['ok'] else 'FAIL'} {r['conversation_id']:<10} {r['mutation']:<25} {c['criterion']} "
                      f"expected={'/'.join(c['expected'])} final={c['final']} votes={c['votes']}"
                      + ("" if c["ok"] else f"\n     why: {c['rationale']}"))
    s = redteam.summarize(results)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    path = config.LOG_DIR / f"redteam-{db.now().replace(':', '')}.json"
    path.write_text(json.dumps({"summary": s, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"full report: {path}")


def run_bench(args) -> None:
    items = bench.build(args.suites, args.split, args.per_mutation)
    if args.only:
        items = [i for i in items if any(o in i["id"] for o in args.only)]
    items = items[: args.limit] if args.limit else items
    cl = bench.load_checklist(args.checklist)
    print(f"bench: {len(items)} dialogs ({', '.join(f'{k} {v}' for k, v in _count(items, 'source').items())}), "
          f"split {args.split}, approach {args.approach}, provider {config.LLM_PROVIDER} {config.MODELS}, "
          f"prompt {config.PROMPT_VERSION}, repeat {args.repeat}")
    tag = args.out.stem if args.out else "adhoc"
    out = args.out or config.ROOT / "results" / "bench" / f"{args.approach}-{config.PROMPT_VERSION}.{args.split}.json"
    parts = [out.with_name(f"{out.stem}.{i}.partial.jsonl") for i in range(args.repeat)]
    runs = [asyncio.run(bench.run(items, cl, args.approach, f"{tag}:{i}", parts[i])) for i in range(args.repeat)]
    meta = {"approach": args.approach, "split": args.split, "suites": args.suites, "provider": config.LLM_PROVIDER,
            "models": config.MODELS, "prompt_version": config.PROMPT_VERSION, "judge_mode": config.JUDGE_MODE,
            "checklist": str(args.checklist or "data/checklist_v1.json"), "created_at": db.now(), "note": args.note}
    doc = bench.save(out, meta, runs)
    if not any(r.get("error") for run in runs for r in run):  # keep the checkpoint so a re-run retries failures
        for p in parts:
            p.unlink(missing_ok=True)
    print_metrics(doc["metrics"])
    print(f"\nsaved {out}")


def _count(items, key):
    out = {}
    for i in items:
        out[i[key]] = out.get(i[key], 0) + 1
    return out


def print_metrics(m: dict, other: dict | None = None) -> None:
    def row(name, a, b=None):
        if other is None:
            return [name, a]
        d = None if a is None or b is None else round(b - a, 1)
        return [name, a, b, ("+" if d and d > 0 else "") + str(d) if d is not None else None]
    o = other or {}
    g = lambda d, *ks: _dig(d, ks)  # noqa: E731
    rows = [row("macro accuracy %", m["macro_acc"], o.get("macro_acc")),
            row("micro accuracy %", m["micro_acc"], o.get("micro_acc")),
            row("c08 recall % (gate 100)", m["c08_recall"], o.get("c08_recall")),
            row("c08 false alarm %", m["c08_false_alarm"], o.get("c08_false_alarm")),
            row("total MAE (points)", m["total_mae"], o.get("total_mae")),
            row("auto-accept coverage %", g(m, "auto_accept", "coverage"), g(o, "auto_accept", "coverage")),
            row("auto-accept accuracy %", g(m, "auto_accept", "acc"), g(o, "auto_accept", "acc")),
            row("harmless flip %", m["harmless_flip_rate"], o.get("harmless_flip_rate")),
            row("repeat flip %", m.get("repeat_flip_rate"), o.get("repeat_flip_rate")),
            row("validation issues", g(m, "ops", "validation_issues"), g(o, "ops", "validation_issues")),
            row("failed cells", g(m, "ops", "failed_cells"), g(o, "ops", "failed_cells")),
            row("p50 s/dialog", g(m, "ops", "p50_seconds"), g(o, "ops", "p50_seconds"))]
    for cid, x in m["by_criterion"].items():
        rows.append(row(f"{cid} accuracy % (n {x['n']})", x["acc"], g(o, "by_criterion", cid, "acc")))
    for cid, x in m["na"].items():
        rows.append(row(f"{cid} N/A precision / recall", f"{x['precision']} / {x['recall']}",
                        None if not o else f"{g(o, 'na', cid, 'precision')} / {g(o, 'na', cid, 'recall')}") if other is None
                    else [f"{cid} N/A precision / recall", f"{x['precision']} / {x['recall']}",
                          f"{g(o, 'na', cid, 'precision')} / {g(o, 'na', cid, 'recall')}", None])
    for k in ("by_source", "by_language"):
        for name, x in m[k].items():
            rows.append(row(f"{k[3:]} {name} macro % ({x['items']})", x["macro_acc"], g(o, k, name, "macro_acc")))
    for name, x in m["by_mutation"].items():
        rows.append(row(f"mutation {name} pass % ({x['n']})", x["pass"], g(o, "by_mutation", name, "pass")))
    rows.append(row("role accuracy % " + "/".join(m["role_acc"]), "/".join(str(v) for v in m["role_acc"].values()),
                    "/".join(str(v) for v in (o.get("role_acc") or {}).values()) if other else None) if other is None
                else ["role accuracy % " + "/".join(m["role_acc"]), "/".join(str(v) for v in m["role_acc"].values()),
                      "/".join(str(v) for v in (o.get("role_acc") or {}).values()), None])
    print(_table(rows, ["metric", "value"] if other is None else ["metric", "A", "B", "B-A"]))
    if m["c08_missed"]:
        print("c08 missed:", ", ".join(m["c08_missed"]))
    if m["ops"]["item_errors"]:
        print("item errors:", ", ".join(m["ops"]["item_errors"]))


def _dig(d, ks):
    for k in ks:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def bench_compare(a: Path, b: Path) -> None:
    da, dbb = (json.loads(p.read_text(encoding="utf-8")) for p in (a, b))
    print(f"A {a.name}: {da['meta'].get('prompt_version')} {da['meta'].get('note') or ''}\n"
          f"B {b.name}: {dbb['meta'].get('prompt_version')} {dbb['meta'].get('note') or ''}\n")
    print_metrics(da["metrics"], dbb["metrics"])
    c = bench.compare(da, dbb)
    print(f"\ncells in both: {c['common_cells']} · fixed {len(c['fixed'])} · broken {len(c['broken'])} · "
          f"sign-test p={c['p']} ({'real change' if c['p'] < 0.05 else 'could be noise'})")
    for name in ("fixed", "broken"):
        if c[name]:
            print(f"{name}: " + ", ".join(f"{i}/{cid}" for i, cid in c[name]))


def bench_show(path: Path, fails: bool, criterion: str | None, source: str | None) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for r in doc["runs"][0]:
        if source and r["source"] != source:
            continue
        for cid, x in r["criteria"].items():
            if x["ok"] is None or (fails and x["ok"]) or (criterion and cid != criterion):
                continue
            mark = "ok " if x["ok"] else "MISS"
            print(f"{mark} {r['id']} {cid} expect={'|'.join(x['expect'])} final={x['final']} votes={x['votes']} "
                  f"conf={x['confidence']}\n     {x['rationale']}"
                  + (f"\n     na_reason: {x['na_reason']}" if x.get("na_reason") else "")
                  + (f"\n     errors: {x['errors']}" if x.get("errors") else ""))


def migrate_sqlite(path: Path, replace: bool) -> None:
    counts = sqlite_migrate.migrate(path, replace)
    print(_table([[t, a, b, "ok" if a == b else "MISMATCH"] for t, (a, b) in counts.items()],
                 ["table", "sqlite", "postgres", ""]))
    print(f"migrated {path} -> Postgres")


def gold_import(path: Path) -> None:
    """Reference labels made blind (see data/eval/LABELING_GUIDE.md) -> the «Эталон» of every dialog card, so
    «Аналитика» shows real accuracy. Run after the final checklist is active: a label counts only while its
    criterion's wording is unchanged."""
    db.init()
    done, skipped = 0, []
    for g in json.loads(path.read_text(encoding="utf-8")):
        for cid, v in g["labels"].items():
            try:
                evaluations.set_gold(g["conversation_id"], cid, bench.norm_label(v["label"]), "blind-review",
                                     (v.get("why") or "")[:200])
                done += 1
            except (LookupError, ValueError) as exc:
                skipped.append(f"{g['conversation_id']}/{cid}: {exc}")
    print(f"gold labels set: {done}, skipped: {len(skipped)}")
    for s in skipped[:10]:
        print("  ", s)


def main() -> None:
    config.setup_logging()
    db.init()
    checklist.ensure_seed()
    p = argparse.ArgumentParser(prog="cqa")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import").add_argument("group")
    r = sub.add_parser("run")
    r.add_argument("group")
    r.add_argument("--limit", type=int)
    r.add_argument("--version", type=int)
    r.add_argument("--yes", action="store_true", help="confirm spending on real models")
    sub.add_parser("report").add_argument("--version", type=int)
    sub.add_parser("trace").add_argument("conversation_id")
    rt = sub.add_parser("redteam", help="break real dialogs on purpose and check the verdicts flip")
    rt.add_argument("group")
    rt.add_argument("--per-lang", type=int, default=1, help="chats per language (RU/KZ/EN)")
    rt.add_argument("--yes", action="store_true", help="confirm spending on real models")
    rt.add_argument("--only", nargs="+", choices=list(redteam.MUTATIONS), help="run only these mutations")
    ex = sub.add_parser("export", help="write the organisers' results.json (format 1.0) for a group")
    ex.add_argument("group")
    ex.add_argument("--out", type=Path, help="default: results/dev/results.json, or results/prod/... with --prod")
    ex.add_argument("--prod", action="store_true", help="control set: write to results/prod/")
    ex.add_argument("--team", help="team_name (default: CQA_TEAM_NAME)")
    mg = sub.add_parser("migrate-sqlite", help="copy the old SQLite database into Postgres (one-shot)")
    mg.add_argument("path", nargs="?", type=Path, default=config.SQLITE_PATH,
                    help=f"SQLite file (default: CQA_DB_PATH = {config.SQLITE_PATH})")
    mg.add_argument("--replace", action="store_true", help="wipe a target that already has conversations")
    b = sub.add_parser("bench", help="score the pipeline on dialogs with known answers (data/eval)")
    b.add_argument("--split", choices=["tune", "holdout", "fresh", "all"], default="tune")
    b.add_argument("--suites", nargs="+", default=["gold", "synthetic", "mutations"],
                   choices=["gold", "synthetic", "mutations"])
    b.add_argument("--approach", default="panel", choices=list(bench.APPROACHES))
    b.add_argument("--checklist", type=Path, help="checklist JSON file (default data/checklist_v1.json)")
    b.add_argument("--per-mutation", type=int, default=6, help="dialogs per mutation and split")
    b.add_argument("--repeat", type=int, default=1, help="run twice to measure stability")
    b.add_argument("--limit", type=int)
    b.add_argument("--only", nargs="+", help="only items whose id contains one of these")
    b.add_argument("--out", type=Path)
    b.add_argument("--note", default="", help="what changed in this run")
    bc = sub.add_parser("bench-compare", help="compare two bench runs")
    bc.add_argument("a", type=Path)
    bc.add_argument("b", type=Path)
    bm = sub.add_parser("bench-merge", help="combine bench runs of the same configuration")
    bm.add_argument("out", type=Path)
    bm.add_argument("paths", type=Path, nargs="+")
    gi = sub.add_parser("gold-import", help="load blind reference labels (data/eval/gold_dev.json) into gold_labels")
    gi.add_argument("path", type=Path, nargs="?", default=None)
    bs = sub.add_parser("bench-show", help="cells of a bench run with the model's rationale")
    bs.add_argument("path", type=Path)
    bs.add_argument("--fails", action="store_true")
    bs.add_argument("--criterion")
    bs.add_argument("--source", choices=["gold", "synthetic", "mutation"])
    args = p.parse_args()
    if args.cmd == "import":
        print("imported", asyncio.run(importer.import_group(args.group)))
    elif args.cmd == "run":
        asyncio.run(run(args.group, args.limit, args.version, args.yes))
    elif args.cmd == "report":
        report(args.version)
    elif args.cmd == "export":
        out = args.out or config.ROOT / "results" / ("prod" if args.prod else "dev") / "results.json"
        doc = results.write(args.group, out, args.team)
        bad = [r["conversation_id"] for r in doc["results"] if r["status"] != "ok"]
        print(f"{out}: {len(doc['results'])} dialogs, {len(bad)} errors{' ' + ', '.join(bad) if bad else ''}; model {doc['model']}")
    elif args.cmd == "migrate-sqlite":
        migrate_sqlite(args.path, args.replace)
    elif args.cmd == "bench":
        run_bench(args)
    elif args.cmd == "bench-compare":
        bench_compare(args.a, args.b)
    elif args.cmd == "bench-merge":
        print_metrics(bench.merge(args.paths, args.out)["metrics"])
    elif args.cmd == "gold-import":
        gold_import(args.path or config.DATA_DIR / "eval" / "gold_dev.json")
    elif args.cmd == "bench-show":
        bench_show(args.path, args.fails, args.criterion, args.source)
    elif args.cmd == "redteam":
        run_redteam(args.group, args.per_lang, args.yes, args.only)
    else:
        trace(args.conversation_id)


if __name__ == "__main__":
    main()
