import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        agent: resolve(__dirname, 'agent.html'),
        analytics: resolve(__dirname, 'analytics.html'),
        work: resolve(__dirname, 'work.html'),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/state': 'http://localhost:8787',
      '/events': 'http://localhost:8787',
      '/session': 'http://localhost:8787',
      '/health': 'http://localhost:8787',
      '/ws': {
        target: 'ws://localhost:8787',
        ws: true,
      },
      '/analytics/files': 'http://localhost:8787',
      '/workspace': 'http://localhost:8787',
    },
  },
});
