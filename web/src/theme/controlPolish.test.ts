import { describe, expect, it } from 'vitest';
import { darkTheme, lightTheme } from './antdTheme';

async function loadTokens(): Promise<string> {
  const fsModule = 'node:fs';
  const { readFileSync } = (await import(fsModule)) as {
    readFileSync: (path: URL, encoding: string) => string;
  };
  return readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
}

async function loadWorkflowSurface(): Promise<string> {
  const fsModule = 'node:fs';
  const { readFileSync } = (await import(fsModule)) as {
    readFileSync: (path: URL, encoding: string) => string;
  };
  return readFileSync(new URL('../components/ui/WorkflowSurface.module.css', import.meta.url), 'utf8');
}

describe('control polish theme contract', () => {
  it('defines shared control tokens for both light and dark themes', async () => {
    const css = await loadTokens();

    const lightButton = lightTheme.components?.Button as { controlHeight?: number; borderRadius?: number };
    const darkButton = darkTheme.components?.Button as { controlHeight?: number; borderRadius?: number };
    const lightInput = lightTheme.components?.Input as { controlHeight?: number; borderRadius?: number };
    const darkInput = darkTheme.components?.Input as { controlHeight?: number; borderRadius?: number };
    const lightSelect = lightTheme.components?.Select as { controlHeight?: number; borderRadius?: number };
    const darkSelect = darkTheme.components?.Select as { controlHeight?: number; borderRadius?: number };

    expect(css).toContain('--op-control-height');
    expect(css).toContain('--op-control-radius');
    expect(css).toContain('--op-focus-ring');
    expect(lightButton.controlHeight).toBe(36);
    expect(darkButton.controlHeight).toBe(36);
    expect(lightInput.controlHeight).toBe(36);
    expect(darkInput.controlHeight).toBe(36);
    expect(lightSelect.controlHeight).toBe(36);
    expect(darkSelect.controlHeight).toBe(36);
    expect(lightButton.borderRadius).toBe(8);
    expect(darkButton.borderRadius).toBe(8);
    expect(lightInput.borderRadius).toBe(8);
    expect(darkInput.borderRadius).toBe(8);
    expect(lightSelect.borderRadius).toBe(8);
    expect(darkSelect.borderRadius).toBe(8);
  });

  it('defines shared workflow surfaces with responsive long-text safety', async () => {
    const css = await loadTokens();

    expect(css).toContain('.op-control-toolbar');
    expect(css).toContain('.op-inline-status');
    expect(css).toContain('.op-empty-state');
    expect(css).toContain('.op-long-text');
    expect(css).toContain('overflow-wrap: anywhere');
    expect(css).toContain('@media (max-width: 720px)');
  });

  it('uses only defined theme tokens for native control interaction states', async () => {
    const [tokens, workflowCss] = await Promise.all([loadTokens(), loadWorkflowSurface()]);
    const declarations = new Set(
      [...tokens.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((match) => match[1]),
    );
    const references = new Set(
      [...workflowCss.matchAll(/var\((--[a-z0-9-]+)/gi)].map((match) => match[1]),
    );
    const undefinedReferences = [...references].filter((token) => !declarations.has(token));

    expect(undefinedReferences).toEqual([]);
    expect(workflowCss).toContain('box-shadow: var(--op-focus-ring)');
  });

  it('keeps the left navigation fixed while the main content owns vertical scrolling', async () => {
    const css = await loadTokens();

    expect(css).toMatch(/\.op-app-shell\s*\{[^}]*height:\s*100dvh;[^}]*overflow:\s*hidden;/s);
    expect(css).toMatch(/\.op-app-main\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/s);
    expect(css).toMatch(/\.op-app-content\s*\{[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/s);
  });
});
