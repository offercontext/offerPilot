// Render the production card and catalogs, rather than parsing implementation syntax.
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const fields = JSON.parse(readFileSync(0, 'utf8'));
const server = await createServer({
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
});
try {
  const { default: ProposalCard } = await server.ssrLoadModule('/src/components/ChatPanel/ProposalCard.tsx');
  const { default: styles } = await server.ssrLoadModule('/src/components/ChatPanel/ChatPanel.module.css');
  const { setLocale } = await server.ssrLoadModule('/src/i18n/index.ts');
  const labels = {};
  for (const locale of ['zh-CN', 'en-US', 'zh-CN']) {
    await setLocale(locale);
    const html = renderToStaticMarkup(createElement(ProposalCard, {
      action: {
        tool_name: 'update_note', human: '用户原文 / User content',
        args: {}, confirmation_token: 'label-contract',
        proposed_changes: fields.map((field) => ({ field, before: 'contract-before', after: '用户原文 / User content' })),
      },
      loading: false, evidence: [], onConfirm() {}, onCancel() {},
    }));
    const dom = new JSDOM(html);
    try {
      const rows = [...dom.window.document.getElementsByClassName(styles.changeRow)];
      if (rows.length !== fields.length) throw new Error(`Expected ${fields.length} visible field rows, got ${rows.length}`);
      const rendered = Object.fromEntries(rows.map((row, index) => {
        if (fields[index] === '用户自定义字段 / custom_field' && row.querySelector('b:last-child')?.textContent !== '用户原文 / User content') {
          throw new Error(`User content changed for ${fields[index]} in ${locale}`);
        }
        return [fields[index], row.querySelector('span')?.textContent];
      }));
      if (labels[locale] && JSON.stringify(labels[locale]) !== JSON.stringify(rendered)) {
        throw new Error(`Labels did not restore after switching back to ${locale}`);
      }
      labels[locale] = rendered;
    } finally {
      dom.window.close();
    }
  }
  process.stdout.write(`CONFIRMATION_LABELS=${JSON.stringify(labels)}\n`);
} finally {
  await server.close();
}
