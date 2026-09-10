# Contributing to OfferPilot

Thanks for helping improve OfferPilot. This file is the public contributor
entry point; detailed agent workflow rules live in [AGENTS.md](AGENTS.md), and
historical design notes live under [docs/](docs/).

## License

OfferPilot is licensed under AGPLv3. By contributing, you agree that your contribution can be distributed under the project's AGPLv3 license.

Before the project broadly accepts external contributions, the maintainers should decide whether a CLA or DCO process is required. This follows ADR-001 so future licensing choices are not blocked by unclear contribution rights.

## Development

Use a task-specific branch; an existing clean, dedicated worktree can be reused.
Use an isolated worktree when user changes or concurrent tasks need protection
(see [AGENTS.md §2](AGENTS.md#2-开工与工具)). Branch names should follow the
repository convention:

```text
<type>/<yyyymmdd>-<name>
```

Common types are `feat`, `fix`, `docs`, `chore`, `refactor`, and `test`.
Keep commits focused and describe the user-facing or developer-facing change.

## Quality Gates

Choose checks by impact using [AGENTS.md §7](AGENTS.md#7-验证与-code-review).
Finishing a task or pushing a branch does not by itself require a full release
gate. Available check commands, from the repository root (Bash):

```bash
# Example targeted suite for CLI changes; choose files for your change.
uv run pytest -q tests/test_cli.py
uv run ruff check .
uv run mypy src
(cd web && npm test && npm run build)
```

Select the relevant commands above; they are not an unconditional checklist.
`npm test` already runs once; `npm run build` includes TypeScript checking.
When CLI startup, HTTP serving, or their integration is affected, use local smoke:

```bash
bash scripts/local-smoke.sh
```

For a release candidate or an explicitly requested complete acceptance run, use
one full gate instead of first running the same checks individually:

```bash
bash scripts/release-gate.sh
```

On Windows, the corresponding entry points are:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1
# Full release acceptance (choose this instead when needed):
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1
```

Standalone local smoke builds by default. The release gate builds once and passes
`--skip-build` / `-SkipBuild` to reuse that successful build. Manual reuse requires
a successful build of the same code and dependencies; `web/dist/index.html`
must exist, but its existence alone is not proof of freshness. Bash retains the
optional positional port (`bash scripts/local-smoke.sh 18799 --skip-build`);
PowerShell uses `-Port 18799 -SkipBuild`.

Add Docker, installation, or real-provider checks only for the relevant scope
under [AGENTS.md §7](AGENTS.md#7-验证与-code-review). Script commands and optional
flags are described in the [release checklist](docs/p0-release-checklist.md).
Reuse verification evidence only under the unchanged-snapshot conditions in §7;
do not infer full-gate success from targeted tests or a reviewer's assessment.

If a check cannot run in your environment, include the command, failure reason,
and residual risk in the pull request or handoff note.
