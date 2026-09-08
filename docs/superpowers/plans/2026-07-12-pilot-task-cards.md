# Pilot Task and Structured Action Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make substantive Pilot replies render as one durable task card with real completed tool steps, a concise conclusion, and safe follow-up actions.

**Architecture:** Keep the buffered Chat API and persisted message schema unchanged. The backend asks task-oriented replies to end with a constrained Markdown tail; `model.ts` parses it during normal turn reconstruction, then a new React card combines that presentation with persisted tool steps. Follow-up buttons call the existing chat submission path, so all write requests still require HITL confirmation.

**Tech Stack:** Python/FastAPI, React 18, TypeScript, Ant Design icons, TanStack Query, Vitest, jsdom.

---

## File map

| File | Responsibility |
| --- | --- |
| `src/offerpilot/api.py` | Ask Pilot for the stable Markdown response tail without changing Chat API payloads. |
| `tests/test_chat_api.py` | Lock the system-prompt contract and pending-write regression behavior. |
| `web/src/components/ChatPanel/model.ts` | Parse the Markdown tail and attach presentation/title data while rebuilding persisted turns. |
| `web/src/components/ChatPanel/model.test.ts` | Unit-test parsing, action limits, fallback, and persistence reconstruction. |
| `web/src/components/ChatPanel/PilotTaskCard.tsx` | Render the unified completed-turn card and route action clicks outward. |
| `web/src/components/ChatPanel/PilotTaskCard.render.test.tsx` | Real DOM regression coverage for card semantics, actions, and disabled state. |
| `web/src/components/ChatPanel/ProcessTimeline.tsx` | Accept a contextual summary label when embedded in a task card. |
| `web/src/components/ChatPanel/MessageBubble.tsx` | Compose a task card, remaining Markdown, and the non-card legacy path. |
| `web/src/components/ChatPanel/MessageBubble.render.test.tsx` | Verify structured content is not duplicated and a card action reaches its handler. |
| `web/src/components/ChatPanel/index.tsx` | Send card actions through the existing `sendMessage` guard and disable them with the composer. |
| `web/src/components/ChatPanel/ChatPanel.module.css` | Style unified cards and embedded process detail in desktop rail and narrow layouts. |
| `web/package.json`, `web/package-lock.json` | Add the jsdom environment needed for genuine React DOM tests. |

### Task 1: Parse and rebuild structured turn presentation

**Files:**
- Modify: `web/src/components/ChatPanel/model.ts:38-385`
- Modify: `web/src/components/ChatPanel/model.test.ts:1-385`

- [ ] **Step 1: Write failing parser and reconstruction tests**

Add the following cases to `model.test.ts`; keep the existing `msg` factory.

```ts
import { buildTurns, parseTurnPresentation } from './model';

it('extracts a structured conclusion/action tail and keeps supporting markdown', () => {
  expect(parseTurnPresentation([
    '查到周三有一场技术一面。',
    '',
    '## 结论',
    '本周应优先准备技术一面。',
    '',
    '## 下一步',
    '- 生成技术一面准备清单',
    '- 查看本周日程',
  ].join('\n'))).toEqual({
    conclusion: '本周应优先准备技术一面。',
    actions: ['生成技术一面准备清单', '查看本周日程'],
    detailMarkdown: '查到周三有一场技术一面。',
  });
});

it('accepts level-three headings, caps actions, and rejects an incomplete tail', () => {
  const parsed = parseTurnPresentation('### 结论\n先准备面试。\n### 下一步\n- A\n- B\n- C\n- D');
  expect(parsed?.actions).toEqual(['A', 'B', 'C']);
  expect(parseTurnPresentation('## 结论\n只有结论')).toBeUndefined();
});

it('rebuilds a persisted tool turn with task title, presentation, and real steps', () => {
  const turns = buildTurns([
    msg({ role: 'user', content: '帮我安排本周面试准备' }),
    msg({ role: 'assistant', tool_calls: JSON.stringify([{ name: 'list_application_events', args: {} }]) }),
    msg({ role: 'tool', content: '[]' }),
    msg({ role: 'assistant', content: '## 结论\n优先准备周三技术一面。\n## 下一步\n- 生成准备清单' }),
  ]);
  expect(turns[1]).toMatchObject({
    taskTitle: '帮我安排本周面试准备',
    steps: [{ name: 'list_application_events' }],
    presentation: { conclusion: '优先准备周三技术一面。', actions: ['生成准备清单'] },
    content: '',
  });
});
```

