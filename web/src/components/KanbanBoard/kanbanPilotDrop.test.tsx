// @vitest-environment jsdom
import { act } from 'react';
import { createPortal } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Application } from '@/types/application';
import { updateApplication } from '@/services/applications';
import KanbanBoard from './index';
import PilotContextDropTarget from './PilotContextDropTarget';
import { PILOT_CONTEXT_DROP_ID } from './applicationLifecycle';

const dnd = vi.hoisted(() => ({
  monitor: undefined as any,
  dropTargets: [] as string[],
  pointerDown: vi.fn(),
}));

vi.mock('@dnd-kit/core', () => ({
  DndContext: ({ children }: { children: React.ReactNode }) => children,
  DragOverlay: ({ children }: { children: React.ReactNode }) => children,
  useDndMonitor: (monitor: any) => {
    dnd.monitor = monitor;
  },
  useDraggable: () => ({
    attributes: { 'data-dnd-kit-draggable': 'true' },
    listeners: { onPointerDown: dnd.pointerDown },
    setNodeRef: () => undefined,
  }),
  useDroppable: ({ id }: { id: string }) => {
    dnd.dropTargets.push(id);
    return { isOver: false, setNodeRef: () => undefined };
  },
}));

vi.mock('@/services/applications', () => ({
  updateApplication: vi.fn(),
  deleteApplication: vi.fn(),
}));

vi.mock('antd', () => ({
  Button: ({ children, ...props }: any) => <button {...props}>{children}</button>,
  Input: { TextArea: (props: any) => <textarea {...props} /> },
  Modal: ({ open, children, onOk }: any) =>
    open ? (
      <div role="dialog">
        {children}
        <button data-testid="confirm-status" onClick={() => void onOk?.()}>Confirm</button>
      </div>
    ) : null,
  Popconfirm: ({ children }: any) => <>{children}</>,
  Select: () => <select />,
  Typography: { Paragraph: ({ children }: any) => <p>{children}</p> },
  message: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('@ant-design/icons', () => ({
  DeleteOutlined: () => null,
  RightOutlined: () => null,
  RobotOutlined: () => null,
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const application: Application = {
  id: 7,
  company_name: 'ByteDance',
  position_name: 'Backend',
  job_url: '',
  status: 'applied',
  source: 'web',
  notes: '',
  applied_at: '2026-07-01T09:00:00+08:00',
  first_pending_at: null,
  first_applied_at: '2026-07-01T09:00:00+08:00',
  first_written_test_at: null,
  first_interview_at: null,
  first_offer_at: null,
  closed_reason: '',
  closed_at: null,
  deleted_at: null,
  created_at: '2026-07-01T09:00:00+08:00',
  updated_at: '2026-07-02T09:00:00+08:00',
};

let container: HTMLDivElement | undefined;
let portal: HTMLDivElement | undefined;
let root: Root | undefined;

function renderBoard({ drawerTarget = false } = {}) {
  container = document.createElement('div');
  portal = document.createElement('div');
  document.body.append(container, portal);
  const onAttachToPilot = vi.fn();
  root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  act(() => {
    root?.render(
      <QueryClientProvider client={client}>
        <KanbanBoard applications={[application]} onAttachToPilot={onAttachToPilot} />
        {drawerTarget && createPortal(
          <PilotContextDropTarget><div>Drawer Pilot</div></PilotContextDropTarget>,
          portal!,
        )}
      </QueryClientProvider>,
    );
  });

  return { onAttachToPilot };
}

function startAndDrop(overId: string | null) {
  act(() => dnd.monitor.onDragStart({ active: { id: application.id } }));
  act(() => dnd.monitor.onDragEnd({ active: { id: application.id }, over: overId ? { id: overId } : null }));
}

beforeEach(() => {
  dnd.monitor = undefined;
  dnd.dropTargets = [];
  dnd.pointerDown.mockReset();
  vi.mocked(updateApplication).mockResolvedValue({} as never);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  portal?.remove();
  root = undefined;
  container = undefined;
  portal = undefined;
  vi.clearAllMocks();
});

describe('Kanban Pilot dnd-kit routing', () => {
  it('renders the whole Kanban card with dnd-kit bindings and no native draggable attribute', () => {
    renderBoard();

    const card = container?.querySelector<HTMLDivElement>('[data-dnd-kit-draggable="true"]');
    expect(card).not.toBeNull();
    expect(card?.getAttribute('draggable')).toBeNull();
    act(() => card?.dispatchEvent(new Event('pointerdown', { bubbles: true })));
    expect(dnd.pointerDown).toHaveBeenCalledTimes(1);
    expect(dnd.monitor.onDragStart).toEqual(expect.any(Function));
  });

  it('drops a whole card on a status column without attaching it to Pilot', async () => {
    const { onAttachToPilot } = renderBoard();

    startAndDrop('interview');
    const confirm = container?.querySelector<HTMLButtonElement>('[data-testid="confirm-status"]');
    expect(confirm).not.toBeNull();
    await act(async () => confirm?.click());

    expect(updateApplication).toHaveBeenCalledWith(
      application.id,
      expect.objectContaining({ status: 'interview' }),
    );
    expect(onAttachToPilot).not.toHaveBeenCalled();
  });

  it('drops a card on a visible drawer portal target without changing its status', () => {
    const { onAttachToPilot } = renderBoard({ drawerTarget: true });

    expect(dnd.dropTargets).toContain(PILOT_CONTEXT_DROP_ID);
    startAndDrop(PILOT_CONTEXT_DROP_ID);

    expect(onAttachToPilot).toHaveBeenCalledWith({
      kind: 'application',
      id: String(application.id),
      label: 'ByteDance · Backend',
    });
    expect(updateApplication).not.toHaveBeenCalled();
    expect(container?.querySelector('[role="dialog"]')).toBeNull();
  });

  it('cannot attach a card when the Pilot target is hidden', () => {
    const { onAttachToPilot } = renderBoard();

    expect(dnd.dropTargets).not.toContain(PILOT_CONTEXT_DROP_ID);
    startAndDrop(null);

    expect(onAttachToPilot).not.toHaveBeenCalled();
    expect(updateApplication).not.toHaveBeenCalled();
  });
});
