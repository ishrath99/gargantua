import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config.
 *
 * The UI runs against ``next start`` (the static-export-ready
 * production server) so the test surface mirrors what ships.  No
 * backend is required — every API call is stubbed via
 * ``page.route``; that keeps CI hermetic and lets us assert the
 * exact request payloads.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 30_000,

  use: {
    baseURL: 'http://localhost:3000',
    trace: 'retain-on-failure',
    // Tests stub network with page.route(); the backend URL the
    // UI builds against doesn't matter — only the path does — so
    // we use an obviously-fake host that would fail loudly if any
    // call escapes the stub.
    extraHTTPHeaders: {},
  },

  webServer: {
    // ``next start`` requires a prior ``next build``.  We run both
    // in one command so a fresh checkout works end-to-end.  In
    // dev, prefer ``pnpm dev`` in a separate terminal and run
    // ``pnpm test:e2e`` against it.
    command: 'pnpm build && pnpm start',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_BASE_URL: 'http://stub.test',
    },
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
