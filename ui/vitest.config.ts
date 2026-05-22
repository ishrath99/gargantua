import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// ``@vitejs/plugin-react`` enables JSX/TSX parsing for the component
// tests added in PR 16.  The PR 15 modules (``lib/api/client.ts``,
// ``lib/auth/storage.ts``) didn't need it; they still pass with it on.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**', '.next/**', 'out/**'],
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, '.'),
    },
  },
});
