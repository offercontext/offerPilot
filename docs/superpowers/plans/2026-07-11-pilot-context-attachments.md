# Pilot Context Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Let users attach application, offer, and resume cards to the active Pilot conversation through a safe drag/drop or menu action, then use fill-only context-aware quick questions.

**Architecture:** Add a reference-only PilotContextAttachment request field and resolve current records server-side. A shared draft-attachment provider connects business cards to ChatPanel without persisting card payloads. Use native HTML drag events for the dedicated attachment handle so existing Kanban dnd-kit status drag remains unchanged; provide a menu/button alternative for keyboard and touch users.

**Tech Stack:** React 18, TypeScript, Ant Design, native HTML drag events, TanStack Query, FastAPI, SQLAlchemy, pytest, Vitest.

---

## File Structure

- web/src/types/chat.ts: attachment request type.
- web/src/lib/pilotAttachments.ts: identity, bounded draft reducer, and deterministic quick-question selection.
- web/src/features/pilot/PilotAttachmentContext.tsx: active-draft provider shared by cards and ChatPanel.
- web/src/components/PilotAttachmentHandle.tsx: dedicated native drag handle plus accessible add action.
- web/src/components/ChatPanel/ContextAttachmentRail.tsx: drop target, removable chips, and attachment status.
- src/offerpilot/api.py: validates references and injects resolved current records into JSON/SSE model context.

### Task 1: Define bounded attachment state and quick-question selection

**Files:**
- Modify: web/src/types/chat.ts
- Create: web/src/lib/pilotAttachments.ts
- Create: web/src/lib/pilotAttachments.test.ts

- [ ] **Step 1: Write failing pure-state tests**

    it('dedupes by kind and id and caps the draft at five', () => {
      const state = addPilotAttachment(emptyPilotAttachmentDraft(), application('1'));
      expect(addPilotAttachment(state, application('1')).attachments).toHaveLength(1);
      expect(addPilotAttachment(withFiveAttachments(), resume('6')).notice).toBe('最多添加 5 个上下文对象');
    });

    it('creates fill-only suggestions from an application and resume', () => {
      expect(pilotQuickQuestions([application('1'), resume('2')])).toEqual([
        '分析简历与岗位的匹配差距',
        '给出最值得修改的三处',
        '生成自我介绍提纲',
      ]);
    });

- [ ] **Step 2: Run the tests to verify they fail**

Run: cd web && npm.cmd test -- --run src/lib/pilotAttachments.test.ts

Expected: FAIL because the attachment type and helper module do not exist.

- [ ] **Step 3: Implement the minimal reference-only contract**

    export type PilotAttachmentKind = 'application' | 'offer' | 'resume';

    export interface PilotContextAttachment {
      kind: PilotAttachmentKind;
      id: string;
      label: string;
    }

    export const PILOT_ATTACHMENT_LIMIT = 5;

    export function pilotAttachmentKey(item: PilotContextAttachment) {
      return item.kind + ':' + item.id;
    }

Implement addPilotAttachment, removePilotAttachment, and pilotQuickQuestions. Preserve first encounter order; return a visible limit notice rather than silently dropping an attachment; never generate a write-oriented suggestion.

- [ ] **Step 4: Run the tests to verify they pass**

Run: cd web && npm.cmd test -- --run src/lib/pilotAttachments.test.ts

Expected: PASS.

- [ ] **Step 5: Commit**

    git add web/src/types/chat.ts web/src/lib/pilotAttachments.ts web/src/lib/pilotAttachments.test.ts
    git commit -m "feat: AI add Pilot attachment draft state"

### Task 2: Scope attachment drafts to the active conversation

**Files:**
- Create: web/src/features/pilot/PilotAttachmentContext.tsx
- Create: web/src/features/pilot/PilotAttachmentContext.test.tsx
- Modify: web/src/layout/AppShell.tsx
- Modify: web/src/components/ChatPanel/index.tsx
- Test: web/src/layout/AppShell.test.ts

- [ ] **Step 1: Write failing provider tests**

    it('keeps attachments scoped to the active conversation key', () => {
      const { result } = renderPilotAttachmentHook();
      act(() => result.current.setActiveConversationKey('conversation:7'));
      act(() => result.current.addAttachment(application('7')));
      act(() => result.current.setActiveConversationKey('conversation:8'));
      expect(result.current.attachments).toEqual([]);
    });

- [ ] **Step 2: Run the tests to verify they fail**

Run: cd web && npm.cmd test -- --run src/features/pilot/PilotAttachmentContext.test.tsx src/layout/AppShell.test.ts

Expected: FAIL because no provider exists.

- [ ] **Step 3: Implement the provider boundary**

Expose setActiveConversationKey, attachments, addAttachment, removeAttachment, and clearAttachments. Keep drafts in a map keyed by conversation:<id> or new:<requestKey>; never share attachments between keys. AppShell wraps business views and ChatPanel, and the accessible add action opens Pilot before adding an attachment.

