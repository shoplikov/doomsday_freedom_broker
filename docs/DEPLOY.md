# Deploy, environments and releases

Every push to `main` or `dev` is tested, built and deployed to one AWS server by GitHub Actions
([`.github/workflows/ci-cd.yml`](../.github/workflows/ci-cd.yml)). A deploy of `main` also creates a GitHub Release.

| Branch | Environment | URL | Server folder | Release |
|---|---|---|---|---|
| `main` | prod | http://13.218.176.204/ | `/opt/doomsday_freedom_broker` | yes, `vYYYY.MM.DD.<run>` |
| `dev` | dev | http://13.218.176.204:8080/ | `/opt/doomsday_freedom_broker-dev` | no |

Each environment is its own Compose project with its own `app` and `db` (PostgreSQL 16) containers, image, `.env` and volumes, so testing on dev never touches prod data.

## Flow

```mermaid
flowchart LR
    F["feature branch"] -->|PR| D["dev"]
    D -->|PR| M["main"]
    D --> T1["tests + docker build"]
    M --> T2["tests + docker build"]
    T1 --> DD["deploy dev<br/><small>:8080</small>"]
    T2 --> DP["deploy prod<br/><small>:80</small>"]
    DP ==> R["GitHub Release<br/><small>vYYYY.MM.DD.N</small>"]

    classDef env fill:#4f46e5,stroke:#312e81,color:#fff
    class DD,DP env
```

Pull requests into `dev` or `main` run only the tests and the Docker build; nothing is deployed until the merge.

### What one deploy does

```mermaid
sequenceDiagram
    autonumber
    participant R as GitHub repo
    participant GH as Actions runner
    participant S as AWS server
    R->>GH: push to main or dev
    Note over GH: pytest + docker build
    GH->>S: rsync code to the env folder
    Note over S: .env and data kept
    GH->>S: docker compose up -d --build
    loop up to 60 s
        GH->>S: GET /api/health
    end
    alt healthy, branch main
        GH->>R: tag + Release with notes
    else not healthy
        GH-->>R: run fails, logs in the run
    end
```

The running version is written to `RELEASE` in the env folder (`version sha time`).

## Releases and rollback

- **Release:** merge `dev` into `main`. After the prod deploy is healthy, the workflow creates the tag and a Release with auto-generated notes (commits and PRs since the last release). No manual tagging.
- **Rollback:** GitHub → **Actions** → **CI/CD** → **Run workflow**, branch `main`, `ref` = an older release tag (e.g. `v2026.11.07.12`). It runs the tests, deploys that exact code to prod, and does not create a new release.
- **Redeploy** the current head (e.g. after editing the server `.env`): same button, leave `ref` empty.

> [!NOTE]
> Rollback changes the code only. The database stays as it is; restore it separately if needed (below).

## Server

`ssh ubuntu@13.218.176.204`. Docker + Compose, Docker logs capped at 3 × 10 MB per container, 4 GB swap.

```text
/opt/doomsday_freedom_broker/        # prod (main), port 80
├── .env                             # settings and API keys, server only, mode 600
├── RELEASE                          # deployed version
└── …                                # code, replaced on every deploy
/opt/doomsday_freedom_broker-dev/    # dev, port 8080, same layout
~/db-backups/                        # manual DB backups
```

| Volume | Holds |
|---|---|
| `doomsday_freedom_broker_pgdata` | prod PostgreSQL data (`db` service; not published to the host) |
| `doomsday_freedom_broker_cqa-data` | prod logs, cached API responses, the old pre-Postgres `cqa.sqlite3` (`/var/cqa` in `app`) |
| `doomsday_freedom_broker-dev_pgdata`, `…-dev_cqa-data` | the same for dev |

Images: `cqa-app:prod` and `cqa-app:dev`, so a dev build never replaces the prod image.

### Settings (`.env`)

Created once from [`.env.example`](../.env.example); deploys never overwrite or delete it. Besides the app settings it holds `CQA_PORT` (80 / 8080), `CQA_IMAGE` (`cqa-app:prod` / `cqa-app:dev`) and `POSTGRES_PASSWORD` for that environment. Compose builds the app's `DATABASE_URL` from `POSTGRES_PASSWORD`; a `DATABASE_URL` line in `.env` is ignored in Docker. Postgres keeps the password it was first started with, so changing `POSTGRES_PASSWORD` later also needs `ALTER USER cqa PASSWORD '…'` in `psql`. After editing, restart that environment:

```bash
cd /opt/doomsday_freedom_broker      # or …-dev
nano .env
docker compose up -d --force-recreate
```

### Database backup and restore

```bash
cd /opt/doomsday_freedom_broker      # or …-dev
mkdir -p ~/db-backups
docker compose exec -T db pg_dump -U cqa --clean --if-exists cqa | gzip > ~/db-backups/prod-$(date +%Y%m%d-%H%M).sql.gz   # backup

docker compose stop app                                        # restore (replaces every table)
gunzip -c ~/db-backups/<file>.sql.gz | docker compose exec -T db psql -U cqa -d cqa -v ON_ERROR_STOP=1 -q >/dev/null
docker compose start app

docker compose exec db psql -U cqa cqa                         # SQL shell
```

`docker compose down -v` deletes the `pgdata` volume, i.e. the database. Plain `down` / `up` keep it.

### Migrating from SQLite (once per environment)

Releases before the move to Postgres kept everything in `/var/cqa/cqa.sqlite3` inside the `cqa-data` volume. The
first Postgres deploy starts an empty `db`; the app creates the tables and seeds checklist v1. The SQLite file stays
where it was, untouched. Copy it over once, right after that deploy (dev first, then prod):

```bash
cd /opt/doomsday_freedom_broker      # or …-dev
docker compose cp app:/var/cqa/cqa.sqlite3 ~/db-backups/prod-before-postgres.sqlite3   # safety copy
docker compose exec app python -m cqa.cli migrate-sqlite        # default path: /var/cqa/cqa.sqlite3
docker compose exec app python -m cqa.cli report | head -3      # dialogs and totals look as before
```

The command copies every table row for row with the same ids, moves the id sequences past the copied rows,
prints a `sqlite / postgres` row count per table and fails (writing nothing) on any mismatch. It refuses a target
that already has conversations; `--replace` wipes the target first (use it to redo the copy, e.g. after a test run).
Keep the SQLite file until you are sure; deleting it is optional.

> [!WARNING]
> Rolling back to a release from before Postgres brings back the SQLite app, which reads the old file and does
> not see anything written to Postgres after the migration.

> [!WARNING]
> The databases contain real customer dialogs and calls. Never commit them to git, and keep copies off public places.

## GitHub settings

| Secret | Value |
|---|---|
| `SSH_HOST` | server IP |
| `SSH_USER` | `ubuntu` |
| `SSH_KEY` | private key of the deploy key (its public half is in the server's `~/.ssh/authorized_keys`) |

The workflow uses environments `production` (main) and `development` (dev); GitHub creates them on the first run. Protect `main` and `dev` with a ruleset that requires the `build` check.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Deploy fails at "Configure SSH" / "Sync code" | Check the 3 secrets; the server IP changes if the EC2 instance is stopped and started |
| Health check fails | The run prints `docker compose logs`; on the server run `docker compose logs -f` in the env folder |
| App restarts with `connection refused` / `password authentication failed` | `docker compose ps db` must be `healthy`; the password in `.env` must match the one the `pgdata` volume was created with |
| App says `provider: mock` | `.env` missing or `LLM_PROVIDER` not `openai`; fix `.env` and restart |
| Site works on the server but not from outside | Open the port (80 / 8080) in the EC2 security group |
