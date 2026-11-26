import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// One React project, six pages. The build goes into ../ui/app and FastAPI serves
//   /designer -> index.html, /server -> server.html, /admin -> admin.html, /analytics -> analytics.html
// (assets under /assets/app/). For `npm run dev`, API calls are proxied to uvicorn.
export default defineConfig({
  plugins: [react()],
  base: '/assets/app/',
  build: {
    outDir: '../ui/app',
    emptyOutDir: true,
    rolldownOptions: {
      input: {
        designer: resolve(import.meta.dirname, 'index.html'),
        server: resolve(import.meta.dirname, 'server.html'),
        admin: resolve(import.meta.dirname, 'admin.html'),
        analytics: resolve(import.meta.dirname, 'analytics.html'),
        home: resolve(import.meta.dirname, 'home.html'),
        embed: resolve(import.meta.dirname, 'embed.html'),
        query: resolve(import.meta.dirname, 'query.html'),
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
