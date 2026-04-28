// Test config kept separate from ``vite.config.ts`` because vitest
// ships its own copy of vite for type compatibility — letting the
// two configs share types causes a version-mismatch error otherwise.
// The actual runtime is harmonised; only the types diverge.
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './vitest.setup.ts',
    css: false,
  },
});
