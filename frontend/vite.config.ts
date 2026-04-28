import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite config. Two non-default choices worth flagging:
//
// * ``server.host: true`` — bind 0.0.0.0 inside the docker dev container
//   so port 3000 on the host actually reaches the dev server. Without
//   this, vite defaults to localhost which is unreachable from outside
//   the container.
// * ``server.proxy`` for ``/api`` — forwards API calls to the backend
//   container. The browser hits the dev server on the same origin,
//   sidestepping CORS for local dev. Production frontend talks to the
//   API directly via the configured base URL.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://api:8000',
        changeOrigin: true,
      },
    },
  },
});
