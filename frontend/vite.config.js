import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// One React project, four pages. The build goes into ../ui/app and FastAPI serves
//   /designer -> index.html, /server -> server.html, /admin -> admin.html, /analytics -> analytics.html
// (assets under /assets/app/). For `npm run dev`, API calls are proxied to uvicorn.
export default defineConfig({
  plugins: [react()],
  base: '/assets/app/',
  build: {
    outDir: '../ui/app',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        designer: resolve(__dirname, 'index.html'),
        server: resolve(__dirname, 'server.html'),
        admin: resolve(__dirname, 'admin.html'),
        analytics: resolve(__dirname, 'analytics.html'),
        query: resolve(__dirname, 'query.html'),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/generate': 'http://127.0.0.1:8000',
      '/reports': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
});