ChatPanel sets the key whenever convID changes or a new startRequest.requestKey begins. Clear only the active key after sendMessage returns true.

- [ ] **Step 4: Run provider and shell tests**

Run: cd web && npm.cmd test -- --run src/features/pilot/PilotAttachmentContext.test.tsx src/layout/AppShell.test.ts

Expected: PASS.

- [ ] **Step 5: Commit**

    git add web/src/features/pilot/PilotAttachmentContext.tsx web/src/features/pilot/PilotAttachmentContext.test.tsx web/src/layout/AppShell.tsx web/src/layout/AppShell.test.ts web/src/components/ChatPanel/index.tsx
    git commit -m "feat: AI scope Pilot attachments to active conversations"

### Task 3: Build the attachment rail and fill-only quick questions

**Files:**
- Create: web/src/components/ChatPanel/ContextAttachmentRail.tsx
- Modify: web/src/components/ChatPanel/Composer.tsx
- Modify: web/src/components/ChatPanel/ChatPanel.module.css
- Modify: web/src/components/ChatPanel/index.tsx
- Test: web/src/components/ChatPanel/layout.test.ts

- [ ] **Step 1: Write failing layout tests**

    it('renders an attachment drop target with removable chips and fill-only suggestions', () => {
      expect(component).toContain('<ContextAttachmentRail');
      expect(composer).toContain('onSuggestionSelect');
      expect(composer).toContain('setValue(question)');
      expect(composer).not.toContain('void onSend(question)');
    });

- [ ] **Step 2: Run the tests to verify they fail**

Run: cd web && npm.cmd test -- --run src/components/ChatPanel/layout.test.ts

Expected: FAIL because the attachment rail does not exist.

- [ ] **Step 3: Implement the rail and composer contract**

ContextAttachmentRail accepts attachments, disabled, onRemove, and onNativeDrop. It reads only application/x-offerpilot-context-attachment from dataTransfer, calls preventDefault only after valid JSON parsing, and exposes removal controls with specific accessible labels.

Extend Composer with suggestions and onSuggestionSelect. A suggestion calls setValue(question), never onSend(question); hide suggestions while text is non-empty or the composer is disabled.

- [ ] **Step 4: Run the ChatPanel tests**

Run: cd web && npm.cmd test -- --run src/components/ChatPanel/layout.test.ts src/components/ChatPanel/model.test.ts

Expected: PASS.

- [ ] **Step 5: Commit**

    git add web/src/components/ChatPanel/ContextAttachmentRail.tsx web/src/components/ChatPanel/Composer.tsx web/src/components/ChatPanel/index.tsx web/src/components/ChatPanel/ChatPanel.module.css web/src/components/ChatPanel/layout.test.ts
    git commit -m "feat: AI add Pilot attachment rail and suggestions"

### Task 4: Serialize and resolve reference attachments safely

**Files:**
- Modify: web/src/services/chat.ts
- Modify: web/src/services/chat.test.ts
- Modify: src/offerpilot/api.py
- Modify: tests/test_chat_api.py

- [ ] **Step 1: Write failing API and client tests**

    @pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
    def test_chat_resolves_attachment_references_server_side(tmp_path, endpoint):
        response = client.post(endpoint, json={
            "message": "compare these",
            "conversation_id": 0,
            "attachments": [{"kind": "application", "id": "1", "label": "forged client label"}],
        })
        assert response.status_code == 200
        assert "real company name" in captured_model.system_context()
        assert "forged client label" not in captured_model.system_context()

    expect(JSON.parse(fetchMock.mock.calls[0][1].body).attachments).toEqual([
      { kind: 'resume', id: '3', label: 'Backend resume' },
    ]);

- [ ] **Step 2: Run the tests to verify they fail**

Run: uv run pytest tests/test_chat_api.py -k attachment -v

Run: cd web && npm.cmd test -- --run src/services/chat.test.ts

Expected: FAIL because neither request path accepts attachments.

- [ ] **Step 3: Implement source-of-truth resolution**

Extend ChatContextInput with attachments and serialize it identically for JSON and SSE. Add _normalize_chat_attachments in api.py: accept only one to five application/offer/resume references, reject duplicate kind-and-id pairs, malformed IDs, and unsupported kinds with HTTP 422.

Add _chat_attachment_messages(attachments, applications, offers, resumes). Resolve every ID through existing repositories and inject only current server-side fields required for reasoning. Missing records produce a bounded warning, not model instructions. Insert these messages after _chat_page_context_messages in both chat endpoints.

- [ ] **Step 4: Run API and client tests**

Run: uv run pytest tests/test_chat_api.py -k attachment -v

Run: cd web && npm.cmd test -- --run src/services/chat.test.ts

Expected: PASS.

- [ ] **Step 5: Commit**

    git add src/offerpilot/api.py tests/test_chat_api.py web/src/services/chat.ts web/src/services/chat.test.ts
    git commit -m "feat: AI resolve Pilot attachment references safely"

