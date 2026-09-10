import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

export default mergeConfig(viteConfig, defineConfig({
  test: {
    // Bound AST/jsdom contention without relaxing the audit's 30-second budget.
    maxWorkers: 4,
    minWorkers: 1,
  },
}));
