# Editable memory profile validation

Base: master `5fb4f2437bfb4d05d8f1b14fc504d1f4806e527f`; branch `feat/20261115-editable-memory-profile`. PR69 was inspected and not modified. No database/schema migration, new graph store, real provider call, merge or deployment.

## Implementation

MemoryProfile and the settings entry share the existing confirmed-memory API, query cache and SQLite records. Explicit confirmation, CAS versions and mutation identity are retained. Successful writes replace current cache immediately and remove history cache; history reads are keyed by current version. Users can add, edit/reconfirm, withdraw and permanently delete existing memory text/history. Automatic groups only organize text for browsing and never feed recall. No AI-inference store or original-conversation provenance exists in this contract, and neither is fabricated.

## Checks

- Frontend focused interaction, navigation, routing, Pilot page labels and translation coverage: 39 passed (5 files).
- Settings and sidebar/command-palette regression: 23 passed (6 files).
- Production `npm run build` including TypeScript: passed; existing large chunk warning remains.
- Backend confirmed-memory, context-source and chat page-context tests: 39 passed, 376 unrelated chat tests deselected; see `backend-final.log`.
- `ruff check src/offerpilot/api.py tests/test_context_sources.py tests/test_chat_api.py tests/test_confirmed_memory.py evidence/memory-profile/http_smoke.py`: passed.
- `mypy src/offerpilot/api.py`: passed.
- Real HTTP smoke: `python evidence/memory-profile/http_smoke.py` passed against an isolated temporary database. Covers create/replay, edit, stale CAS rejection, unconfirmed rejection, delete/replay, detail/list and actual recall after edit/delete. It uses no model.
- Independent review identified and verified fixes for chat view compatibility, stale history, theme tokens, graph layout and inaccurate wording; follow-up reviewed the validation-error fix without finding a blocking data/confirmation issue.

## Failed attempts and remaining gaps

Initial npm install failed on an ONNX download proxy timeout; identical locked install retry succeeded. First expanded frontend run found the expected-label contract lacked the new page; assertion updated and all 39 passed. Two build/test invocations accidentally ran at repository root (no package.json); corrected invocations in web passed.

Initial real HTTP smoke found an existing 500 when unconfirmed input raised Pydantic ValueError. Fixed only the memory validation response to a stable 422 without echoing private content; added HTTP regression. Original failure retained in `http-smoke.log`; successful rerun in `http-smoke-final.log`.

jsdom reports AntD textarea measurement warnings, and existing settings tests emit diagnostic/network warnings despite passing assertions. Browser console status cannot be inferred from those tests.

Actual browser UI validation and implementation screenshots are blocked by cloud preview connectivity; see root `design-qa.md`. No visual QA pass is claimed. Full release gate, Docker/Windows, actual model/provider and release validation were not run; this is a draft feature branch, not a release candidate. Grouping is keyword-based and may misclassify ambiguous text; it is explicitly labeled and does not mutate stored facts. The five groups do not imply nonempty data or relationships.

Checked-in text logs normalize trailing whitespace only; original local logs retain tool output.
