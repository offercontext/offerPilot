import { describe, expect, it } from 'vitest';
import kanbanCard from './KanbanBoard/KanbanCard.tsx?raw';
import applicationList from './ApplicationListView.tsx?raw';
import applicationDetail from './ApplicationDetail.tsx?raw';
import offerCard from './OfferCard.tsx?raw';
import resumeCard from './ResumeCard.tsx?raw';

describe('application Pilot entry contract', () => {
  it('binds non-Kanban card roots directly to native Pilot attachment drag', () => {
    const cardSources = [applicationDetail, applicationList, offerCard, resumeCard];

    for (const source of cardSources) {
      expect(source).toContain('createPilotAttachmentDragBinding');
      expect(source).not.toMatch(/import PilotAttachmentHandle/);
      expect(source).not.toMatch(/<PilotAttachmentHandle/);
      expect(source).not.toContain('添加到 Pilot');
    }

    expect(offerCard).toContain("kind: 'offer'");
    expect(resumeCard).toContain("kind: 'resume'");
  });

  it('keeps Kanban card dragging inside dnd-kit instead of a native draggable binding', () => {
    expect(kanbanCard).toContain("import { useDraggable } from '@dnd-kit/core'");
    expect(kanbanCard).toContain('{...listeners}');
    expect(kanbanCard).toContain('{...attributes}');
    expect(kanbanCard).not.toContain('createPilotAttachmentDragBinding');
    expect(kanbanCard).not.toContain('applicationDragBinding');
    expect(kanbanCard).not.toContain('draggable: true');
    expect(kanbanCard).not.toContain('<PilotAttachmentHandle');
    expect(kanbanCard).not.toContain('问 Pilot');
  });
});