- [ ] **Step 2: Run the new tests and verify red**

Run: `npm.cmd test -- --run src/components/ChatPanel/model.test.ts`

Expected: FAIL because `parseTurnPresentation` and the new `UITurn` fields do not exist.

- [ ] **Step 3: Implement the typed presentation parser and turn attachment**

In `model.ts`, define the stable presentation fields and parser before `buildTurns`:

```ts
export interface TurnPresentation {
  conclusion: string;
  actions: string[];
  detailMarkdown: string;
}

const STRUCTURED_HEADER = /^ {0,3}#{2,3}\s*(结论|下一步)\s*$/gim;
const MAX_TASK_ACTIONS = 3;

export function parseTurnPresentation(content: string): TurnPresentation | undefined {
  const headers = [...content.matchAll(STRUCTURED_HEADER)];
  const conclusionHeader = headers.find((header) => header[1] === '结论');
  const actionsHeader = headers.find((header) => header[1] === '下一步');
  if (!conclusionHeader || !actionsHeader || conclusionHeader.index === undefined || actionsHeader.index === undefined) return undefined;
  if (conclusionHeader.index >= actionsHeader.index) return undefined;

  const conclusionStart = conclusionHeader.index + conclusionHeader[0].length;
  const actionsStart = actionsHeader.index + actionsHeader[0].length;
  const conclusion = content.slice(conclusionStart, actionsHeader.index).trim();
  const actions = content
    .slice(actionsStart)
    .split('\n')
    .map((line) => line.match(/^\s*[-*+]\s+(.+)$/)?.[1]?.trim())
    .filter((line): line is string => Boolean(line))
    .slice(0, MAX_TASK_ACTIONS);
  if (!conclusion || actions.length === 0) return undefined;

  return { conclusion, actions, detailMarkdown: content.slice(0, conclusionHeader.index).trim() };
}
```

Extend `UITurn` with `taskTitle?: string` and `presentation?: TurnPresentation`. In `buildTurns`, remember the most recent user content, parse each visible assistant message, set `content` to `presentation?.detailMarkdown ?? m.content`, and attach a title with this helper:

```ts
function taskTitleFor(userContent: string): string {
  const normalized = userContent.replace(/\s+/g, ' ').trim();
  if (!normalized) return '本轮任务';
  return normalized.length > 36 ? `${normalized.slice(0, 36)}…` : normalized;
}
```

Do not synthesize tool steps in the model. The card owns its `整理建议` no-tool display, while `UITurn.steps` remains an exact representation of persisted tool calls.

- [ ] **Step 4: Run model tests and TypeScript verification**

Run: `npm.cmd test -- --run src/components/ChatPanel/model.test.ts && npm.cmd exec tsc -- -b`

Expected: model tests pass and TypeScript reports no errors.

- [ ] **Step 5: Commit the parser slice**

```bash
git add web/src/components/ChatPanel/model.ts web/src/components/ChatPanel/model.test.ts
git commit -m "feat: AI parse structured Pilot replies"
```

### Task 2: Request the stable response tail from Pilot

**Files:**
- Modify: `src/offerpilot/api.py:1488-1497`
- Modify: `tests/test_chat_api.py`

- [ ] **Step 1: Write a failing system-prompt contract regression**

Add a test using the existing `ScriptedModel`, `Assistant`, `TestClient`, and `create_app` helpers:

