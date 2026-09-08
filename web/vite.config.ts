import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  worker: {
    format: 'es',
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.replace(/\\/g, '/');
          if (!normalized.includes('/node_modules/')) {
            return undefined;
          }
          if (normalized.includes('/react/') || normalized.includes('/react-dom/')) {
            return 'react';
          }
          if (normalized.includes('/@tanstack/react-query/') || normalized.includes('/axios/')) {
            return 'query';
          }
          if (normalized.includes('/react-markdown/') || normalized.includes('/remark-gfm/')) {
            return 'markdown';
          }
          if (normalized.includes('/dayjs/')) {
            return 'date';
          }
          return undefined;
        },
      },
    },
  },
});
