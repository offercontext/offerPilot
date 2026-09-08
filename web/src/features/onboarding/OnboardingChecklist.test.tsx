// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import OnboardingChecklist from './OnboardingChecklist';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('OnboardingChecklist', () => {
  it('shows all four milestones and progress', () => {
    const html = renderToStaticMarkup(
      <OnboardingChecklist
        status={{
          steps: {
            configure_ai: true,
            create_primary_resume: false,
            create_first_application: false,
            send_first_pilot_message: false,
          },
          completed_count: 1,
          is_complete: false,
          force_open: false,
        }}
        onCollapse={vi.fn()}
        onAction={vi.fn()}
      />,
    );
    expect(html).toContain('1 / 4');
    expect(html).toContain('<button');
    expect(html).toContain('data-onboarding-action="configure_ai"');
    expect(html).toContain('data-onboarding-action="create_primary_resume"');
    expect(html).toContain('data-onboarding-action="create_first_application"');
    expect(html).toContain('data-onboarding-action="send_first_pilot_message"');
    expect(html).toContain('配置 AI');
    expect(html).toContain('创建主简历');
    expect(html).toContain('添加第一条投递');
    expect(html).toContain('向 Pilot 发出第一条消息');
  });

  it('forwards each completed card click in action order', () => {
    const actions: string[] = [];
    const container = document.createElement('div');
    const root = createRoot(container);

    act(() => {
      root.render(
        <OnboardingChecklist
          status={{
            steps: {
              configure_ai: true,
              create_primary_resume: true,
              create_first_application: true,
              send_first_pilot_message: true,
            },
            completed_count: 4,
            is_complete: true,
            force_open: true,
          }}
          onCollapse={vi.fn()}
          onAction={(action) => actions.push(action)}
        />,
      );
    });

    const cards = container.querySelectorAll<HTMLButtonElement>('button[data-onboarding-action]');
    expect(cards).toHaveLength(4);

    act(() => {
      cards.forEach((card) => card.click());
    });

    expect(actions).toEqual([
      'configure_ai',
      'create_primary_resume',
      'create_first_application',
      'send_first_pilot_message',
    ]);

    act(() => root.unmount());
  });
});
