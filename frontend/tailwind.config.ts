import type { Config } from 'tailwindcss';

// Tailwind v3 config. ``content`` lists every file the JIT compiler
// scans for class names — keeping it tight (just src + index.html)
// stops the dev build from re-walking node_modules on every edit.
const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Single brand colour for now. Real palette will land with the
      // first design pass (PR #B / #C).
      colors: {
        brand: {
          50: '#f0f7ff',
          500: '#2563eb',
          600: '#1d4ed8',
          700: '#1e40af',
        },
      },
    },
  },
  plugins: [],
};

export default config;
