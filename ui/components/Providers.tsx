'use client';

/**
 * Client-side providers stacked at the root.
 *
 * Lives in its own file (not inline in ``app/layout.tsx``) so the
 * layout can stay a server component; only this subtree carries the
 * ``'use client'`` boundary.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import type { ReactNode } from 'react';

import { AuthProvider } from '@/lib/auth/context';

export function Providers({ children }: { children: ReactNode }) {
  // One QueryClient instance per mount.  ``useState`` keeps it
  // stable across re-renders without recreating per render.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // 30s feels right for a config-heavy admin UI: stale
            // enough to feel snappy on tab switches, fresh enough
            // that you don't see a deleted row keep appearing.
            staleTime: 30 * 1000,
            retry: (failureCount, error) => {
              // Don't retry auth errors — the client already tried
              // the refresh dance once.
              if (
                error instanceof Error &&
                error.name === 'ApiError' &&
                'status' in error &&
                (error as unknown as { status: number }).status === 401
              ) {
                return false;
              }
              return failureCount < 2;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
