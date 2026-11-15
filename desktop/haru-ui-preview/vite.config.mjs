import { defineConfig } from '../../web/node_modules/vite/dist/node/index.js';
import react from '../../web/node_modules/@vitejs/plugin-react/dist/index.js';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(root, '../../web');

// Separate input/output: this fixture never enters web's product build.
export default defineConfig({
  root,
  plugins: [react()],
  publicDir: path.join(web, 'public'),
  resolve: {
    alias: {
      '@': path.join(web, 'src'),
      react: path.join(web, 'node_modules/react'),
      'react-dom': path.join(web, 'node_modules/react-dom'),
    },
    dedupe: ['react', 'react-dom'],
  },
  build: { outDir: path.join(root, '.output'), emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { host: '127.0.0.1', port: 4177, strictPort: true, hmr: false },
  preview: { host: '127.0.0.1', port: 4177, strictPort: true },
});
