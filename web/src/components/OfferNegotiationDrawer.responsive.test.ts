import { describe, expect, it } from 'vitest';

async function readSource(relativePath: string): Promise<string> {
  const fsModule = 'node:fs';
  const { readFileSync } = (await import(fsModule)) as {
    readFileSync: (path: URL, encoding: string) => string;
  };
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

describe('OfferNegotiationDrawer responsive container contract', () => {
  it('uses the drawer workspace width for the narrow Pilot header layout', async () => {
    const drawer = await readSource('./OfferNegotiationDrawer.tsx');
    const css = await readSource('./OfferNegotiationDrawer.module.css');
    expect(drawer).toContain('styles.workspace');
    expect(css).toContain('container-type: inline-size;');
    expect(css).toContain('container-name: negotiation-workspace;');
    expect(css).toContain('@container negotiation-workspace (max-width: 640px)');
    expect(css).toContain('flex-direction: column;');
  });
});
