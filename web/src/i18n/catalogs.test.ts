import { describe, expect, it } from 'vitest';
import ts from 'typescript';

const catalogSources = import.meta.glob<string>('./catalogs/*.ts', { eager: true, query: '?raw', import: 'default' });
const productionSources = import.meta.glob<string>(['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts', '!../**/*.test.tsx', '!./**/*'], { eager: true, query: '?raw', import: 'default' });
const han = /\p{Script=Han}/u;
const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
const parse = (path: string, source: string) => ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
const literal = (node: ts.Node | undefined): node is ts.StringLiteral | ts.NoSubstitutionTemplateLiteral => Boolean(node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)));
function traverse(node: ts.Node, visit: (node: ts.Node) => void) { visit(node); ts.forEachChild(node, (child) => traverse(child, visit)); }
function position(source: ts.SourceFile, node: ts.Node) { return `${source.fileName}:${source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1}`; }
function catalogEntries() {
  const entries: Array<{ key: string; value: string; at: string }> = [];
  for (const [path, source] of Object.entries(catalogSources)) {
    const parsed = parse(path, source);
    traverse(parsed, (node) => {
      if (ts.isPropertyAssignment(node) && literal(node.name) && literal(node.initializer)) {
        entries.push({ key: node.name.text, value: node.initializer.text, at: position(parsed, node) });
      }
    });
  }
  return entries;
}

describe('authored UI translation coverage', () => {
  it('has unambiguous English catalogs and preserves named interpolation slots', () => {
    const seen = new Map<string, string>();
    const defects: string[] = [];
    for (const { key, value, at } of catalogEntries()) {
      if (seen.has(key) && seen.get(key) !== value) defects.push(`${at}: conflicting translation for ${key}`);
      seen.set(key, value);
      if (!value.trim() || han.test(value)) defects.push(`${at}: missing English translation for ${key}`);
      // Context-only identifiers get their source and placeholder validation at their call site below.
      if (han.test(key) && JSON.stringify(placeholders(key)) !== JSON.stringify(placeholders(value))) defects.push(`${at}: changed placeholders for ${key}`);
    }
    expect(defects).toEqual([]);
    expect(seen.size).toBeGreaterThan(3000);
  });

  it('covers literal translation requests and prevents new raw Chinese JSX copy', () => {
    const english = new Map(catalogEntries().map(({ key, value }) => [key, value]));
    const defects: string[] = [];
    for (const [path, source] of Object.entries(productionSources)) {
      const parsed = parse(path, source);
      traverse(parsed, (node) => {
        if (ts.isJsxText(node) && han.test(node.text)) defects.push(`${position(parsed, node)}: raw JSX text ${node.text.trim()}`);
        if (ts.isJsxAttribute(node) && literal(node.initializer) && han.test(node.initializer.text)) defects.push(`${position(parsed, node)}: raw JSX attribute ${node.name.getText(parsed)}`);
        if (!(ts.isCallExpression(node) || ts.isNewExpression(node))) return;
        const name = node.expression.getText(parsed);
        if (!['t', 'UiTextError', 'translateKnownLabel'].includes(name)) return;
        const key = node.arguments?.[0];
        if (!literal(key) || !han.test(key.text)) return;
        const context = name === 't' ? node.arguments?.[2] : undefined;
        const translated = (literal(context) ? english.get(context.text) : undefined) ?? english.get(key.text);
        if (translated === undefined) defects.push(`${position(parsed, node)}: missing translation for ${key.text}`);
        else if (JSON.stringify(placeholders(key.text)) !== JSON.stringify(placeholders(translated))) defects.push(`${position(parsed, node)}: source/context placeholder mismatch for ${key.text}`);
      });
    }
    expect(defects).toEqual([]);
    expect(Object.keys(productionSources).length).toBeGreaterThan(250);
  });
});