```python
def test_chat_system_prompt_requests_structured_task_tail(tmp_path):
    model = ScriptedModel([Assistant(content="plain reply")])
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model))

    response = client.post("/api/chat", json={"message": "总结我的投递", "conversation_id": 0})

    assert response.status_code == 200
    system = next(message for message in model.calls[0] if message.role == "system")
    assert "## 结论" in system.content
    assert "## 下一步" in system.content
    assert "Do not add text after the next-step list" in system.content
```

- [ ] **Step 2: Run the new API test and verify red**

Run: `uv run pytest tests/test_chat_api.py::test_chat_system_prompt_requests_structured_task_tail -q`

Expected: FAIL because the current system prompt only names prose sections.

- [ ] **Step 3: Add the compact prompt instruction without changing API data**

Replace `_chat_response_system_message()` content with an instruction that preserves its current language, evidence, and hidden-reasoning requirements and adds this exact contract:

```python
"For a substantive task reply, put supporting evidence or caveats first, then end with exactly "
"'## 结论' followed by one concise plain-text conclusion and '## 下一步' followed by one to "
"three '- ' follow-up prompts. Do not add text after the next-step list. "
"For greetings or a clarification question, do not force these headings. "
```

Do not add a response model, database column, or Chat API field.

- [ ] **Step 4: Run chat API regression coverage**

Run: `uv run pytest tests/test_chat_api.py -q`

Expected: all chat API tests pass, including existing pending-write confirmation cases.

- [ ] **Step 5: Commit the prompt contract**

```bash
git add src/offerpilot/api.py tests/test_chat_api.py
git commit -m "feat: AI request structured Pilot replies"
```

### Task 3: Build the accessible unified task card

**Files:**
- Create: `web/src/components/ChatPanel/PilotTaskCard.tsx`
- Create: `web/src/components/ChatPanel/PilotTaskCard.render.test.tsx`
- Modify: `web/src/components/ChatPanel/ProcessTimeline.tsx:7-59`
- Modify: `web/src/components/ChatPanel/ChatPanel.module.css:510-670`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

- [ ] **Step 1: Add jsdom and write the failing DOM test**

Install the test environment with `npm.cmd install --save-dev jsdom@25.0.1`. Create `PilotTaskCard.render.test.tsx` with a jsdom directive and the following real-render cases:

```tsx
/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PilotTaskCard from './PilotTaskCard';

let host: HTMLDivElement | undefined;
afterEach(() => host?.remove());

it('renders completed real steps, conclusion, and follow-up action', async () => {
  host = document.createElement('div');
  document.body.append(host);
  const onAction = vi.fn();
  const root = createRoot(host);
  await act(async () => root.render(<PilotTaskCard title="安排面试准备" steps={[{ name: 'list_application_events' }]} presentation={{ conclusion: '优先准备周三技术一面。', actions: ['生成准备清单'], detailMarkdown: '' }} disabled={false} onAction={onAction} />));
  expect(host.querySelector('[aria-label="本轮任务：安排面试准备"]')).not.toBeNull();
  expect(host.textContent).toContain('已完成 1 步');
  await act(async () => host?.querySelector<HTMLButtonElement>('[aria-label="继续：生成准备清单"]')?.click());
  expect(onAction).toHaveBeenCalledWith('生成准备清单');
});

it('disables follow-up actions and labels a no-tool reply as advice整理', async () => {
  host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PilotTaskCard title="整理建议" steps={[]} presentation={{ conclusion: '先整理项目案例。', actions: ['生成准备清单'], detailMarkdown: '' }} disabled onAction={vi.fn()} />));
  expect(host.textContent).toContain('已完成建议整理');
  expect(host.querySelector<HTMLButtonElement>('[aria-label="继续：生成准备清单"]')?.disabled).toBe(true);
});
```

- [ ] **Step 2: Run the card test and verify red**

