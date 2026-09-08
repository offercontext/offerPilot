# OfferPilot Project Adjustment Review

Date: 2026-07-07
Branch: `codex/feat/project-adjustments-review`
Inputs: Feishu wiki `OfferPilot开源MVP版本wiki`, Feishu ADR page, current `main` code, frontend design / UI polish skill checks.

## First-Principles Frame

OfferPilot's MVP should optimize for five properties:

1. A user can start locally with one command and trust that private job-search data stays local.
2. The first screen explains the job-search workflow, not the implementation inventory.
3. Application status, events, resumes, Pilot tools, and CLI/API behavior share one domain model.
4. AI is useful because it can safely act on local job-search data, with write operations gated by HITL.
5. Open-source positioning, license, docs, and install paths are consistent before public adoption.

## Highest-Priority Adjustments

| Priority | Adjustment | Current Evidence | Why It Matters | Suggested Next Step |
| --- | --- | --- | --- | --- |
| P0 | Fix license and open-source positioning mismatch | Feishu ADR-001 chooses AGPLv3; repo `LICENSE` and README still say MIT. | This is a release blocker: legal promise and repo artifact conflict. | Replace `LICENSE` with AGPLv3, update README license sections, add contribution / CLA note before accepting external PRs. |
| P0 | Make application status a backend-owned single source of truth | Feishu/ADR says 待投递 -> 已投递 -> 笔试 -> 面试 -> Offer -> 结束; current frontend uses `applied`, `assessment`, `written_test`, `interview`, `offer`, `eliminated`, `rejected`; backend accepts arbitrary strings. | Board columns, Pilot write tools, CLI filters, metrics, and docs can drift. | Add shared status constants in backend, validate API/CLI/tool inputs, expose status metadata to frontend, migrate labels/old values. |
| P0 | Regroup navigation to match product IA | Wiki says top modules are 简历 / 练习 / 投递 / 面试 / 知识库 / 设置, with Pilot as right rail; current sidebar exposes dashboard, board, calendar, reminders, reviews, mock, offers, knowledge, questions, resumes. | Users see implementation pages, not a coherent job-search workspace. | Introduce module-level navigation, move board/calendar/offers under 投递 tabs, mock/reviews under 面试, questions under 练习, resumes under 简历. |
| P0 | Convert Pilot from optional drawer into a persistent product surface | Wiki defines a three-column workspace with right-side Pilot; current `ChatPanel` opens as a large drawer. | Pilot is the differentiator; hiding it behind a button makes the app feel like a tracker plus chat add-on. | Keep drawer for small screens, but use a resizable right rail on desktop and pass active module/application context by default. |
| P0 | Finish platform basics for v0.1 | Wiki requires multi-provider key config, local/server mode, login switch, basic logs. Current config is single `api_key/base_url/model`, no login mode/log view. | v0.1 acceptance requires platform confidence, especially for self-hosted use. | Add config model for providers/runtime mode/logging, then expose Settings UI and CLI/API parity. |
| P0 | Replace or wrap the AI client with LiteLLM routing | ADR-004 chooses LiteLLM; current `ConfiguredAIClient` hand-rolls OpenAI and Anthropic only. | Multi-provider routing/fallback/cost tracking is a core platform promise. | Introduce LiteLLM as the provider abstraction and migrate config/tests around provider profiles. |
| P0 | Persist the HITL agent state beyond the current message flow | ADR-002 chooses LangGraph checkpointer / interrupt semantics; current hand-written loop stores chat messages but no durable pending action/checkpoint table. | Confirmation flows can become fragile across reloads/restarts, and future scheduled wakeups cannot build on the current loop. | Short-term: persist pending actions. Next: add LangGraph + sqlite checkpointer + `scheduled_wakeups`. |
| P0 | Align README with current architecture and roadmap | README still advertises MIT and several implemented/roadmap details that differ from Feishu, while Feishu says AGPL, LiteLLM, LangGraph, Skill mechanism. | README is the public contract for contributors and installers. | Rewrite README around local-first AGPL MVP, status roadmap, quality gates, and verified install paths. |

## Product / Domain Adjustments

| Priority | Adjustment | Rationale |
| --- | --- | --- |
| P0 | Define "v0.1 done" as a thin end-to-end loop, not breadth | The current code already contains many P1/P2 surfaces: question bank, knowledge, offers, mock studio. The product needs a sharper v0.1 path: create resume -> create application -> move status -> add event -> ask Pilot to read/write with confirmation -> restart and verify data. |
| P0 | Make API and CLI behavior visibly symmetric | Architecture review calls this out. New features should land in repositories/workflows first; API/CLI should become adapters. |
| P1 | Add module-level PRDs before expanding more surfaces | The wiki includes a sub-PRD template. Use it for 投递, 简历, Pilot, and 设置 first so implementation stops drifting by component. |
| P1 | Treat Offer as part of the application lifecycle before making it a standalone "谈薪" product | Wiki puts Offer selection under 投递 and negotiation as later enhancement. Current sidebar makes it top-level, which inflates MVP scope. |
| P1 | Make reminders/actions derive from explicit task entities or documented heuristics | Current reminders appear derived from pipeline insights. Good for MVP, but users will eventually need editable tasks/reminders with predictable persistence. |