### Task 5: Expose application, offer, and resume cards safely

**Files:**
- Create: web/src/components/PilotAttachmentHandle.tsx
- Create: web/src/components/PilotAttachmentHandle.test.tsx
- Modify: web/src/components/ApplicationDetail.tsx
- Modify: web/src/components/ApplicationListView.tsx
- Modify: web/src/components/KanbanBoard/KanbanCard.tsx
- Modify: web/src/components/KanbanBoard/KanbanColumn.tsx
- Modify: web/src/components/KanbanBoard/index.tsx
- Modify: web/src/components/OfferCard.tsx
- Modify: web/src/components/OfferCenterView.tsx
- Modify: web/src/components/ResumeCard.tsx
- Modify: web/src/components/ResumeLibraryView.tsx
- Modify: web/src/components/applicationPilotEntry.test.ts

- [ ] **Step 1: Write failing source-entry tests**

    it('exposes an accessible Pilot attachment action for applications, offers, and resumes', () => {
      expect(applicationDetail).toContain('PilotAttachmentHandle');
      expect(offerCard).toContain("kind: 'offer'");
      expect(resumeCard).toContain("kind: 'resume'");
      expect(kanbanCard).toContain('onAttachToPilot');
    });

- [ ] **Step 2: Run the tests to verify they fail**

Run: cd web && npm.cmd test -- --run src/components/applicationPilotEntry.test.ts src/components/PilotAttachmentHandle.test.tsx

Expected: FAIL because no reusable attachment handle exists.

- [ ] **Step 3: Implement a dedicated native attachment handle**

    <button
      type="button"
      draggable
      aria-label={'添加' + attachment.label + '到 Pilot 上下文'}
      onDragStart={(event) =>
        event.dataTransfer.setData(
          'application/x-offerpilot-context-attachment',
          JSON.stringify(attachment),
        )
      }
      onClick={() => onAttach(attachment)}
    >
      添加到 Pilot
    </button>

Pass onAttachToPilot through each supported card path. In KanbanCard, place the native handle outside the dnd-kit status-drag activator, so normal card drag still changes status and only the handle creates an attachment. Do not add handles to aggregate cards, Kanban columns, or unsaved forms.

- [ ] **Step 4: Run card-entry tests**

Run: cd web && npm.cmd test -- --run src/components/applicationPilotEntry.test.ts src/components/PilotAttachmentHandle.test.tsx

Expected: PASS.

- [ ] **Step 5: Commit**

    git add web/src/components/PilotAttachmentHandle.tsx web/src/components/PilotAttachmentHandle.test.tsx web/src/components/ApplicationDetail.tsx web/src/components/ApplicationListView.tsx web/src/components/KanbanBoard web/src/components/OfferCard.tsx web/src/components/OfferCenterView.tsx web/src/components/ResumeCard.tsx web/src/components/ResumeLibraryView.tsx web/src/components/applicationPilotEntry.test.ts
    git commit -m "feat: AI attach application offer and resume cards"

### Task 6: Verify recovery prerequisite and release behavior

**Files:**
- Modify: web/src/components/ChatPanel/layout.test.ts or an adjacent behavior test with a real select/reload harness.
- Modify: docs/p0-release-checklist.md only if the release gate changes.

- [ ] **Step 1: Add the persisted-reply recovery regression test**

Load a persisted user/assistant conversation, invoke the thread selection callback, and assert the assistant message reaches MessageBubble instead of the empty-state greeting. Keep the test independent from attachments.

- [ ] **Step 2: Run the recovery test before enabling attachment release**

Run: cd web && npm.cmd test -- --run src/components/ChatPanel/layout.test.ts src/components/ChatPanel/model.test.ts

Expected: the regression test initially fails; after the narrow selection/state-transition repair it passes without changing attachment semantics.

- [ ] **Step 3: Run the full release gate**

    uv run pytest
    uv run ruff check .
    uv run mypy src
    cd web && npm.cmd test -- --run
    cd web && npm.cmd run build
    uv run oc smoke --static-dir web/dist

Expected: all commands exit 0. Report the TestClient deprecation warning if it remains.

- [ ] **Step 4: Run a real browser acceptance pass**

1. Add an application, offer, and resume through the accessible action.
2. Drag one supported card through its dedicated handle into visible Pilot.
3. Confirm chips are removable, the sixth card is rejected, and a Kanban status drag still works.
4. Click a quick question and confirm it fills but does not send.
5. Send a read-only prompt and verify resolved server-side attachment context affects the answer.
6. Switch conversations and confirm draft attachments do not leak.

- [ ] **Step 5: Commit and request code review**

    git add docs/p0-release-checklist.md tests/test_chat_api.py web/src/components/ChatPanel web/src/layout/AppShell.tsx web/src/features/pilot
    git commit -m "test: AI verify Pilot context attachments"

Request an independent review focused on attachment privacy boundaries, drag collision behavior, and cross-conversation isolation before merge.