Run: `npm.cmd test -- --run src/components/ChatPanel/PilotTaskCard.render.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the card and embedded timeline label**

Create `PilotTaskCard.tsx` with this component boundary:

```tsx
interface Props {
  title: string;
  steps: ToolStep[];
  presentation?: TurnPresentation;
  disabled: boolean;
  onAction: (action: string) => void;
}

export default function PilotTaskCard({ title, steps, presentation, disabled, onAction }: Props) {
  const completed = steps.length ? `已完成 ${steps.length} 步` : '已完成建议整理';
  return (
    <article className={styles.taskCard} aria-label={`本轮任务：${title}`}>
      <header className={styles.taskHead}>
        <div><span className={styles.taskEyebrow}>本轮任务</span><h4>{title}</h4></div>
        <span className={styles.taskStatus}>{completed}</span>
      </header>
      {steps.length ? <ProcessTimeline steps={steps} summary={completed} embedded /> : null}
      {presentation ? <section className={styles.taskConclusion} aria-label="结论"><span>结论</span><p>{presentation.conclusion}</p></section> : null}
      {presentation?.actions.length ? <section className={styles.taskActions} aria-label="下一步"><span>下一步</span>{presentation.actions.map((action) => <button key={action} type="button" disabled={disabled} aria-label={`继续：${action}`} onClick={() => onAction(action)}>{action}</button>)}</section> : null}
    </article>
  );
}
```

Extend `ProcessTimeline` props with `summary?: string` and `embedded?: boolean`; use `summary ?? \`AI 做了什么 · 共 ${steps.length} 步\`` for the header. Add task-card CSS for a bordered single container, readable status text, keyboard-visible action buttons, and narrow-rail wrapping. Keep `ProcessTimeline` inside the card so its existing evidence disclosure remains the only evidence expansion path.

- [ ] **Step 4: Run rendered card and existing layout tests**

Run: `npm.cmd test -- --run src/components/ChatPanel/PilotTaskCard.render.test.tsx src/components/ChatPanel/layout.test.ts`

Expected: all card and existing layout tests pass.

- [ ] **Step 5: Commit the card slice**

```bash
git add web/package.json web/package-lock.json web/src/components/ChatPanel/PilotTaskCard.tsx web/src/components/ChatPanel/PilotTaskCard.render.test.tsx web/src/components/ChatPanel/ProcessTimeline.tsx web/src/components/ChatPanel/ChatPanel.module.css
git commit -m "feat: AI render Pilot task cards"
```

### Task 4: Integrate cards with message rendering and normal follow-ups

**Files:**
- Modify: `web/src/components/ChatPanel/MessageBubble.tsx:32-70`
- Create: `web/src/components/ChatPanel/MessageBubble.render.test.tsx`
- Modify: `web/src/components/ChatPanel/index.tsx:459`
- Modify: `web/src/components/ChatPanel/layout.test.ts`

- [ ] **Step 1: Write failing rendering and action-routing regressions**

Create a jsdom test that renders `MessageBubble` with a structured `UITurn` and verifies the conclusion appears once, the residual evidence Markdown appears once, and the action reaches the handler:

```tsx
const turn: UITurn = {
  role: 'assistant', taskTitle: '安排本周准备', steps: [],
  presentation: { conclusion: '优先准备技术一面。', actions: ['生成准备清单'], detailMarkdown: '依据：周三面试最近。' },
  content: '依据：周三面试最近。',
};
host = document.createElement('div');
document.body.append(host);
const onAction = vi.fn();
const root = createRoot(host);
await act(async () => root.render(<MessageBubble turn={turn} index={0} actionsDisabled={false} onAction={onAction} />));
expect(host.textContent?.match(/优先准备技术一面。/g)).toHaveLength(1);
expect(host.textContent?.match(/依据：周三面试最近。/g)).toHaveLength(1);
await act(async () => host?.querySelector<HTMLButtonElement>('[aria-label="继续：生成准备清单"]')?.click());
expect(onAction).toHaveBeenCalledWith('生成准备清单');
```

Extend `layout.test.ts` source assertions to require `PilotTaskCard`, `actionsDisabled`, and the exact follow-up prefix `继续处理：` in `index.tsx`.

