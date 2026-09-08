# Contributing to OfferPilot

Thanks for helping improve OfferPilot. This file is the public contributor
entry point; detailed agent workflow rules live in [AGENTS.md](AGENTS.md), and
historical design notes live under [docs/](docs/).

## License

OfferPilot is licensed under AGPLv3. By contributing, you agree that your contribution can be distributed under the project's AGPLv3 license.

Before the project broadly accepts external contributions, the maintainers should decide whether a CLA or DCO process is required. This follows ADR-001 so future licensing choices are not blocked by unclear contribution rights.

## Development

Use a feature branch or worktree for changes. Branch names should follow the
repository convention:

```text
<type>/<yyyymmdd>-<name>
```

Common types are `feat`, `fix`, `docs`, `chore`, `refactor`, and `test`.
Keep commits focused and describe the user-facing or developer-facing change.

## Quality Gates

Run the relevant checks before opening a pull request:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
cd web && npm test -- --run
cd web && npm run build
```

For user-facing behavior changes, also run a local smoke check:

```bash
scripts/local-smoke.sh
```

If a check cannot run in your environment, include the command, failure reason,
and residual risk in the pull request or handoff note.
