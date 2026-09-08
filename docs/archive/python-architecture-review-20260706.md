# OfferPilot Python Architecture Review

Date: 2026-07-06
Branch: `feature/20260705-python-rewrite`

Archived snapshot from the Python rewrite cutover. This is not the current
architecture entry point; use `README.md`, `AGENTS.md`, and the active docs in
`docs/` for current operating guidance.

## First Principles

OfferPilot is now a Python-first, local-first job-search workbench. The
architecture should optimize for four properties:

1. Low setup cost: `uv sync && uv run oc start` should start a useful local app.
2. Clear change boundaries: HTTP, CLI, persistence, and AI workflows should share
   business logic instead of diverging.
3. Trustworthy local data: SQLite startup must be additive and idempotent.
4. Fast feedback: pytest, ruff, mypy, frontend tests, and smoke checks should be
   cheap enough to run before every cutover commit.

## Current Shape

- `src/offerpilot/api.py` owns FastAPI routing, CORS, API response shape, and SPA
  static fallback.
- `src/offerpilot/cli.py` owns the Typer `oc` command surface and delegates data
  work to repositories/workflows.
- `src/offerpilot/db.py` initializes SQLite, enables foreign keys, and performs
  additive compatibility columns.
- `src/offerpilot/models.py` defines SQLAlchemy models with Go-compatible table
  and column names.
- `src/offerpilot/repositories/` owns persistence behavior by topic.
- `src/offerpilot/ai/` owns provider adapters, tool-loop safety, and reusable AI
  workflows for JD analysis, resume matching, and question generation.
- `web/src/services` keeps the React SPA on the `/api` contract.

## High-Priority Follow-Ups

### 1. Keep API and CLI workflows unified

The Python CLI now uses `offerpilot.ai.workflows` for AI commands. API endpoints
still contain some duplicated prompt/JSON helpers. The next cleanup should move
JD, resume, question, material-kit, and mock scoring endpoints fully onto
workflow functions so behavior cannot drift between HTTP and CLI.

### 2. Make SQLite migration policy explicit

Startup is additive and idempotent, but there is no `schema_migrations` table yet.
That is acceptable for this cutover only because the current goal is preserving
existing local data while removing the Go runtime. Before larger schema changes,
add ordered migration records or a deliberately documented no-Alembic policy.

### 3. Keep frontend smoke tests real

Unit tests and Vite build are necessary but not enough. A release candidate
should run Python `oc start`, load the React app through the Python static
fallback, and exercise at least health, application creation/listing, dashboard,
and one non-AI module through the browser/API proxy.

## Verification Baseline

Current expected verification commands:

- `uv run pytest -q`
- `uv run ruff check .`
- `uv run mypy src`
- `npm test` in `web/`
- `npm run build` in `web/`
- real Python backend + frontend smoke against `http://127.0.0.1:8080`

Docker verification should run when Docker is available:

- `docker build -t offerpilot-python .`
- `docker run --rm -p 8080:8080 -v offerpilot-data:/data offerpilot-python`
