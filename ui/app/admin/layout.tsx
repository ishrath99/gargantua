import { type ReactNode } from 'react';

import { AdminSidebar } from '@/components/admin/AdminSidebar';
import { AdminTopBar } from '@/components/admin/AdminTopBar';
import { AuthGuard } from '@/components/RouteGuard';

export const metadata = {
  title: 'Admin — gargantua',
};

/**
 * Shared chrome for every ``/admin/...`` route.
 *
 * Gating: we wrap in :class:`AuthGuard` with ``requireScope='admin'``
 * so an unauthenticated visit bounces to ``/login`` and a logged-in
 * non-admin bounces to ``/``.  The guard renders a loading state
 * during the brief boot flicker while we resolve ``/auth/me`` — see
 * ``components/RouteGuard.tsx`` for the state machine.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard requireScope="admin">
      <div className="flex min-h-screen bg-neutral-50 dark:bg-neutral-950">
        <AdminSidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <AdminTopBar />
          <main className="flex-1 p-6">
            <div className="mx-auto w-full max-w-6xl">{children}</div>
          </main>
        </div>
      </div>
    </AuthGuard>
  );
}
