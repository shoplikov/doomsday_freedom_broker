"""One-shot copy of a pre-Postgres SQLite file into PostgreSQL:  python -m cqa.cli migrate-sqlite [PATH] [--replace]

Copies every table row for row (ids kept), renames gold_labels.by -> labelled_by, moves the identity sequences
past max(id), then compares row counts. Everything runs in one Postgres transaction: any error leaves the target
untouched. Delete this module once every server has migrated."""
import sqlite3
from pathlib import Path

from . import db

RENAMED = {"gold_labels": {"by": "labelled_by"}}


def migrate(path: Path, replace: bool = False) -> dict[str, tuple[int, int]]:
    """Returns {table: (source rows, target rows)}. Raises SystemExit with a message on any refusal or mismatch."""
    if not path.is_file():
        raise SystemExit(f"SQLite file not found: {path}")
    src = sqlite3.connect(path)
    try:
        src_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        db.init()
        counts = {}
        with db.tx() as con:
            if con.execute("SELECT count(*) AS n FROM conversations").fetchone()["n"] and not replace:
                raise SystemExit("target database already has conversations; rerun with --replace to wipe it first")
            # The target is empty or being replaced: drop what the app wrote at startup (the seed checklist).
            con.execute(f"TRUNCATE {', '.join(db.TABLES)} RESTART IDENTITY")
            for table in db.TABLES:
                if table not in src_tables:
                    counts[table] = (0, 0)
                    continue
                cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                target_cols = [RENAMED.get(table, {}).get(c, c) for c in cols]
                with con.cursor().copy(f"COPY {table} ({', '.join(target_cols)}) FROM STDIN") as copy:
                    for r in src.execute(f"SELECT {', '.join(cols)} FROM {table}"):
                        copy.write_row(r)
                n_src = src.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                counts[table] = (n_src, con.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"])
            for table in db.IDENTITY_TABLES:  # next id = max(id) + 1, so new rows never collide with copied ones
                con.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                            f" (SELECT coalesce(max(id), 0) + 1 FROM {table}), false)")
            bad = {t: c for t, c in counts.items() if c[0] != c[1]}
            if bad:
                raise SystemExit(f"row count mismatch, nothing was written: {bad}")
    finally:
        src.close()
    return counts
