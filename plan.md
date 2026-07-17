# Catalog Enrichment Studio Implementation Plan

## Current Repository State

- The repository is effectively greenfield. No reusable application code, package manifests, migrations, tests, or documentation exist yet.
- The only tracked file from the initial commit was `README.md`; it is currently deleted in the working tree. This plan does not restore it.
- An unrelated untracked file, `xcvjh`, exists and is left untouched.
- The product spec requests implementation, but the first approved step is planning only. No application code or dependencies are added in this phase.

## Fixed Decisions

- Access model: trusted internal users, no app login for the first build.
- Expected scale: up to 5,000 SKUs per run, with a few concurrent runs.
- LLM adapter support: both embedded LiteLLM SDK and generic OpenAI-compatible HTTP profile.
- Mixed attribute sets within one base-code group: reject the group during preflight.
- Configuration reproducibility: freeze published effective snapshots for runs, without building full history screens for every editable config table.
- Deletion model: 30-day soft delete with restore, followed by scheduled purge.

## Architecture

- Frontend: React, TypeScript, Vite, Tailwind, TanStack Query, TanStack Table, React Router, safe markdown rendering, and local reusable controls only where repeated.
- Backend: FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, Redis, Celery, MinIO/S3-compatible storage, HTTPX, Pillow, openpyxl, boto3, cryptography, and LiteLLM.
- Deployment: Docker Compose for local/Codespaces development with backend, worker, beat, frontend, PostgreSQL, Redis, and MinIO.
- API: `/api/v1`, UUID identifiers, UTC timestamps, paginated list responses, `202 Accepted` for background jobs, row-version optimistic edits, and sanitized errors.
- Storage: private buckets only, using short-lived object URLs or backend streaming for review/download workflows.
- Configuration: live relational config plus immutable published JSON snapshots pinned to runs. Secrets stay encrypted in profile rows and are never copied into snapshots.
- Processing: PostgreSQL is authoritative for run/task state. Redis is used for Celery and lightweight concurrency leases, not as the source of truth.
- Security defaults: public HTTP/HTTPS image URLs only, SSRF checks across redirects, byte/pixel caps, no proxy environment usage, HTTPS LLM endpoints by default, and explicit allowlists for private LLM endpoints.
- Minimal deliberate omissions: no RBAC/SSO, no cloud-specific deployment, no workflow engine, no WebSockets, no pandas/Polars, no browser E2E suite, and no full CMS connector until a target CMS contract exists.

## Phase 1 - Preserve Approved Plan

Create `plan.md` with the approved phased plan. Do not create application code, install dependencies, restore `README.md`, or touch unrelated working-tree files.

Acceptance criteria:

- `plan.md` exists and captures the agreed architecture, phases, risks, acceptance criteria, and verification commands.
- Existing user-owned working-tree changes remain untouched.
- No application code, package manifests, generated lockfiles, or dependency directories are created.

Verification commands:

```bash
test -s plan.md
grep -q "Phase 8" plan.md
git diff --check
git status --short
```

## Phase 2 - Infrastructure, Data Model, and UI Shell

Scaffold the smallest working app skeleton: backend, frontend, Compose services, settings, migrations, storage client, health checks, JSON logging with secret redaction, and a basic sidebar shell for the required product areas.

Implement:

- Backend package with FastAPI app, settings, database session, Alembic setup, health endpoints, and structured errors.
- Compose services for PostgreSQL, Redis, MinIO, backend, Celery worker, Celery beat, and frontend.
- `start.sh` that generates ignored development secrets when absent, waits for health, and prints local/Codespaces URLs.
- Production guardrails that reject default development secrets.
- Initial schema for users/system metadata, config tables, run tables, task tables, image tables, extraction/mapping/validation tables, edits, exports, and soft-delete fields.
- Frontend app shell with navigation for Dashboard, Templates, Attributes, Value Lists, Mapping Profiles, LLM Profiles, Image Downloader, Runs, Review, Exports, and Settings.

Acceptance criteria:

- Fresh checkout can start all services locally.
- Backend and frontend are reachable on expected ports.
- Migrations apply cleanly to an empty database.
- Health endpoint verifies database, Redis, and storage connectivity.
- Logs do not print API keys or encrypted secrets.

Verification commands:

