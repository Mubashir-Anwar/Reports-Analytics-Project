import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Build goes into ../ui/designer and is served by FastAPI at /designer
// (assets under /assets/designer/). For `npm run dev`, API calls are proxied to uvicorn.
export default defineConfig({
  plugins: [react()],
  base: '/assets/designer/',
  build: { outDir: '../ui/designer', emptyOutDir: true },
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
