"""Terminal loop for the team:  uv run python -m cqa.cli {import,run,report,trace,export,migrate-sqlite} ...

  import DEV                 fetch + normalize + store a group
  run DEV [--limit 10]       evaluate the group with the active checklist (parallel, uses the cache)
  report [--version N]       panel analytics: A/B agreement, judge sides, gold accuracy per role, cost
  trace CONVERSATION_ID      every LLM call of one dialog: role, try, latency, tokens, validation errors
  export DEV [--prod]        organisers' results/{dev,prod}/results.json (format 1.0)
  migrate-sqlite [PATH]      one-shot copy of the old SQLite file into Postgres (--replace wipes the target)
"""
import argparse
import asyncio
import json
from pathlib import Path

from . import analytics, checklist, config, db, evaluations, importer, redteam, results, sqlite_migrate, traces


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


def migrate_sqlite(path: Path, replace: bool) -> None:
    counts = sqlite_migrate.migrate(path, replace)
    print(_table([[t, a, b, "ok" if a == b else "MISMATCH"] for t, (a, b) in counts.items()],
                 ["table", "sqlite", "postgres", ""]))
    print(f"migrated {path} -> Postgres")


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
    elif args.cmd == "redteam":
        run_redteam(args.group, args.per_lang, args.yes, args.only)
    else:
        trace(args.conversation_id)


if __name__ == "__main__":
    main()
