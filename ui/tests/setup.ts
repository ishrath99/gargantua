import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

// Each test starts with a clean slate — no leaked tokens / fetch
// mocks between cases.
beforeEach(() => {
  if (typeof window !== 'undefined') {
    window.localStorage.clear();
  }
  vi.restoreAllMocks();
});

// React Testing Library's auto-cleanup only fires when ``globals: true``
// is set on Vitest.  Our config keeps globals off (per Vitest 1.x
// guidance), so we wire ``cleanup()`` ourselves to unmount React
// trees between cases.  Without this, DOM nodes from earlier tests
// leak into ``screen`` queries and "found multiple…" errors abound.
afterEach(() => {
  cleanup();
});
