# P0 Release Checklist

Date: 2026-07-07
Branch: `codex/feat/project-adjustments-review`

This checklist is the v0.1 release gate. The local default gate is automated by `scripts/release-gate.ps1` and `scripts/release-gate.sh`; Docker remains an explicit opt-in step because some development machines do not have Docker available.

## Scope Status

| P0 area | Status | Evidence |
| --- | --- | --- |
| License and public positioning | Complete | `LICENSE` is AGPLv3; README and `CONTRIBUTING.md` state AGPLv3 contribution terms. |
| Application lifecycle contract | Complete | Backend owns `pending -> applied -> written_test -> interview -> offer -> closed`; legacy status aliases are normalized. |
| Product IA | Complete | Navigation is grouped by module-level workspace areas instead of implementation pages. |
| Pilot product surface | Complete | Desktop Pilot is a persistent rail; narrow screens retain drawer behavior. |
| Platform basics | Complete | Settings expose provider profiles, runtime mode, auth status, and diagnostics logs. |
| AI provider routing | Complete | LiteLLM backs configured provider profiles and preserves provider-specific blocks such as reasoning content. |
| HITL durability | Complete | Pending actions survive reloads via persisted conversation pending fields. |
| Schema safety | Complete | Additive startup repairs are tracked in `schema_migrations`. |
| Local release smoke | Complete | `scripts/local-smoke.ps1` and `scripts/local-smoke.sh` build the SPA, start `oc start`, check health and SPA fallback, then run `oc smoke`. `oc verify --profile local` starts a real HTTP app and exercises API/chat write confirmation, unconfigured AI handling, resume CRUD, and application event CRUD over localhost. |
| Docker: deferred | Deferred | Docker build/run smoke is covered by scripts and static tests, but not executed locally because Docker is unavailable. |

## Required Non-Docker Gate

Run one of these before treating the branch as locally releasable:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1
```

```bash
./scripts/release-gate.sh
```

The scripts wrap the following checks:

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src
cd web
npm.cmd test
npm.cmd run build
cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1
uv run oc verify --profile local --static-dir web/dist
```

When existing AI credentials are available and real provider calls are allowed, run the real provider gate. This includes `uv run oc verify --profile real-ai --static-dir web/dist`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1 -RealAi
```

```bash
./scripts/release-gate.sh --real-ai
```

On a machine with Docker, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1 -Docker
```

```bash
./scripts/release-gate.sh --docker
```

For install-path verification, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1 -Install
```

```bash
./scripts/release-gate.sh --install
```

This calls `scripts/install-gate.ps1` or `scripts/install-gate.sh` to verify the source checkout CLI and isolated `uv tool install` path. The shell gate also verifies `scripts/install.sh --source` against the local checkout.

## Browser Product Walkthrough

Run this once against a built local app before tagging v0.1. Use the default local mode unless the test explicitly checks auth.

1. Dashboard: load the app shell, confirm the dashboard summary, 7-day events, quick actions, and Pilot entry are visible.
2. Resumes: create or open a resume, verify master resume state, completion sections, sample/PDF/manual entry points, and edit drawer behavior.
3. Applications: create an application, edit it, move it across board statuses, confirm closed status requires a reason, and verify list search/filter/sort.
4. Application events: create an application event, confirm it appears in application context and calendar/list surfaces, then update and delete it.
5. Pilot: send a workspace message, send an application-scoped message, verify context display, trigger a write action, reject once, then approve once.
6. Settings: verify provider config status, runtime mode, auth toggle/state, diagnostics logs, and missing API key guidance.
7. Interview empty state: open the interview module, verify the v0.1 empty state and placeholder save behavior does not create formal interview records.

## Deferred After P0

- Docker image build/run verification on a machine with Docker.
- Full LangGraph checkpoint migration beyond durable pending actions.
- Skill execution sandbox beyond trust/manifest provenance.
- Screenshot-level responsive QA for dense data views.
