import { fileURLToPath } from 'node:url';
const web = fileURLToPath(new URL('../../../web/', import.meta.url));
export default {
  root: web,
  define: { __PLAYWRIGHT_BUNDLE__: JSON.stringify(fileURLToPath(new URL('../node_modules/playwright-core/lib/coreBundle.js', import.meta.url))) },
  esbuild: { jsx: 'automatic' },
  resolve: { alias: { '@': `${web}src`, 'react': `${web}node_modules/react`, 'react-dom': `${web}node_modules/react-dom`, 'vitest': `${web}node_modules/vitest` } },
  test: { include: ['../desktop/real-ai-validation/test-ui/*.test.tsx'], environment: 'jsdom',
    pool: 'forks', minWorkers: 1, maxWorkers: 1 },
};