## Technical Adjustments

| Priority | Adjustment | Rationale |
| --- | --- | --- |
| P0 | Add status validation and compatibility migration | Existing local data may contain old statuses. Add a mapping layer and tests for legacy values. |
| P0 | Add `schema_migrations` trigger before any destructive data change | ADR-006 allows additive changes only; status migrations or embedding columns should remain additive unless the migration table is introduced. |
| P0 | Add smoke coverage for `oc start` serving the built SPA | Unit/build checks pass, but the architecture review explicitly requires real backend + frontend smoke. |
| P1 | Implement real RAG search path | Current knowledge search is LIKE-based and chunks have no embeddings. ADR-008 calls for FTS5 + local embedding + RRF + source citations. |
| P1 | Build the Skill mechanism separately from MCP | ADR-005 says Skill is a context package with trust/install/loading rules; no repo-level model/API exists yet. This should not be confused with registering MCP servers. |
| P1 | Split large frontend container responsibilities | `AppShell` handles data fetching, navigation, modals, detail selection, chat wiring, and derived actions. A module shell/container split would make IA changes safer. |
| P2 | Defer LangGraph time-travel/multi-branch until Pilot workflows need it | The architecture is right, but MVP can start with persisted HITL and provider routing before full graph migration. |

## Frontend / UI Adjustments

| Principle | Before | After |
| --- | --- | --- |
| Information architecture | Sidebar exposes many task-level views as top-level destinations. | Use top-level modules from the wiki; place subviews in segmented tabs inside the content area. |
| Pilot presence | Pilot is launched from buttons and shown as a drawer. | Desktop uses a persistent right rail; drawer remains for mobile/narrow screens. |
| Visual direction | Current palette is heavily purple/violet gradient-led. UI skill guidance for an operational dashboard points to a restrained, high-contrast workspace palette. | Move toward neutral work surfaces, blue/teal trust accents, and sparing action color. Keep gradients only for brand moments. |
| Icons | Several UI labels use emoji (`🎯`, `💡`, `📊`, `✅`) in product surfaces. | Use Ant Design icons consistently; keep emoji out of app UI controls and status labels. |
| Typography | Some UI CSS uses negative letter spacing; headings do not consistently use balanced wrapping. | Remove negative letter spacing, add `text-wrap: balance` to headings and `pretty` to short descriptions. |
| Interaction polish | Buttons and custom controls have mixed hit areas and press feedback. | Ensure 44x44 hit targets for custom buttons, specific transitions only, and optional `scale(0.96)` press feedback. |
| Accessibility | Many controls are labelled, but status/meaning can still rely on color in cards/tags. | Pair color with text/icons, add role/aria-live for async errors, verify focus order in command palette, drawer/rail, kanban drag fallback. |
| Responsive data views | Offer comparison uses horizontal scroll; kanban and dense dashboard areas still need explicit mobile fallback checks. | Use card/list alternatives for narrow screens where board columns or tables become scan-hostile. |

## Verification Run

- `git fetch origin main` and `git pull --ff-only origin main`: main was already up to date.
- Worktree created at `D:\Users\yuqi.chen\offerpilot\.worktrees\project-adjustments-review` on `codex/feat/project-adjustments-review`.
- `uv sync`: passed.
- `npm install`: passed, with npm audit reporting 5 vulnerabilities (3 moderate, 1 high, 1 critical).
- `uv run pytest -q`: 88 passed, 1 Starlette/httpx deprecation warning.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed.
- `npm test`: 40 frontend tests passed.
- `npm run build`: passed.

## Recommended Execution Order

1. Legal/docs gate: AGPL license, README rewrite, contribution note.
2. Domain contract gate: backend-owned status enum, migration compatibility, API/CLI/tool/frontend alignment.
3. v0.1 UX gate: module IA regroup + desktop Pilot rail + Settings basics.
4. AI platform gate: LiteLLM provider profiles, persisted pending actions, logs.
5. MVP smoke gate: built SPA served by `oc start`, core loop browser/API smoke, Docker run verification.
6. P1 expansion: Skill install model, real RAG, question/practice refinements, Offer/talk-salary enhancements.
