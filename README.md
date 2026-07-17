# Catalog Enrichment Studio

An internal web app for turning product workbooks and images into validated, reviewable,
spreadsheet-ready catalog data. It uses a mock vision provider for the included demo, so no paid
API key is needed.

## Start locally

Requirements: Docker with Compose v2 and `curl`.

```bash
./start.sh
```

The script creates a private `.env` on first run, builds the services, waits for the dependency-aware
health check, and prints the URLs. The defaults are:

- UI: http://localhost:5173
- API and OpenAPI docs: http://localhost:8000/docs

Stop with `docker compose down`; add `-v` only when you intentionally want to erase local database,
queue, and object data. Apply schema changes with `docker compose exec backend alembic upgrade head`.

## Workflow

1. Run the opt-in demo seed: `docker compose exec backend python -m app.demo.seed`.
2. Download the seeded workbook from http://localhost:8000/api/v1/demo/catalog.xlsx, or configure
   and publish your own headers, attribute/value lists, mapping rules, prompts, and LLM profile.
3. Download the generated template, fill it, and upload it from **Runs**.
4. Resolve preflight errors. Preflight never calls an LLM.
5. Start the run, review validation issues and provenance, then edit or bulk-edit values.
6. Export CSV/XLSX and normalized images. Blocking issues require an audited override.

The end-to-end mock check is:

```bash
docker compose exec backend python -m app.demo.verify_workflow
```

## Configuration

Runtime settings are environment variables documented in `.env.example`. `start.sh` generates local
database, object-store, signing, and Fernet encryption secrets. Published configuration is copied to
immutable run snapshots; encrypted provider keys are not included. LLM profiles support the embedded
LiteLLM adapter, generic OpenAI-compatible HTTPS endpoints, and the local mock provider.

Useful checks:

```bash
docker compose exec backend pytest
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
docker compose exec frontend npm test
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run lint
docker compose exec frontend npm run build
docker compose exec backend alembic check
```

## Operations and retention

PostgreSQL is authoritative for runs and tasks; Redis carries Celery messages and leases; private
objects live in MinIO/S3. The worker processes jobs and beat purges runs after the 30-day restore
window. Run/task operations are idempotent, so retry and resume preserve completed work. Use the
health endpoint when wiring monitoring: `GET /api/v1/health` checks all three dependencies.

Logs are JSON and redact common secret fields. Provider usage and reported cost are stored as
provenance; there is intentionally no internal price catalog.

## Security boundary

This first build has no login and is only suitable for a trusted private network. Add SSO/RBAC before
multi-tenant or public use. Image downloads allow only public HTTP/HTTPS destinations, recheck every
redirect, ignore proxy environment variables, and enforce byte/pixel limits. Production mode rejects
development secrets and non-HTTPS provider endpoints unless a private endpoint is explicitly allowed.

Production still needs network egress controls, TLS at the edge, managed secrets/KMS, backups,
monitoring, alerts, and a disaster-recovery plan. Review provider data-residency terms before sending
catalog data externally.

## Troubleshooting

- `docker compose ps` shows dependency and application health.
- `docker compose logs backend worker frontend` shows startup or job failures.
- If a port is occupied, set `BACKEND_PORT`/`FRONTEND_PORT` before `./start.sh`.
- If local state is disposable, `docker compose down -v` gives the next start a clean database/store.
- Spreadsheet software may already have removed leading zeroes before upload; the app cannot recover
  them, but exports preserve SKU/EAN values as text and neutralize spreadsheet formulas.

The approved scope, acceptance criteria, and isolated final-audit commands are in [plan.md](plan.md).
