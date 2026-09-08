// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mutationMutate = vi.hoisted(() => vi.fn());

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: [], isLoading: false, refetch: vi.fn() }),
  useMutation: () => ({ isPending: false, mutate: mutationMutate }),
}));
vi.mock('@/services/resumes', () => ({
  listResumes: vi.fn(), createResume: vi.fn(), matchResume: vi.fn(), uploadResume: vi.fn(),
}));
vi.mock('./ResumeUploadModal', () => ({ default: () => null }));
vi.mock('@ant-design/icons', () => ({ RobotOutlined: () => null, PlusOutlined: () => null, UploadOutlined: () => null }));
vi.mock('antd', () => {
  const Input = Object.assign(
    (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
    { TextArea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} /> },
  );
  return {
    Modal: (props: { open: boolean; children: ReactNode; footer?: ReactNode }) => props.open ? <div role="dialog">{props.children}{props.footer}</div> : null,
    Input,
    Button: ({ loading: _loading, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button {...props} />,
    Select: () => <select aria-label="选择已保存的简历" />,
    message: { success: vi.fn(), error: vi.fn() },
    Spin: () => <span>loading</span>,
    Progress: () => <span>progress</span>,
    Divider: () => <hr />,
    Empty: Object.assign((props: { description?: ReactNode }) => <div>{props.description}</div>, { PRESENTED_IMAGE_SIMPLE: null }),
  };
});

const { default: ResumeMatchModal } = await import('./ResumeMatchModal');

let root: Root | undefined;
let container: HTMLDivElement | undefined;

beforeEach(() => {
  mutationMutate.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
});

describe('ResumeMatchModal', () => {
  it('mounts a stable result surface without starting a request', () => {
    act(() => root?.render(<ResumeMatchModal open onClose={vi.fn()} />));

    expect(container?.querySelector('[data-testid="resume-match-result-surface"]')).not.toBeNull();
    expect(mutationMutate).not.toHaveBeenCalled();
  });
});