```bash
docker compose config --quiet
./start.sh
docker compose exec backend alembic upgrade head
docker compose exec backend alembic check
curl -fsS http://localhost:8000/api/v1/health
curl -fsS http://localhost:5173
docker compose exec backend pytest
docker compose exec frontend npm run typecheck
docker compose exec frontend npm test
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

## Phase 3 - Configuration Modules

Build CRUD and publish flows for headers, value lists, attribute sets, prompts, mapping profiles, app settings, and LLM profiles.

Implement:

- Header definitions with aliases and required/generated flags.
- Value lists with canonical values, aliases, archive behavior, duplicate prevention, and import/export.
- Attribute sets with assignment rules and immutable published snapshots.
- Prompt and mapping profile version publication.
- LLM profiles with encrypted API keys, sanitized reads, async connection-test jobs, and no key decryption in normal API responses.
- Markdown editor/preview for prompt text using safe rendering.

Acceptance criteria:

- Published snapshots are immutable and runs can pin them later.
- Duplicate aliases and canonical values are rejected deterministically.
- Secrets are encrypted at rest, redacted in logs, and never returned by API reads.
- Connection tests run through the worker path so provider access matches production execution.

Verification commands:

```bash
docker compose exec backend pytest tests/test_config.py
docker compose exec backend pytest tests/test_snapshots.py
docker compose exec backend pytest tests/test_llm_profiles.py
docker compose exec frontend npm test -- --run config
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run lint
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

## Phase 4 - Shared Image Downloader

Create one shared downloader/parser service used by both the standalone image workflow and catalog runs.

Implement:

- Workbook parsing with configured header aliases, generated image column detection, and raw-cell preservation.
- Secure bounded HTTP fetch with redirect checks, DNS/private-address rejection, timeout and retry limits, byte caps, and image pixel caps.
- Pillow normalization: EXIF orientation, transparency handling, RGB output, aspect-fit to 1500 canvas, JPEG quality 90.
- Storage persistence, checksum/cache reuse, per-image metadata, and per-row failure reporting.
- Standalone outputs: normalized image ZIP, report CSV/XLSX, and validation summary.

Acceptance criteria:

- Dynamic image columns and configured aliases parse correctly.
- Bad image URLs fail per row without stopping the whole job.
- SSRF, oversize file, oversize pixel, redirect, and timeout cases are rejected.
- Repeated URLs reuse stored normalized images when request settings match.

Verification commands:

```bash
docker compose exec backend pytest tests/test_workbook_images.py
docker compose exec backend pytest tests/test_image_downloader.py
docker compose exec backend pytest tests/test_ssrf.py
docker compose exec backend pytest tests/test_image_reports.py
docker compose exec frontend npm test -- --run image
docker compose exec frontend npm run typecheck
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

## Phase 5 - Indexer, Preflight, Jobs, and Runs

Build upload, template generation, preflight validation, run creation, progress tracking, cancellation, retry, duplication, resume, and soft-delete flows.

Implement:

- Generated templates with exact required configured columns plus `image_1` through `image_10`.
- Workbook upload accepting configured headers and up to 50 contiguous image columns.
- Preflight with canonical header mapping, row counts, missing required fields, invalid base-code grouping, image URL checks, attribute-set assignment, and no LLM calls.
- Grouping by base code, deterministic representative image selection, and rejection when one base-code group resolves to mixed attribute sets.
- Idempotent task creation, locked dispatcher, retry/cancel/resume behavior, duplicate-run creation from pinned config, and retention purge via Celery beat.

Acceptance criteria:

- Preflight catches blocking issues before run creation.
- Mixed attribute sets are allowed across a workbook but rejected within the same base-code group.
- Cancelled and retried runs do not corrupt completed row/task state.
- Soft-deleted runs can be restored within 30 days and purged afterward.

Verification commands:

```bash
docker compose exec backend pytest tests/test_templates.py
docker compose exec backend pytest tests/test_preflight.py
docker compose exec backend pytest tests/test_grouping.py
docker compose exec backend pytest tests/test_jobs.py
docker compose exec backend pytest tests/test_retention.py
docker compose exec worker celery -A app.worker inspect ping
docker compose exec frontend npm test -- --run runs
docker compose exec frontend npm run typecheck
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

## Phase 6 - Vision, Mapping, Validation, and Provenance

Implement the enrichment pipeline using mockable provider adapters, deterministic mapping, validation, and detailed provenance.

Implement:

- Provider protocol with LiteLLM SDK adapter, OpenAI-compatible HTTP adapter, and mock adapter for tests/demo.
- Vision prompts that require image positions, strict JSON response validation, and one schema-repair retry.
- Vision request cache keyed by model settings, prompt version, image checksums, and schema.
- Deterministic mapping priority: direct configured columns, parsed `input_data`, vision extraction, defaults, then blank.
- Value-list mapping with exact aliases first, optional fuzzy matching only when enabled, ambiguity rejection, and configurable multiselect delimiter.
- Validation issues for missing required values, invalid select values, ambiguity, confidence thresholds, and provider/schema errors.
- Provenance per field, including source, confidence, model/profile snapshot, prompt version, image references, usage, and provider-reported cost.