- [ ] **Step 2: Run the rendering tests and verify red**

Run: `npm.cmd test -- --run src/components/ChatPanel/MessageBubble.render.test.tsx src/components/ChatPanel/layout.test.ts`

Expected: FAIL because `MessageBubble` does not accept action props and no card is rendered.

- [ ] **Step 3: Wire the card without bypassing composer guards**

Add these props to `MessageBubble`:

```tsx
interface Props {
  turn: UITurn;
  index: number;
  actionsDisabled: boolean;
  onAction: (action: string) => void;
}
```

Set `hasTaskCard = !isUser && Boolean(turn.steps?.length || turn.presentation)`. When true, render `PilotTaskCard` with `turn.taskTitle ?? '本轮任务'`, then render the Markdown bubble only when `turn.content.trim()` is non-empty. Render the legacy standalone `ProcessTimeline` only when `turn.steps` exists and `hasTaskCard` is false.

In `ChatPanel/index.tsx`, replace the current one-line `turns.map` with:

```tsx
turns.map((turn, i) => (
  <MessageBubble
    key={i}
    turn={turn}
    index={i}
    actionsDisabled={composerDisabled}
    onAction={(action) => void sendMessage(`继续处理：${action}`)}
  />
))
```

Do not add a direct reminder, mutation, or navigation handler. `sendMessage` already enforces loading, pending confirmation, and missing-key guards; any future write tool therefore continues through `ProposalCard`.

- [ ] **Step 4: Run focused frontend verification**

Run: `npm.cmd test -- --run src/components/ChatPanel/model.test.ts src/components/ChatPanel/PilotTaskCard.render.test.tsx src/components/ChatPanel/MessageBubble.render.test.tsx src/components/ChatPanel/layout.test.ts && npm.cmd exec tsc -- -b`

Expected: all focused tests pass and TypeScript reports no errors.

- [ ] **Step 5: Commit the integration slice**

```bash
git add web/src/components/ChatPanel/MessageBubble.tsx web/src/components/ChatPanel/MessageBubble.render.test.tsx web/src/components/ChatPanel/index.tsx web/src/components/ChatPanel/layout.test.ts
git commit -m "feat: AI connect Pilot task actions"
```

### Task 5: Verify behavior across the complete application

**Files:**
- Test: `tests/test_chat_api.py`
- Test: `web/src/components/ChatPanel/model.test.ts`
- Test: `web/src/components/ChatPanel/PilotTaskCard.render.test.tsx`
- Test: `web/src/components/ChatPanel/MessageBubble.render.test.tsx`

- [ ] **Step 1: Run the complete automated gate**

Run:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
cd web && npm.cmd test -- --run
cd web && npm.cmd run build
uv run oc smoke --static-dir web/dist
```

Expected: every command exits zero; report the existing TestClient deprecation warning separately if it remains the only warning.

- [ ] **Step 2: Walk through the card in the in-app browser**

Run the built app with the existing local configuration. Use a deterministic test conversation that returns a tool result followed by the required Markdown tail. Verify the card shows actual completed steps, one conclusion, follow-up buttons, keyboard focus, and the existing write-confirmation card after a write-oriented follow-up.

- [ ] **Step 3: Review diff scope and commit any verification-only correction**

Run: `git diff --check d0cd406..HEAD && git status --short`

Expected: no whitespace errors and no untracked generated files. If verification finds no correction, make no empty commit.

## Plan self-review

- Spec coverage: Tasks 1-4 implement the selected unified card, real-step derivation, Markdown contract, safe follow-ups, fallback, accessibility, and no-schema boundary. Task 5 covers full regressions and browser verification.
- Placeholder scan: no unresolved implementation or test instructions remain.
- Type consistency: `TurnPresentation`, `taskTitle`, `actionsDisabled`, `onAction`, `summary`, and `embedded` are defined before their consuming tasks and use the same names throughout.
