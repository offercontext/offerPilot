/** @vitest-environment jsdom */
import { act, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PilotTaskCard from './PilotTaskCard';
import type { ToolStep, TurnPresentation } from './model';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let root: Root | undefined;

function renderCard(props: ComponentProps<typeof PilotTaskCard>) {
  const container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  act(() => root?.render(<PilotTaskCard {...props} />));
  return container;
}

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  document.body.replaceChildren();
});

const presentation: TurnPresentation = {
  conclusion: '已完成岗位匹配分析，并标出两项需要补强的经历。',
  actions: ['继续完善简历'],
  detailMarkdown: '',
};

const completedStep: ToolStep = {
  name: 'list_applications',
  detail: '产品经理岗位',
  evidence: [
    {
      id: 'application-1',
      kind: 'application',
      title: 'OfferPilot',
      meta: '产品经理',
      snippet: '已投递，等待笔试通知',
      source: 'applications',
    },
  ],
};

describe('PilotTaskCard', () => {
  it('renders a completed task, conclusion, and action from real tool steps', () => {
    const onAction = vi.fn();
    const container = renderCard({
      title: '分析产品经理岗位匹配度',
      steps: [completedStep],
      presentation,
      disabled: false,
      onAction,
    });

    const card = container.querySelector('article[aria-label="本轮任务：分析产品经理岗位匹配度"]');
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain('分析产品经理岗位匹配度');
    expect(card?.textContent).toContain('已完成 1 步');
    expect(card?.textContent).toContain(presentation.conclusion);

    const action = card?.querySelector<HTMLButtonElement>('button[aria-label="继续：继续完善简历"]');
    expect(action).not.toBeNull();
    act(() => action?.click());
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith('继续完善简历');
  });

  it('renders a presentation-only status and disables its next action', () => {
    const onAction = vi.fn();
    const container = renderCard({
      title: '整理面试复盘',
      steps: [],
      presentation: {
        conclusion: '面试复盘已归档。',
        actions: ['继续准备下一轮'],
        detailMarkdown: '',
      },
      disabled: true,
      onAction,
    });

    const card = container.querySelector('article[aria-label="本轮任务：整理面试复盘"]');
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain('已完成建议整理');
    expect(card?.querySelector('[aria-label="AI 操作摘要"]')).toBeNull();

    const action = card?.querySelector<HTMLButtonElement>('button[aria-label="继续：继续准备下一轮"]');
    expect(action?.disabled).toBe(true);
    act(() => action?.click());
    expect(onAction).not.toHaveBeenCalled();
  });

  it('normalizes duplicate and blank next actions before rendering or dispatching', () => {
    const onAction = vi.fn();
    const container = renderCard({
      title: '准备本周求职安排',
      steps: [],
      presentation: {
        conclusion: '建议先完成准备清单。',
        actions: ['  生成准备清单  ', '', '生成准备清单', '查看日程'],
        detailMarkdown: '',
      },
      disabled: false,
      onAction,
    });

    const actions = Array.from(container.querySelectorAll<HTMLButtonElement>('section[aria-label="下一步"] button'));
    expect(actions).toHaveLength(2);
    expect(actions.map((action) => action.textContent)).toEqual(['生成准备清单', '查看日程']);
    expect(actions[0].getAttribute('aria-label')).toBe('继续：生成准备清单');

    act(() => actions[0].click());
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith('生成准备清单');
  });

  it('omits next actions when every action is blank', () => {
    const container = renderCard({
      title: '整理准备事项',
      steps: [],
      presentation: {
        conclusion: '暂时没有可继续的操作。',
        actions: [' ', '\n\t'],
        detailMarkdown: '',
      },
      disabled: false,
      onAction: vi.fn(),
    });

    expect(container.querySelector('section[aria-label="下一步"]')).toBeNull();
  });

  it('keeps the embedded process timeline expandable with its evidence', () => {
    const container = renderCard({
      title: '查看投递进度',
      steps: [completedStep],
      disabled: false,
      onAction: vi.fn(),
    });

    const card = container.querySelector('article');
    const timeline = card?.querySelector('[aria-label="AI 操作摘要"]');
    const toggle = timeline?.querySelector<HTMLButtonElement>('button');
    expect(timeline).not.toBeNull();
    expect(timeline?.className).toContain('timelineEmbedded');
    expect(toggle?.textContent).toContain('已完成 1 步');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');

    act(() => toggle?.click());
    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(timeline?.textContent).toContain('OfferPilot');
  });

  it('does not invent source or unknown-result state for an ordinary Pilot task', () => {
    const onAction = vi.fn();
    const container = renderCard({
      title: '业务查询',
      steps: [],
      presentation,
      disabled: false,
      onAction,
    });

    expect(container.textContent).not.toContain('已冻结来源');
    expect(container.textContent).not.toContain('结果待确认');
    const action = container.querySelector<HTMLButtonElement>('section[aria-label="下一步"] button');
    act(() => action?.click());
    expect(onAction).toHaveBeenCalledTimes(1);
  });
});