Acceptance criteria:

- Tests run against mocks without paid provider keys.
- Schema-invalid provider responses retry once and then fail clearly.
- Direct workbook values outrank parsed and vision-derived values.
- Mapping and validation produce deterministic results for aliases, invalid values, multiselects, and ambiguous fuzzy matches.
- Cached vision requests avoid repeated provider calls for identical request hashes.

Verification commands:

```bash
docker compose exec backend pytest tests/test_providers.py
docker compose exec backend pytest tests/test_vision.py
docker compose exec backend pytest tests/test_mapping.py
docker compose exec backend pytest tests/test_validation.py
docker compose exec backend pytest tests/test_pipeline.py
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

## Phase 7 - Review and Export

Build the operational review grid, field editing, provenance inspection, and export jobs.

Implement:

- Server-paginated review grid with frozen identifying columns, filters, search, sort, status, confidence, and source indicators.
- Inline edits, bulk edits, undo for recent local changes, optimistic concurrency via row versions, audit trail, and revalidation.
- Provenance panel for each field.
- Background exports for combined XLSX/CSV and image ZIP.
- Safe output filenames, text-preserved SKU/EAN cells, formula-injection protection, and override confirmation for blocking validation errors.

Acceptance criteria:

- A 5,000-row run does not load every row into the browser at once.
- Edits are audited and validation updates after changes.
- Conflicting edits are rejected with a clear reload path.
- Exports open in spreadsheet software with SKU/EAN values preserved as text.
- Blocking validation issues prevent export unless the user confirms an audited override.

Verification commands:

```bash
docker compose exec backend pytest tests/test_review.py
docker compose exec backend pytest tests/test_edits.py
docker compose exec backend pytest tests/test_exports.py
docker compose exec frontend npm test -- --run review
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run lint
docker compose exec backend ruff check .
docker compose exec backend ruff format --check .
```

## Phase 8 - Demo, Documentation, and Final Audit

Add only the demo assets and docs needed to prove the app works locally.

Implement:

- Opt-in seed data with mock LLM profile, sample attribute set, value lists, mapping profile, local sample images, and sample workbook.
- Complete `README.md` replacing the deleted placeholder only during this documentation phase.
- Setup, operation, configuration, security, troubleshooting, and workflow docs.
- A small demo verifier that runs the mock workflow end to end.

Acceptance criteria:

- A clean local/Codespaces setup can run the complete mock workflow without external provider keys.
- Documentation explains the trusted-internal access model and production hardening gaps.
- Final audit uses an isolated Compose project and leaves the normal developer environment alone.
- No secrets, object data, dependency directories, or generated exports are committed.

Verification commands:

```bash
COMPOSE_PROJECT_NAME=catalog-enrichment-audit BACKEND_PORT=18000 FRONTEND_PORT=15173 ./start.sh
docker compose -p catalog-enrichment-audit exec backend alembic upgrade head
docker compose -p catalog-enrichment-audit exec backend pytest
docker compose -p catalog-enrichment-audit exec frontend npm test
docker compose -p catalog-enrichment-audit exec frontend npm run typecheck
docker compose -p catalog-enrichment-audit exec frontend npm run lint
docker compose -p catalog-enrichment-audit exec frontend npm run build
docker compose -p catalog-enrichment-audit exec backend ruff check .
docker compose -p catalog-enrichment-audit exec backend ruff format --check .
docker compose -p catalog-enrichment-audit exec backend python -m app.demo.verify_workflow
curl -fsS http://localhost:18000/api/v1/health
curl -fsS http://localhost:15173
docker compose -p catalog-enrichment-audit down -v
git diff --check
git status --short
```

## Architectural Risks and Assumptions

- No-login access is acceptable only for a trusted private environment. SSO/RBAC should be added before broad or multi-tenant use.
- External LLM calls may create data-residency obligations. The first build records provider/profile provenance but does not enforce regional policy.
- "CMS-ready" means configurable export shapes for now. A real CMS connector needs the target platform contract.
- Leading zeroes cannot be recovered if Excel already destroyed them before upload.
- SSRF controls reduce risk but production should still use network egress controls.
- A provider timeout after charge but before response can still duplicate provider cost on retry; the system can make state idempotent, not provider billing.
- Provider-reported cost is stored when available; no internal pricing catalog is planned.
- Base-code conflict detection is deterministic for structured fields, but raw prose comparison remains limited.
- Backups, monitoring, managed KMS, metrics, alerts, and disaster recovery are production hardening tasks outside the first build.
