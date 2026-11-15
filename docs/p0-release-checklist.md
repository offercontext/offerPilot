# P0 Release Checklist

Date: 2026-07-07
Branch: `codex/feat/project-adjustments-review`

The scope/status, browser walkthrough, and deferred sections below are the historical v0.1 snapshot, not current feature restrictions or a fresh verification report. See [early interview scope](archive/early-interview-version-scope.md) for the former version boundaries. The command reference is maintained for the current `scripts/release-gate.ps1` and `scripts/release-gate.sh`; verification triggers, evidence reuse, and optional-gate scope are owned by [AGENTS.md §7](../AGENTS.md#7-验证与-code-review).

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
$env:PYTHONUTF8 = "1"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1
```

```bash
./scripts/release-gate.sh
```

The scripts wrap the checks below. This is an explanation of the full gate, not a second checklist to run before or after it. A release builds the frontend once, then calls local smoke with `--skip-build` / `-SkipBuild`; standalone local smoke still builds by default. Skipping requires `web/dist/index.html`, and the caller must establish that the build matches the current code and dependencies.

Windows uses `PYTHONUTF8=1`, matching `.github/workflows/desktop-windows.yml`. Set it before pytest collection, shard execution and the release entrypoint; `PYTHONIOENCODING` alone changes streams, not default file decoding. Keep one source/dependency/configuration/environment snapshot for all evidence. Preserve unsuccessful attempts separately and do not relabel evidence from an older SHA as a complete gate for a new SHA.

Both runtime layers remain: local smoke launches the actual `oc start` CLI process and checks health/SPA fallback before `oc smoke`; `oc verify --profile local` runs broader business scenarios over an isolated HTTP app. The business checks overlap (and share the chat-card helper), but local verify does not replace the independent CLI startup check. This change does not merge or remove either layer. PowerShell gates explicitly stop on nonzero native-command and child-gate exit codes.

The general pytest gate tests the historical Application JD implementation-scope guard with isolated Git fixtures (committed, staged, unstaged, and untracked changes). To audit an actual JD release scope, supply both `OFFERPILOT_APPLICATION_JD_BASELINE_FILE` and `OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE` and run `uv run pytest -q tests/test_application_jd_browser_harness.py -k implementation_scope_is_machine_checked`. The files must contain the previously approved baseline SHA and ASCII path allowlist, not values generated from the current diff. Setting either variable activates the real-worktree audit; a missing counterpart, invalid file, or out-of-scope change fails closed. General regression success is not evidence that a particular release's file scope was approved.

Frontend tests use `web/vitest.config.ts` to cap worker concurrency at four while retaining every test and the production source audit's thirty-second budget. This avoids CPU/IO contention from host-dependent default worker counts; `npm test` remains the full frontend gate.

For a host under memory pressure, the full entry also supports `-IsolateBackendTests -BackendOutput <new-directory>` (PowerShell), or `--isolate-backend-tests --backend-output <new-directory>` (Bash). Use a new ignored directory such as `artifacts/backend-full-<run-id>`, or a location outside the checkout, so evidence files do not change the source snapshot. This optional mode freezes the complete pytest catalog and runs serial processes with a target of 400 nodes each, keeping each file together; an oversized file remains one group. Session fixtures are recreated per process. It verifies every collected node's execution identity, native exits, source/index, recorded Python/platform/dependency/tool versions, and persisted log/report hashes before accepting a full backend receipt; the environment fingerprint does not cover every process variable. Skips remain separately reported. Missing/duplicate results, failure, changed recorded inputs or an existing output directory block completion. `PYTEST_ADDOPTS` must be empty. Interrupted runs retain partial evidence and require a new directory; there is no automatic resume or cleanup. Standalone `--verify` may revalidate a matching old receipt, while the complete gate always executes into a new directory first. The default remains one `pytest -q` process; all other release checks still run in either mode.

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src
cd web
npm.cmd test
npm.cmd run build
cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1 -SkipBuild
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

This calls `scripts/install-gate.ps1` or `scripts/install-gate.sh` to verify the source checkout CLI and isolated `uv tool install` path. It checks that the base install does not require MCP, installs the `mcp` extra, and uses that installed environment for real stdio preparation and pending readback with fictitious data. It never approves the request. Release-gate runs retain installation artifacts and print their location; failures always retain them. The shell gate also verifies `scripts/install.sh --source` against the local checkout. Windows and POSIX installation execution are separate evidence.

## Browser Product Walkthrough

Historical v0.1 walkthrough: the original instruction was to run this against a built local app before tagging v0.1. Do not treat the interview placeholder below as a current feature limit; select current product scenarios using the applicable PRD/ADR.

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
