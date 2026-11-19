# QueryMuse

Conversational data exploration ([querymuse.ai](https://www.querymuse.ai); app at `https://app.querymuse.ai`). Formerly One Data Navigator; internal names (`odn` package, `ODN_*` settings) are unchanged.

MVP plan: [docs/one_data_navigator_mvp_dev_plan.md](docs/one_data_navigator_mvp_dev_plan.md)

## Local development (Docker)

```sh
cp .env.example .env          # BigQuery SA JSON goes in ./keys/ (gitignored); set BQ_* vars
docker compose up -d --build
docker compose --profile targets up -d sample-postgres   # sample Postgres source (Phase 3)
```

| Service | URL (host) | Purpose |
| --- | --- | --- |
| frontend | http://localhost:3100 | Next.js + CopilotKit web app |
| api | http://localhost:8100 (`/healthz`, `/readyz`) | API/BFF + platform services |
| agent | http://localhost:8110 | ADK agent runtime |
| worker | internal only (`worker:8000`) | Isolated DuckDB + chart worker |
| postgres | localhost:5532 (`odn` / `odn_app`) | Metadata DB (pgvector) |
| gcs | http://localhost:4443 | fake-gcs-server, bucket `odn-workspace-local` |
| oidc | http://localhost:8180/odn | mock OIDC issuer |
| sample-postgres | localhost:5533 (`analyst_ro`) | Sample source DB |

Backend tests: `cd backend && uv sync --all-extras && uv run pytest`

## Production (GCP VM)

The same stack runs on one Compute Engine VM with `docker-compose.prod.yml` (Google sign-in, Caddy TLS,
`gs://datahop`). Deploy the committed `HEAD` with `deploy/deploy.sh up`. See
[docs/deploy_gcp_vm.md](docs/deploy_gcp_vm.md).
